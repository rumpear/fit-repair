"""Detect and repair impossible speed and distance samples.

Strava derives total distance, average and max speed from the `distance`
stream of the file, so the cumulative distance must be fixed, not only the
`speed` field. Distance is rebuilt step by step: plausible increments are kept
as recorded (wheel sensor quantisation included), implausible ones are
replaced by the repaired speed integrated over the time the timer was running.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from fit_repair.model import Config, Track


@dataclass
class SpeedFix:
    speed: list[float | None]
    dist: list[float | None]
    windows: list[tuple[int, int]]  # inclusive record ranges whose speed was replaced
    fixed_steps: list[int]  # records whose distance increment was recomputed


def repair_speed_distance(track: Track, pauses: list[tuple[float, float]], cfg: Config) -> SpeedFix:
    bad = [s is not None and s > cfg.max_speed for s in track.speed]
    _grow_over_ramps(bad, track, cfg)
    windows = _runs(bad)

    speed = list(track.speed)
    for a, b in windows:
        _interpolate(speed, track.t, a, b)

    pauses = sorted(pauses)
    starts = [p[0] for p in pauses]
    dist: list[float | None] = list(track.dist)
    fixed: list[int] = []
    prev: int | None = None
    for i, d in enumerate(track.dist):
        if d is None:
            continue
        if prev is None:
            prev = i
            continue
        step = d - track.dist[prev]
        moving = _moving_time(track.t[prev], track.t[i], pauses, starts)
        if bad[i] or bad[prev] or step < 0 or step > cfg.max_speed * moving:
            step = _integrate(speed[prev], speed[i], moving, step, cfg)
            fixed.append(i)
        dist[i] = dist[prev] + step
        prev = i

    return SpeedFix(speed=speed, dist=dist, windows=windows, fixed_steps=fixed)


def _grow_over_ramps(bad: list[bool], track: Track, cfg: Config) -> None:
    """Extend detected spikes over neighbours that are still implausibly elevated."""
    v, t = track.speed, track.t
    todo = [i for i, b in enumerate(bad) if b]
    while todo:
        i = todo.pop()
        for j, k in ((i - 1, i - 2), (i + 1, i + 2)):
            if not (0 <= j < len(bad) and 0 <= k < len(bad)) or bad[j]:
                continue
            if v[j] is None or v[k] is None:
                continue
            dt = abs(t[j] - t[k]) or 1.0
            if (v[j] - v[k]) / dt > cfg.max_accel:
                bad[j] = True
                todo.append(j)


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    runs, start = [], None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(flags) - 1))
    return runs


def _interpolate(speed: list[float | None], t: list[float], a: int, b: int) -> None:
    left = speed[a - 1] if a > 0 else None
    right = speed[b + 1] if b + 1 < len(speed) else None
    if left is None and right is None:
        left = right = 0.0
    elif left is None:
        left = right
    elif right is None:
        right = left
    t0 = t[a - 1] if a > 0 else t[a]
    t1 = t[b + 1] if b + 1 < len(t) else t[b]
    for i in range(a, b + 1):
        frac = (t[i] - t0) / (t1 - t0) if t1 > t0 else 0.0
        speed[i] = left + (right - left) * frac


def _moving_time(t0: float, t1: float, pauses: list[tuple[float, float]], starts: list[float]) -> float:
    """Time between two records minus the time the timer was paused (at least 1 s)."""
    paused = 0.0
    k = bisect.bisect_left(starts, t1) - 1
    while k >= 0:
        s, e = pauses[k]
        if e <= t0:
            break
        paused += max(0.0, min(e, t1) - max(s, t0))
        k -= 1
    dt = t1 - t0
    return max(dt - paused, min(dt, 1.0))


def _integrate(v0: float | None, v1: float | None, moving: float, recorded: float, cfg: Config) -> float:
    known = [v for v in (v0, v1) if v is not None]
    if not known:
        return min(max(recorded, 0.0), cfg.max_speed * moving)
    return sum(known) / len(known) * moving
