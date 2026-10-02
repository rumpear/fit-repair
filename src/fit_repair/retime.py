"""Move the whole activity in time, or drop all of its coordinates.

Both are for rides where the GPS was spoofed from start to finish. There is
no real fix to keep then, and since head units set their clock from GPS, the
date can be wrong as well. Every absolute time is moved by the same amount,
so laps, pauses and the timer stay consistent with each other.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fit_repair.model import Activity, Patches
from fit_repair.reader import utc_offset


def start_time(activity: Activity) -> float | None:
    """Start of the activity as POSIX seconds: session start, else the first record."""
    for session in activity.by_name("session"):
        if (t := session.time("start_time")) is not None:
            return t
    return next((t for r in activity.by_name("record") if (t := r.time()) is not None), None)


def resolve_shift(activity: Activity, start: datetime) -> int:
    """Seconds to add to every time so that the activity starts at `start`.

    A start without a UTC offset is taken in the device's local time.
    """
    if any(activity.data[m.offset] & 0x80 for m in activity.messages):
        raise ValueError("compressed timestamps are not supported")
    if any("timestamp_16" in m.fields for m in activity.messages):
        raise ValueError("16-bit timestamps are not supported")
    old = start_time(activity)
    if old is None:
        raise ValueError("the file has no start time")
    if start.tzinfo is None:
        offset = utc_offset(activity)
        if offset is None:
            raise ValueError("the device time zone is unknown, give the offset, e.g. 2026-09-27T15:32:20+03:00")
        start = start.replace(tzinfo=timezone(timedelta(seconds=round(offset))))
    return round(start.timestamp() - old)


def shift_times(activity: Activity, shift: int, patches: Patches) -> int:
    """Add `shift` seconds to every absolute time; return the number of fields changed.

    Times below 0x10000000 are relative to device power-on and decode as plain
    integers, so they are left alone.
    """
    changed = 0
    for msg in activity.messages:
        for name, ref in msg.fields.items():
            value = msg.get(name)
            if ref.kind == "time" and isinstance(value, datetime):
                changed += patches.set(activity.data, ref, value + timedelta(seconds=shift))
    return changed


def strip_positions(activity: Activity, patches: Patches) -> int:
    """Clear every coordinate in every message; return the number of fields changed."""
    changed = 0
    for msg in activity.messages:
        for ref in msg.fields.values():
            if ref.kind == "position":
                changed += patches.set(activity.data, ref, None)
    return changed
