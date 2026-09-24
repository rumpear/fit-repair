"""Merge several recordings of one ride into a single activity.

Two phases, so that everything the files contain survives byte for byte:

1. splice: concatenate the raw messages of all files, leaving out what must
   exist only once (file header, session, activity) and the end-of-recording
   events of every file but the last. The result is already a valid FIT file,
   but its values are still per file: the distance restarts from zero, lap
   numbers repeat, the session describes only the last file.
2. fixup: rewrite those values in place with the same byte patches the
   cleaner uses.

The break between two files becomes a timer pause. Laps are kept as recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fit_repair import aggregate, geo
from fit_repair.model import Activity, Message, Patches, Track, to_semicircles
from fit_repair.reader import extract_track, parse_activity, utc_offset
from fit_repair.splice import splice

# Kept from the first file only.
ONCE = frozenset({"file_id", "file_creator", "sport"})
# Kept from the last file only and rewritten to describe the whole ride.
SUMMARY = frozenset({"session", "activity"})
# Record fields that count up from the start of a recording.
CUMULATIVE = ("distance", "accumulated_power", "total_cycles", "calories")

TIMER_STOPS = frozenset({"stop", "stop_all", "stop_disable", "stop_disable_all"})
RECORDING_ENDS = frozenset({"stop_all", "stop_disable_all"})
EVENT_TYPE_STOP = 1  # raw value of event_type "stop"


class MergeError(Exception):
    pass


@dataclass(frozen=True)
class MergeConfig:
    # Add the straight-line distance between the files to the distance stream.
    bridge_gap: bool = False
    # A longer break between two files is most likely a different ride.
    max_gap: float = 2 * 3600  # s
    # Merge even if the break is too long or the sport differs.
    force: bool = False
    # The gap is bridged only if it could be covered at this speed.
    max_speed: float = 100 / 3.6  # m/s


@dataclass
class Part:
    """One input file."""

    name: str
    start: float  # first record, POSIX seconds
    end: float  # last timestamp in the file
    distance: float | None  # session total_distance, m
    timer: float | None  # session total_timer_time, s
    laps: int


@dataclass
class Gap:
    """The break between two consecutive files."""

    seconds: float  # from the last record of one file to the first record of the next
    metres: float | None  # straight line between their nearest GPS fixes
    plausible: bool  # the straight line could be covered at max_speed
    bridged: float  # metres added to the distance stream


@dataclass
class Result:
    activity: Activity  # spliced file, before patching
    patches: Patches
    order: list[int]  # input indices in time order
    parts: list[Part]  # in time order
    gaps: list[Gap]
    session: dict[str, Any]  # merged session values
    unmerged: list[str]  # session fields left as in the last file although the files differ
    events_dropped: int
    events_converted: int
    warnings: list[str]
    utc_offset: float | None


def merge(files: list[Activity], cfg: MergeConfig = MergeConfig(), names: list[str] | None = None) -> Result:
    if len(files) < 2:
        raise MergeError("нужно хотя бы два файла")
    names = names or [f"файл {n}" for n in range(1, len(files) + 1)]
    for activity, name in zip(files, names):
        _check_file(activity, name)

    order = sorted(range(len(files)), key=lambda i: _start(files[i]))
    files = [files[i] for i in order]
    names = [names[i] for i in order]
    warnings = _check_sequence(files, names, cfg)

    tracks = [extract_track(a.by_name("record")) for a in files]
    gaps = [_gap(tracks[k], tracks[k + 1], cfg) for k in range(len(files) - 1)]

    kept, to_stop, dropped = _select(files)
    merged = parse_activity(splice(files, kept))
    origin = [(k, m) for k, messages in enumerate(kept) for m in messages]
    if len(origin) != len(merged.messages):
        raise RuntimeError(f"splice produced {len(merged.messages)} messages, expected {len(origin)}")

    patches = Patches()
    _shift_records(merged, origin, files, gaps, patches)
    for (_, source), msg in zip(origin, merged.messages):
        ref = msg.fields.get("event_type")
        if id(source) in to_stop and ref is not None:
            patches.set(merged.data, ref, EVENT_TYPE_STOP)
    _renumber_laps(merged, patches)

    sessions = [a.by_name("session")[0] for a in files]
    session = _merge_session(sessions, merged, gaps, patches)
    for act in merged.by_name("activity"):
        ref = act.fields.get("total_timer_time")
        if ref is not None and "total_timer_time" in session:
            patches.set(merged.data, ref, session["total_timer_time"])

    parts = [
        Part(
            name=name,
            start=_start(a),
            end=_end(a),
            distance=s.get("total_distance"),
            timer=s.get("total_timer_time"),
            laps=len(a.by_name("lap")),
        )
        for a, s, name in zip(files, sessions, names)
    ]
    return Result(
        activity=merged,
        patches=patches,
        order=order,
        parts=parts,
        gaps=gaps,
        session=session,
        unmerged=aggregate.unmerged(sessions),
        events_dropped=dropped,
        events_converted=len(to_stop),
        warnings=warnings,
        utc_offset=utc_offset(merged),
    )


def _check_file(activity: Activity, name: str) -> None:
    if len(activity.crc_ranges) != 1 or activity.crc_ranges[0][0] != 0:
        raise MergeError(f"{name}: несколько FIT-сегментов в одном файле пока не поддерживаются")
    sessions = activity.by_name("session")
    if len(sessions) != 1:
        raise MergeError(f"{name}: ожидается одна сессия, а в файле их {len(sessions)}")
    if not any(r.time() is not None for r in activity.by_name("record")):
        raise MergeError(f"{name}: в файле нет записей record")
    # A compressed timestamp is relative to the previous one, which may be in a dropped message.
    if any(activity.data[m.offset] & 0x80 for m in activity.messages):
        raise MergeError(f"{name}: сжатые метки времени пока не поддерживаются")


def _start(activity: Activity) -> float:
    return next(t for r in activity.by_name("record") if (t := r.time()) is not None)


def _end(activity: Activity) -> float:
    return max(t for m in activity.messages if (t := m.time()) is not None)


def _check_sequence(files: list[Activity], names: list[str], cfg: MergeConfig) -> list[str]:
    for k in range(len(files) - 1):
        end, start = _end(files[k]), _start(files[k + 1])
        if start < end:
            raise MergeError(f"{names[k]} и {names[k + 1]} пересекаются по времени, это не продолжение одной записи")
        if start - end > cfg.max_gap and not cfg.force:
            raise MergeError(
                f"между {names[k]} и {names[k + 1]} {(start - end) / 3600:.1f} ч, похоже на разные поездки "
                "(--force, чтобы склеить всё равно)"
            )

    first = files[0].by_name("session")[0]
    sport = (first.get("sport"), first.get("sub_sport"))
    for activity, name in zip(files[1:], names[1:]):
        session = activity.by_name("session")[0]
        other = (session.get("sport"), session.get("sub_sport"))
        if other != sport and not cfg.force:
            raise MergeError(
                f"{name}: другой вид спорта ({'/'.join(map(str, other))} вместо {'/'.join(map(str, sport))}); "
                "--force, чтобы склеить всё равно"
            )

    warnings = []
    if len({_device(a) for a in files}) > 1:
        warnings.append("файлы записаны разными устройствами")
    return warnings


def _device(activity: Activity) -> tuple:
    ids = activity.by_name("file_id")
    if not ids:
        return ()
    return tuple(repr(ids[0].get(k)) for k in ("manufacturer", "product", "serial_number"))


def _gap(before: Track, after: Track, cfg: MergeConfig) -> Gap:
    seconds = after.t[0] - before.t[-1]
    a = next((i for i in reversed(range(len(before))) if before.has_fix(i)), None)
    b = next((i for i in range(len(after)) if after.has_fix(i)), None)
    if a is None or b is None:
        return Gap(seconds, None, False, 0.0)
    metres = geo.distance(before.lat[a], before.lon[a], after.lat[b], after.lon[b])
    plausible = metres <= cfg.max_speed * max(after.t[b] - before.t[a], 1.0)
    return Gap(seconds, metres, plausible, metres if cfg.bridge_gap and plausible else 0.0)


def _select(files: list[Activity]) -> tuple[list[list[Message]], set[int], int]:
    """Messages to keep from each file, ids of events to turn into a timer stop, events dropped."""
    kept: list[list[Message]] = []
    to_stop: set[int] = set()
    dropped = 0
    last = len(files) - 1
    for k, activity in enumerate(files):
        messages = []
        running = False
        for m in activity.messages:
            if (m.name in ONCE and k > 0) or (m.name in SUMMARY and k < last):
                continue
            if m.name == "event" and k < last:
                event, kind = m.get("event"), m.get("event_type")
                if kind in RECORDING_ENDS:
                    # The end of a recording that another file continues. If the
                    # timer was still running, it becomes the start of the pause.
                    if event != "timer" or not running:
                        dropped += 1
                        continue
                    to_stop.add(id(m))
                    running = False
                elif event == "timer":
                    running = kind == "start" or (running and kind not in TIMER_STOPS)
            messages.append(m)
        kept.append(messages)
    return kept, to_stop, dropped


def _shift_records(
    merged: Activity,
    origin: list[tuple[int, Message]],
    files: list[Activity],
    gaps: list[Gap],
    patches: Patches,
) -> None:
    """Make cumulative record fields continue from where the previous file ended."""
    offsets: list[dict[str, float]] = []
    total = dict.fromkeys(CUMULATIVE, 0.0)
    for k, activity in enumerate(files):
        offsets.append(dict(total))
        records = activity.by_name("record")
        for name in CUMULATIVE:
            total[name] += next((r.get(name) for r in reversed(records) if r.get(name) is not None), 0.0)
        if k < len(gaps):
            total["distance"] += gaps[k].bridged

    for (k, _), msg in zip(origin, merged.messages):
        if msg.name != "record":
            continue
        for name, add in offsets[k].items():
            value, ref = msg.get(name), msg.fields.get(name)
            if add and value is not None and ref is not None:
                patches.set(merged.data, ref, value + add)


def _renumber_laps(merged: Activity, patches: Patches) -> None:
    laps = merged.by_name("lap")
    base = laps[0].get("message_index") if laps else None
    if base is None:
        return
    for n, lap in enumerate(laps):
        ref = lap.fields.get("message_index")
        if ref is not None:
            patches.set(merged.data, ref, base + n)


def _merge_session(sessions: list[Message], merged: Activity, gaps: list[Gap], patches: Patches) -> dict[str, Any]:
    values = aggregate.combine(sessions)
    if "total_distance" in values:
        values["total_distance"] += sum(g.bridged for g in gaps)
    start, end = sessions[0].time("start_time"), sessions[-1].time("timestamp")
    if start is not None and end is not None:
        values["total_elapsed_time"] = end - start
    timer, distance = values.get("total_timer_time"), values.get("total_distance")
    if timer and distance is not None:
        values["avg_speed"] = values["enhanced_avg_speed"] = distance / timer
    values["num_laps"] = len(merged.by_name("lap"))
    # From the records, not the per-file boxes: head units write (0, 0) or spoofed corners there.
    values.update(_bounding_box(extract_track(merged.by_name("record"))))

    # The spliced file keeps only the last session; it becomes the merged one.
    template = merged.by_name("session")[0]
    for name, value in values.items():
        ref = template.fields.get(name)
        if ref is not None:
            patches.set(merged.data, ref, value)
    return values


def _bounding_box(track: Track) -> dict[str, int]:
    fixes = [i for i in range(len(track)) if track.has_fix(i)]
    if not fixes:
        return {}
    lats = [track.lat[i] for i in fixes]
    lons = [track.lon[i] for i in fixes]
    return {
        "nec_lat": to_semicircles(max(lats)),
        "nec_long": to_semicircles(max(lons)),
        "swc_lat": to_semicircles(min(lats)),
        "swc_long": to_semicircles(min(lons)),
    }
