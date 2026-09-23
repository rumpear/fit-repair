"""Decode a FIT file with fitdecode and remember where every raw field lives."""

from __future__ import annotations

import io
from pathlib import Path

import fitdecode

from fit_cleaner.model import (
    INVALID_VALUES,
    Activity,
    FieldRef,
    Message,
    Track,
    to_degrees,
)


def read_activity(path: str | Path) -> Activity:
    return parse_activity(Path(path).read_bytes())


def parse_activity(data: bytes) -> Activity:
    messages: list[Message] = []
    definitions: list[tuple[int, int]] = []
    crc_ranges: list[tuple[int, int]] = []
    segment_start = 0

    with fitdecode.FitReader(
        io.BytesIO(data),
        keep_raw_chunks=True,
        check_crc=fitdecode.CrcCheck.WARN,
    ) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitHeader):
                segment_start = frame.chunk.offset
            elif isinstance(frame, fitdecode.FitCRC):
                crc_ranges.append((segment_start, frame.chunk.offset))
            elif isinstance(frame, fitdecode.FitDefinitionMessage):
                definitions.append((frame.chunk.offset, len(frame.chunk.bytes)))
            elif isinstance(frame, fitdecode.FitDataMessage):
                messages.append(_to_message(frame))

    return Activity(data=data, messages=messages, crc_ranges=crc_ranges, definitions=definitions)


def _to_message(frame: fitdecode.FitDataMessage) -> Message:
    values: dict = {}
    for f in frame.fields:
        # Raw fields come before the ones expanded from components.
        values.setdefault(f.name, f.value)

    refs: dict[str, FieldRef] = {}
    endian = frame.def_mesg.endian
    offset = frame.chunk.offset + 1  # skip the record header byte
    for fd in frame.def_mesg.field_defs:
        ref = _field_ref(fd, offset, endian)
        if ref is not None:
            refs[fd.name] = ref
        offset += fd.size
    return Message(
        name=frame.name,
        values=values,
        fields=refs,
        offset=frame.chunk.offset,
        size=len(frame.chunk.bytes),
    )


def _field_ref(fd, offset: int, endian: str) -> FieldRef | None:
    profile = fd.field
    base = fd.base_type
    if profile is None or base.name not in INVALID_VALUES:
        return None
    if fd.size % base.size:
        return None
    scale, add = profile.scale, profile.offset
    if isinstance(scale, (tuple, list)) or isinstance(add, (tuple, list)):
        return None
    # A subfield only renames the value (avg_cadence -> avg_running_cadence);
    # if it also rescaled it, the encoding would depend on the reference field.
    if any((sub.scale, sub.offset) != (scale, add) for sub in profile.subfields or ()):
        return None
    return FieldRef(
        offset=offset,
        size=fd.size,
        fmt=endian + base.fmt,
        base_type=base.name,
        scale=float(scale or 1),
        add=float(add or 0),
        count=fd.size // base.size,
    )


def extract_track(records: list[Message]) -> Track:
    track = Track(t=[], lat=[], lon=[], dist=[], speed=[])
    for rec in records:
        t = rec.time()
        track.t.append(t if t is not None else (track.t[-1] if track.t else 0.0))
        track.lat.append(to_degrees(rec.get("position_lat")))
        track.lon.append(to_degrees(rec.get("position_long")))
        track.dist.append(rec.get("distance"))
        speed = rec.get("enhanced_speed")
        track.speed.append(speed if speed is not None else rec.get("speed"))
    return track


def utc_offset(activity: Activity) -> float | None:
    """Seconds between device local time and UTC, from activity.local_timestamp."""
    for act in activity.by_name("activity"):
        ts, local = act.time("timestamp"), act.time("local_timestamp")
        if ts is not None and local is not None:
            return local - ts
    return None


def timer_pauses(events: list[Message]) -> list[tuple[float, float]]:
    """Intervals during which the device timer was stopped (auto/manual pause)."""
    pauses: list[tuple[float, float]] = []
    paused_at: float | None = None
    for ev in events:
        if ev.get("event") != "timer":
            continue
        kind, ts = ev.get("event_type"), ev.time()
        if ts is None:
            continue
        if kind in ("stop", "stop_all", "stop_disable", "stop_disable_all"):
            if paused_at is None:
                paused_at = ts
        elif kind == "start" and paused_at is not None:
            pauses.append((paused_at, ts))
            paused_at = None
    return pauses
