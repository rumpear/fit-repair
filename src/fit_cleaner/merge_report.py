"""Human-readable summary of a merge."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fit_cleaner.merge import Gap, Result
from fit_cleaner.report import fmt_km, fmt_offset


def format_merge_report(result: Result) -> str:
    offset = result.utc_offset
    tz = timezone(timedelta(seconds=offset)) if offset is not None else timezone.utc

    def hms(t: float) -> str:
        return datetime.fromtimestamp(t, tz).strftime("%H:%M:%S")

    tz_name = f"UTC{fmt_offset(offset)}" if offset is not None else "UTC"
    lines = [f"Recordings in time order ({tz_name})"]
    for n, part in enumerate(result.parts):
        distance = fmt_km(part.distance) if part.distance is not None else "?"
        timer = _duration(part.timer) if part.timer is not None else "?"
        lines.append(
            f"  {n + 1}. {part.name}: {hms(part.start)}-{hms(part.end)}, {distance}, "
            f"timer {timer}, laps {part.laps}"
        )
        if n < len(result.gaps):
            lines.append(f"     {_gap(result.gaps[n])}")

    s = result.session
    lines.append("")
    lines.append("Result")
    if s.get("total_distance") is not None:
        lines.append(f"  distance: {fmt_km(s['total_distance'])}")
    if s.get("total_timer_time") is not None and s.get("total_elapsed_time") is not None:
        lines.append(
            f"  timer: {_duration(s['total_timer_time'])}, "
            f"with pauses: {_duration(s['total_elapsed_time'])}"
        )
    lines.append(f"  laps: {s['num_laps']}")
    if s.get("avg_heart_rate") is not None:
        lines.append(f"  heart rate: avg {s['avg_heart_rate']:.0f}, max {s.get('max_heart_rate')}")
    lines.append(
        f"  recording end in intermediate files: events dropped {result.events_dropped}, "
        f"turned into a pause {result.events_converted}"
    )
    if result.unmerged:
        lines.append(f"  not recomputed, taken from the last file: {', '.join(result.unmerged)}")
    for w in result.warnings:
        lines.append(f"  ! {w}")

    lines.append("")
    lines.append(f"Fields changed in file: {len(result.patches)}")
    return "\n".join(lines)


def _gap(gap: Gap) -> str:
    text = f"gap {_duration(gap.seconds)}"
    if gap.metres is not None and gap.plausible:
        text += f", straight line {gap.metres:.0f} m"
    elif gap.metres is not None:
        text += ", coordinates at the join do not match"
    if gap.bridged:
        return text + f": added to distance {gap.bridged:.0f} m"
    return text + ": pause, no distance added"


def _duration(seconds: float) -> str:
    total = round(seconds)
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"
