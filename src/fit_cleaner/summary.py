"""Recompute lap/session fields that depend on repaired records.

Only fields derived from data we changed are touched: distance, average and
max speed, start/end positions and the bounding box. Heart rate, timer,
calories, ascent etc. stay exactly as the device wrote them. Lap boundaries
are kept even if an auto-lap was triggered by bogus distance.
"""

from __future__ import annotations

import bisect
from typing import Any

from fit_cleaner import geo
from fit_cleaner.model import Message, Track, to_degrees, to_semicircles
from fit_cleaner.position import PositionFix
from fit_cleaner.speed import SpeedFix

MAX_SPEED_SLACK = 0.5  # m/s: a stored max this far above the records is bogus
POSITION_MATCH = 200.0  # m: a stored position this close to a kept fix is fine
POSITION_TIME_SLACK = 60.0  # s

Update = tuple[Message, str, Any]


def summary_updates(messages: list[Message], track: Track, sp: SpeedFix, pos: PositionFix) -> list[Update]:
    updates: list[Update] = []
    rejected = sorted(pos.rejected)
    for msg in messages:
        t0, t1 = msg.time("start_time"), msg.time("timestamp")
        if t0 is None or t1 is None:
            continue
        lo, hi = bisect.bisect_left(track.t, t0), bisect.bisect_right(track.t, t1)
        updates += _distance_and_speed(msg, track, sp, lo, hi)
        updates += _positions(msg, track, pos, rejected, t0, t1, lo, hi)
    return updates


def _distance_and_speed(msg: Message, track: Track, sp: SpeedFix, lo: int, hi: int) -> list[Update]:
    out: list[Update] = []
    total = msg.get("total_distance")
    if total is not None:
        delta = _dist_delta(track.dist, sp.dist, hi - 1) - _dist_delta(track.dist, sp.dist, lo - 1)
        if abs(delta) >= 0.01:
            new_total = max(total + delta, 0.0)
            out.append((msg, "total_distance", new_total))
            for name in ("avg_speed", "enhanced_avg_speed"):
                avg = msg.get(name)
                if avg is not None and total > 0:
                    out.append((msg, name, avg * new_total / total))

    speeds = [v for v in sp.speed[lo:hi] if v is not None]
    if speeds:
        vmax = max(speeds)
        for name in ("max_speed", "enhanced_max_speed"):
            old = msg.get(name)
            if old is not None and old > vmax + MAX_SPEED_SLACK:
                out.append((msg, name, vmax))
    return out


def _dist_delta(orig: list[float | None], new: list[float | None], i: int) -> float:
    """Correction applied to the cumulative distance at or before record i."""
    while i >= 0 and orig[i] is None:
        i -= 1
    return 0.0 if i < 0 else new[i] - orig[i]


def _positions(
    msg: Message,
    track: Track,
    pos: PositionFix,
    rejected: list[int],
    t0: float,
    t1: float,
    lo: int,
    hi: int,
) -> list[Update]:
    wlo = bisect.bisect_left(track.t, t0 - POSITION_TIME_SLACK)
    whi = bisect.bisect_right(track.t, t1 + POSITION_TIME_SLACK)
    if bisect.bisect_left(rejected, wlo) == bisect.bisect_left(rejected, whi):
        return []  # nothing was removed around this message

    around = [i for i in range(wlo, whi) if pos.lat[i] is not None]
    inside = [i for i in range(lo, hi) if pos.lat[i] is not None]
    out: list[Update] = []

    for prefix, is_start in (("start_position", True), ("end_position", False)):
        lat, lon = to_degrees(msg.get(prefix + "_lat")), to_degrees(msg.get(prefix + "_long"))
        if lat is None or lon is None:
            continue
        if any(geo.distance(lat, lon, pos.lat[i], pos.lon[i]) <= POSITION_MATCH for i in around):
            continue
        if is_start:
            cand = next((i for i in inside if track.t[i] <= t0 + POSITION_TIME_SLACK), None)
        else:
            cand = next((i for i in reversed(inside) if track.t[i] >= t1 - POSITION_TIME_SLACK), None)
        new_lat = to_semicircles(pos.lat[cand]) if cand is not None else None
        new_lon = to_semicircles(pos.lon[cand]) if cand is not None else None
        out += [(msg, prefix + "_lat", new_lat), (msg, prefix + "_long", new_lon)]

    if msg.get("nec_lat") is not None:
        lats = [pos.lat[i] for i in inside]
        lons = [pos.lon[i] for i in inside]
        box = {
            "nec_lat": max(lats, default=None),
            "nec_long": max(lons, default=None),
            "swc_lat": min(lats, default=None),
            "swc_long": min(lons, default=None),
        }
        out += [(msg, name, to_semicircles(value)) for name, value in box.items()]
    return out
