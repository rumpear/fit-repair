"""Combine the session summaries of several recordings into one.

Each field has one rule. Averages are weighted by timer time, which is what a
head unit averages over. Fields that depend on the merged data as a whole
(elapsed time, average speed, lap count, bounding box) are computed in
merge.py instead. A field without a rule keeps the last file's value, and
`unmerged` lists those whose values differ between files.
"""

from __future__ import annotations

from typing import Any

from fit_repair.model import Message

SUM = frozenset({
    "total_timer_time", "total_moving_time", "total_distance", "total_cycles",
    "total_calories", "total_fat_calories", "total_ascent", "total_descent", "total_work",
    "time_in_hr_zone", "time_in_speed_zone", "time_in_cadence_zone", "time_in_power_zone",
    "time_standing", "stand_count", "jump_count", "total_grit",
})
MAX = frozenset({
    "max_speed", "enhanced_max_speed", "max_heart_rate", "max_cadence", "max_power",
    "max_altitude", "enhanced_max_altitude", "max_temperature", "max_pos_grade",
    "max_pos_vertical_speed",
})
MIN = frozenset({
    "min_heart_rate", "min_altitude", "enhanced_min_altitude", "min_temperature",
    "max_neg_grade", "max_neg_vertical_speed",  # the most negative one
})
WEIGHTED = frozenset({
    "avg_heart_rate", "avg_cadence", "avg_power", "avg_temperature", "avg_altitude",
    "enhanced_avg_altitude", "avg_grade", "avg_pos_grade", "avg_neg_grade",
    "avg_pos_vertical_speed", "avg_neg_vertical_speed",
})
# Taken from the earliest file that has a value.
FIRST = frozenset({"start_time", "start_position_lat", "start_position_long", "first_lap_index"})
# Computed from the merged data by merge.py.
DERIVED = frozenset({
    "total_elapsed_time", "avg_speed", "enhanced_avg_speed", "num_laps",
    "nec_lat", "nec_long", "swc_lat", "swc_long",
})
# Describe the end of the ride or the message itself: the last file's value is right.
LAST = frozenset({
    "timestamp", "end_position_lat", "end_position_long", "event", "event_type",
    "message_index", "sport", "sub_sport", "sport_index", "trigger",
})
_HANDLED = SUM | MAX | MIN | WEIGHTED | FIRST | DERIVED | LAST


def combine(sessions: list[Message]) -> dict[str, Any]:
    """Merged values of every field that has a rule, sessions in time order."""
    weights = [s.get("total_timer_time") or s.get("total_elapsed_time") or 0.0 for s in sessions]
    out: dict[str, Any] = {}
    for name in _present(sessions):
        values = [s.get(name) for s in sessions]
        present = [v for v in values if v is not None]
        if name in SUM:
            out[name] = _sum(present)
        elif name in MAX:
            out[name] = max(present)
        elif name in MIN:
            out[name] = min(present)
        elif name in WEIGHTED:
            out[name] = _weighted(values, weights)
        elif name in FIRST:
            out[name] = present[0]
    return {k: v for k, v in out.items() if v is not None}


def unmerged(sessions: list[Message]) -> list[str]:
    """Fields without a rule whose values differ between the files."""
    out = []
    for name in sorted(_present(sessions) - _HANDLED):
        if len({repr(s.get(name)) for s in sessions}) > 1:
            out.append(name)
    return out


def _present(sessions: list[Message]) -> set[str]:
    return {k for s in sessions for k, v in s.values.items() if v is not None}


def _sum(values: list[Any]) -> Any:
    if not isinstance(values[0], (tuple, list)):
        return sum(values)
    if len({len(v) for v in values}) > 1:
        return None  # arrays of different length (e.g. zone count): nothing sensible to add up
    return tuple(sum(x or 0 for x in column) for column in zip(*values))


def _weighted(values: list[Any], weights: list[float]) -> float | None:
    pairs = [(v, w) for v, w in zip(values, weights) if v is not None]
    total = sum(w for _, w in pairs)
    if total > 0:
        return sum(v * w for v, w in pairs) / total
    return sum(v for v, _ in pairs) / len(pairs) if pairs else None
