"""Human-readable summary of what was changed and why."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fit_cleaner.model import to_degrees
from fit_cleaner.pipeline import Result


def format_report(result: Result) -> str:
    track, sp, pos = result.track, result.speed, result.position
    offset = result.utc_offset
    tz = timezone(timedelta(seconds=offset)) if offset is not None else timezone.utc

    def hms(i: int) -> str:
        return datetime.fromtimestamp(track.t[i], tz).strftime("%H:%M:%S")

    lines: list[str] = []
    if not len(track):
        return "В файле нет записей record - менять нечего."

    tz_name = f"UTC{fmt_offset(offset)}" if offset is not None else "UTC"
    lines.append(f"Записей: {len(track)}, {hms(0)}-{hms(len(track) - 1)} ({tz_name})")

    lines.append("")
    lines.append("GPS")
    interpolated, stale = set(pos.interpolated), set(pos.stale)
    if pos.rejected:
        removed = len(pos.rejected) - len(interpolated)
        lines.append(f"  удалено координат: {removed}, заменено интерполяцией: {len(interpolated)}")
        for a, b, n in _groups(pos.rejected):
            group = [i for i in pos.rejected if a <= i <= b]
            frozen = sum(1 for i in group if i in stale)
            reasons = []
            if frozen:
                reasons.append(f"замёрзшая позиция: {frozen}")
            if n - frozen:
                reasons.append(f"невозможный прыжок: {n - frozen}")
            if all(i in interpolated for i in group):
                reasons.append("интерполированы")
            lines.append(f"    {hms(a)}-{hms(b)}  записей: {n} ({', '.join(reasons)})")
    else:
        lines.append("  подозрительных координат нет")
    lines.append(f"  сверка с датчиком дистанции: {'да' if pos.sensor_check else 'нет'}")
    for w in pos.warnings:
        lines.append(f"  ! {w}")

    lines.append("")
    lines.append("Скорость / дистанция")
    if sp.windows:
        for a, b in sp.windows:
            peak = max((v for v in track.speed[a : b + 1] if v is not None), default=0.0)
            fixed = max((v for v in sp.speed[a : b + 1] if v is not None), default=0.0)
            lines.append(
                f"  всплеск {hms(a)}-{hms(b)}: записей {b - a + 1}, "
                f"{_kmh(peak)} -> {_kmh(fixed)}"
            )
    else:
        lines.append("  всплесков скорости нет")
    last_orig = next((d for d in reversed(track.dist) if d is not None), None)
    last_new = next((d for d in reversed(sp.dist) if d is not None), None)
    if last_orig is not None and last_new is not None:
        diff = last_new - last_orig
        if abs(diff) >= 0.01:
            lines.append(f"  дистанция: {fmt_km(last_orig)} -> {fmt_km(last_new)} ({diff / 1000:+.3f} км)")
        else:
            lines.append(f"  дистанция: {fmt_km(last_orig)} (без изменений)")

    lines.append("")
    lines.append("Итоги кругов и сессии")
    if result.changes:
        for c in result.changes:
            lines.append(f"  {c.message}: {c.field} {_fmt(c.field, c.old)} -> {_fmt(c.field, c.new)}")
    else:
        lines.append("  без изменений")

    lines.append("")
    lines.append(f"Изменено полей в файле: {len(result.patches)}")
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


def _kmh(v: float) -> str:
    return f"{v * 3.6:.1f} км/ч"


def fmt_km(m: float) -> str:
    return f"{m / 1000:.3f} км"


def _fmt(field: str, value: Any) -> str:
    if value is None:
        return "нет"
    if field.endswith("_lat") or field.endswith("_long"):
        return f"{to_degrees(value):.5f}"
    if field.endswith("speed"):
        return _kmh(value)
    if field.endswith("distance"):
        return fmt_km(value)
    return str(value)
