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
    lines = [f"Записи по времени ({tz_name})"]
    for n, part in enumerate(result.parts):
        distance = fmt_km(part.distance) if part.distance is not None else "?"
        timer = _duration(part.timer) if part.timer is not None else "?"
        lines.append(
            f"  {n + 1}. {part.name}: {hms(part.start)}-{hms(part.end)}, {distance}, "
            f"таймер {timer}, кругов {part.laps}"
        )
        if n < len(result.gaps):
            lines.append(f"     {_gap(result.gaps[n])}")

    s = result.session
    lines.append("")
    lines.append("Итог")
    if s.get("total_distance") is not None:
        lines.append(f"  дистанция: {fmt_km(s['total_distance'])}")
    if s.get("total_timer_time") is not None and s.get("total_elapsed_time") is not None:
        lines.append(
            f"  таймер: {_duration(s['total_timer_time'])}, "
            f"с паузами: {_duration(s['total_elapsed_time'])}"
        )
    lines.append(f"  кругов: {s['num_laps']}")
    if s.get("avg_heart_rate") is not None:
        lines.append(f"  пульс: средний {s['avg_heart_rate']:.0f}, максимальный {s.get('max_heart_rate')}")
    lines.append(
        f"  конец записи в промежуточных файлах: событий убрано {result.events_dropped}, "
        f"превращено в паузу {result.events_converted}"
    )
    if result.unmerged:
        lines.append(f"  не пересчитано, взято из последнего файла: {', '.join(result.unmerged)}")
    for w in result.warnings:
        lines.append(f"  ! {w}")

    lines.append("")
    lines.append(f"Изменено полей в файле: {len(result.patches)}")
    return "\n".join(lines)


def _gap(gap: Gap) -> str:
    text = f"разрыв {_duration(gap.seconds)}"
    if gap.metres is not None and gap.plausible:
        text += f", по прямой {gap.metres:.0f} м"
    elif gap.metres is not None:
        text += ", координаты на стыке не сходятся"
    if gap.bridged:
        return text + f": добавлено к дистанции {gap.bridged:.0f} м"
    return text + ": пауза, дистанция не добавлена"


def _duration(seconds: float) -> str:
    total = round(seconds)
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"
