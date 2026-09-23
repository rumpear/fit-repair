"""Glue: decoded activity -> repaired values -> byte patches."""

from __future__ import annotations

from dataclasses import dataclass

from fit_cleaner.model import Activity, Change, Config, Patches, Track, to_semicircles
from fit_cleaner.position import PositionFix, repair_positions
from fit_cleaner.reader import extract_track, timer_pauses
from fit_cleaner.speed import SpeedFix, repair_speed_distance
from fit_cleaner.summary import summary_updates


@dataclass
class Result:
    track: Track
    speed: SpeedFix
    position: PositionFix
    changes: list[Change]
    patches: Patches
    utc_offset: float | None  # seconds, from activity.local_timestamp


def clean(activity: Activity, cfg: Config) -> Result:
    records = activity.by_name("record")
    track = extract_track(records)
    pauses = timer_pauses(activity.by_name("event"))

    sp = repair_speed_distance(track, pauses, cfg)
    pos = repair_positions(track, sp.dist, cfg)

    patches = Patches()
    data = activity.data
    for i, rec in enumerate(records):
        if sp.dist[i] != track.dist[i] and "distance" in rec.fields:
            patches.set(data, rec.fields["distance"], sp.dist[i])
        if sp.speed[i] != track.speed[i]:
            for name in ("speed", "enhanced_speed"):
                if name in rec.fields:
                    patches.set(data, rec.fields[name], sp.speed[i])
        if (pos.lat[i], pos.lon[i]) != (track.lat[i], track.lon[i]):
            for name, value in (("position_lat", pos.lat[i]), ("position_long", pos.lon[i])):
                if name in rec.fields:
                    patches.set(data, rec.fields[name], to_semicircles(value))

    labels = {}
    for n, lap in enumerate(activity.by_name("lap"), 1):
        labels[id(lap)] = f"lap {n}"
    sessions = activity.by_name("session")
    for n, session in enumerate(sessions, 1):
        labels[id(session)] = "session" if len(sessions) == 1 else f"session {n}"

    changes: list[Change] = []
    summaries = activity.by_name("lap") + sessions
    for msg, name, value in summary_updates(summaries, track, sp, pos):
        ref = msg.fields.get(name)
        if ref is not None and patches.set(data, ref, value):
            changes.append(Change(labels[id(msg)], name, msg.get(name), value))

    return Result(
        track=track,
        speed=sp,
        position=pos,
        changes=changes,
        patches=patches,
        utc_offset=_utc_offset(activity),
    )


def _utc_offset(activity: Activity) -> float | None:
    for act in activity.by_name("activity"):
        ts, local = act.time("timestamp"), act.time("local_timestamp")
        if ts is not None and local is not None:
            return local - ts
    return None
