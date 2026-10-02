"""Human-readable summary of what was changed and why."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fit_repair.model import to_degrees
from fit_repair.pipeline import Result


def format_report(result: Result) -> str:
    track, sp, pos = result.track, result.speed, result.position
    offset = result.utc_offset
    tz = timezone(timedelta(seconds=offset)) if offset is not None else timezone.utc

    def hms(i: int) -> str:
        return datetime.fromtimestamp(track.t[i] + result.time_shift, tz).strftime("%H:%M:%S")

    def stamp(t: float) -> str:
        return datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d %H:%M:%S")

    lines: list[str] = []
    if not len(track):
        return "No record messages in the file - nothing to change."

    tz_name = f"UTC{fmt_offset(offset)}" if offset is not None else "UTC"
    day = datetime.fromtimestamp(track.t[0] + result.time_shift, tz).strftime("%Y-%m-%d")
    lines.append(f"Records: {len(track)}, {day} {hms(0)}-{hms(len(track) - 1)} ({tz_name})")

    if result.time_shift and result.start is not None:
        lines.append("")
        lines.append("Time")
        lines.append(
            f"  start {stamp(result.start)} -> {stamp(result.start + result.time_shift)} "
            f"(shifted by {fmt_shift(result.time_shift)})"
        )

    lines.append("")
    lines.append("GPS")
    interpolated, stale = set(pos.interpolated), set(pos.stale)
    if result.stripped is not None:
        lines.append(f"  all coordinates removed (--strip-gps): {result.stripped} fields")
    elif pos.rejected:
        removed = len(pos.rejected) - len(interpolated)
        lines.append(f"  coordinates removed: {removed}, interpolated: {len(interpolated)}")
        for a, b, n in _groups(pos.rejected):
            group = [i for i in pos.rejected if a <= i <= b]
            frozen = sum(1 for i in group if i in stale)
            reasons = []
            if frozen:
                reasons.append(f"frozen position: {frozen}")
            if n - frozen:
                reasons.append(f"impossible jump: {n - frozen}")
            if all(i in interpolated for i in group):
                reasons.append("interpolated")
            lines.append(f"    {hms(a)}-{hms(b)}  records: {n} ({', '.join(reasons)})")
    else:
        lines.append("  no suspicious coordinates")
    if result.stripped is None:
        lines.append(f"  distance sensor cross-check: {'yes' if pos.sensor_check else 'no'}")
    for w in pos.warnings:
        lines.append(f"  ! {w}")

    lines.append("")
    lines.append("Speed / distance")
    if sp.windows:
        for a, b in sp.windows:
            peak = max((v for v in track.speed[a : b + 1] if v is not None), default=0.0)
            fixed = max((v for v in sp.speed[a : b + 1] if v is not None), default=0.0)
            lines.append(
                f"  spike {hms(a)}-{hms(b)}: {b - a + 1} records, "
                f"{_kmh(peak)} -> {_kmh(fixed)}"
            )
    else:
        lines.append("  no speed spikes")
    last_orig = next((d for d in reversed(track.dist) if d is not None), None)
    last_new = next((d for d in reversed(sp.dist) if d is not None), None)
    if last_orig is not None and last_new is not None:
        diff = last_new - last_orig
        if abs(diff) >= 0.01:
            lines.append(f"  distance: {fmt_km(last_orig)} -> {fmt_km(last_new)} ({diff / 1000:+.3f} km)")
        else:
            lines.append(f"  distance: {fmt_km(last_orig)} (unchanged)")

    lines.append("")
    lines.append("Lap and session summaries")
    if result.changes:
        for c in result.changes:
            lines.append(f"  {c.message}: {c.field} {_fmt(c.field, c.old)} -> {_fmt(c.field, c.new)}")
    else:
        lines.append("  unchanged")

    lines.append("")
    lines.append(f"Fields changed in file: {len(result.patches)}")
    return "\n".join(lines)


def _groups(indices: list[int]) -> list[tuple[int, int, int]]:
    """Group sorted record indices into runs (small holes are merged)."""
    groups: list[tuple[int, int, int]] = []
    for i in indices:
        if groups and i - groups[-1][1] <= 5:
            a, _, n = groups[-1]
            groups[-1] = (a, i, n + 1)
        else:
            groups.append((i, i, 1))
    return groups


def fmt_offset(seconds: float) -> str:
    sign = "+" if seconds >= 0 else "-"
    minutes = round(abs(seconds) / 60)
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def fmt_shift(seconds: int) -> str:
    sign = "+" if seconds >= 0 else "-"
    days, rest = divmod(abs(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    clock = f"{hours:02d}:{rest // 60:02d}:{rest % 60:02d}"
    return f"{sign}{days} d {clock}" if days else f"{sign}{clock}"


def _kmh(v: float) -> str:
    return f"{v * 3.6:.1f} km/h"


def fmt_km(m: float) -> str:
    return f"{m / 1000:.3f} km"


def _fmt(field: str, value: Any) -> str:
    if value is None:
        return "none"
    if field.endswith("_lat") or field.endswith("_long"):
        return f"{to_degrees(value):.5f}"
    if field.endswith("speed"):
        return _kmh(value)
    if field.endswith("distance"):
        return fmt_km(value)
    return str(value)
