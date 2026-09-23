"""Detect GPS fixes that cannot be real and remove them.

1. Drop stale fixes: under jamming many devices keep repeating the last known
   position while the wheel sensor shows the bike moving on.
2. Split the track into chains of consecutive fixes that are physically
   reachable from each other (distance <= max_speed * dt + tolerance). A chain
   also ends wherever the GPS was not tracking (no fix or stale fixes), so a
   fix taken while the receiver re-acquires never glues onto the old chain.
3. Score every chain by its duration times its agreement with the distance
   stream (wheel sensor): a spoofed position that stands still or jitters
   while the wheel keeps turning disagrees with it.
4. Accept chains greedily, best first. A chain is accepted only if it is
   reachable from the nearest accepted chain before it and can reach the
   nearest accepted chain after it. The decision is made at the jump itself,
   so a spoofed position never becomes "reachable" just because time passes.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from fit_cleaner import geo
from fit_cleaner.model import Config, Track

# GPS path vs wheel distance over one consistency window agree when they
# differ by no more than this share of the larger one plus a fixed margin.
_REL_TOLERANCE = 0.35
_ABS_TOLERANCE = 60.0  # m

# A position that stays within this radius while the wheel covers the given
# distance is a frozen (stale) fix, not a real one.
_STALE_RADIUS = 3.0  # m
_STALE_MIN_WHEEL = 30.0  # m


@dataclass
class Chain:
    idx: list[int]
    consistency: float | None = None  # share of windows agreeing with the wheel

    @property
    def start(self) -> int:
        return self.idx[0]

    @property
    def end(self) -> int:
        return self.idx[-1]


@dataclass
class PositionFix:
    lat: list[float | None]
    lon: list[float | None]
    rejected: list[int]  # records whose original fix was removed
    interpolated: list[int]  # rejected records re-filled by interpolation
    stale: list[int]  # rejected because the position was frozen
    sensor_check: bool
    warnings: list[str] = field(default_factory=list)


def repair_positions(track: Track, dist: list[float | None], cfg: Config) -> PositionFix:
    fixes = [i for i in range(len(track)) if track.has_fix(i)]
    use_sensor = cfg.sensor_check and any(d is not None for d in dist)

    stale = _stale_fixes(fixes, track, dist) if use_sensor else set()
    usable = [track.has_fix(i) and i not in stale for i in range(len(track))]
    chains = _build_chains(usable, track, cfg)
    if use_sensor:
        for chain in chains:
            chain.consistency = _consistency(chain.idx, track, dist, cfg)

    warnings: list[str] = []
    accepted, use_sensor = _select(chains, track, cfg, use_sensor, warnings)

    keep = {i for k in accepted for i in chains[k].idx}
    rejected = [i for i in fixes if i not in keep]
    lat = [track.lat[i] if i in keep else None for i in range(len(track))]
    lon = [track.lon[i] if i in keep else None for i in range(len(track))]
    interpolated = _fill_short_gaps(lat, lon, set(rejected), track, dist, cfg)

    return PositionFix(
        lat=lat,
        lon=lon,
        rejected=rejected,
        interpolated=interpolated,
        stale=sorted(stale),
        sensor_check=use_sensor,
        warnings=warnings,
    )


def _reachable(i: int, j: int, track: Track, cfg: Config) -> bool:
    gap = geo.distance(track.lat[i], track.lon[i], track.lat[j], track.lon[j])
    return gap <= cfg.max_speed * abs(track.t[j] - track.t[i]) + cfg.gps_tolerance


def _stale_fixes(fixes: list[int], track: Track, dist: list[float | None]) -> set[int]:
    """Fixes repeating an earlier position while the wheel sensor shows movement."""
    stale: set[int] = set()
    run: list[int] = []

    def close() -> None:
        d0, d1 = dist[run[0]], dist[run[-1]]
        if len(run) > 1 and d0 is not None and d1 is not None and d1 - d0 >= _STALE_MIN_WHEEL:
            stale.update(run[1:])

    for i in fixes:
        s = run[0] if run else None
        if s is not None and geo.distance(track.lat[s], track.lon[s], track.lat[i], track.lon[i]) <= _STALE_RADIUS:
            run.append(i)
            continue
        if run:
            close()
        run = [i]
    if run:
        close()
    return stale


def _build_chains(usable: list[bool], track: Track, cfg: Config) -> list[Chain]:
    """Records without a usable fix end the current chain."""
    chains: list[Chain] = []
    broken = True
    for i, ok in enumerate(usable):
        if not ok:
            broken = True
            continue
        if not broken and _reachable(chains[-1].end, i, track, cfg):
            chains[-1].idx.append(i)
        else:
            chains.append(Chain(idx=[i]))
        broken = False
    return chains


def _consistency(idx: list[int], track: Track, dist: list[float | None], cfg: Config) -> float | None:
    """Share of time windows where the GPS path length matches the wheel distance."""
    pts = [i for i in idx if dist[i] is not None]
    if len(pts) < 2 or track.t[pts[-1]] - track.t[pts[0]] < cfg.min_check_duration:
        return None

    path = [0.0]
    for a, b in zip(pts, pts[1:]):
        path.append(path[-1] + geo.distance(track.lat[a], track.lon[a], track.lat[b], track.lon[b]))

    samples = agree = 0
    s = 0
    for e in range(1, len(pts)):
        if track.t[pts[e]] - track.t[pts[s]] < cfg.consistency_window:
            continue
        gps = path[e] - path[s]
        wheel = dist[pts[e]] - dist[pts[s]]
        samples += 1
        if abs(gps - wheel) <= _REL_TOLERANCE * max(gps, wheel) + _ABS_TOLERANCE:
            agree += 1
        s = e
    return agree / samples if samples else None


def _select(
    chains: list[Chain], track: Track, cfg: Config, use_sensor: bool, warnings: list[str]
) -> tuple[list[int], bool]:
    def score(c: Chain) -> float:
        weight = c.consistency if use_sensor and c.consistency is not None else 1.0
        return (duration(c) + 1.0) * weight

    def duration(c: Chain) -> float:
        return track.t[c.end] - track.t[c.start]

    def inconsistent(c: Chain) -> bool:
        return use_sensor and c.consistency is not None and c.consistency < cfg.min_consistency

    # If the sensor disagrees with most of the GPS, the sensor is the suspect one
    # (dead magnet, wrong wheel size): stop judging GPS by it.
    if use_sensor:
        disputed = sum(duration(c) for c in chains if inconsistent(c))
        if disputed > sum(duration(c) for c in chains) / 2:
            warnings.append(
                "GPS в основном не согласуется с датчиком дистанции - сверка с датчиком отключена"
            )
            use_sensor = False

    accepted: list[int] = []  # chain ids, kept sorted (== time order)
    for k in sorted(range(len(chains)), key=lambda k: score(chains[k]), reverse=True):
        chain = chains[k]
        if inconsistent(chain):
            continue
        pos = bisect.bisect_left(accepted, k)
        before = chains[accepted[pos - 1]] if pos > 0 else None
        after = chains[accepted[pos]] if pos < len(accepted) else None
        if before is not None and not _reachable(before.end, chain.start, track, cfg):
            continue
        if after is not None and not _reachable(chain.end, after.start, track, cfg):
            continue
        accepted.insert(pos, k)
    return accepted, use_sensor


def _fill_short_gaps(
    lat: list[float | None],
    lon: list[float | None],
    rejected: set[int],
    track: Track,
    dist: list[float | None],
    cfg: Config,
) -> list[int]:
    """Interpolate removed fixes lying in a short hole between two kept fixes."""
    if cfg.interpolate_gap <= 0:
        return []
    kept = [i for i in range(len(lat)) if lat[i] is not None]
    filled: list[int] = []
    for a, b in zip(kept, kept[1:]):
        if b - a < 2 or track.t[b] - track.t[a] > cfg.interpolate_gap:
            continue
        for k in range(a + 1, b):
            if k not in rejected:
                continue
            frac = _fraction(a, b, k, track.t, dist)
            lat[k] = lat[a] + (lat[b] - lat[a]) * frac
            lon[k] = lon[a] + (lon[b] - lon[a]) * frac
            filled.append(k)
    return filled


def _fraction(a: int, b: int, k: int, t: list[float], dist: list[float | None]) -> float:
    da, db, dk = dist[a], dist[b], dist[k]
    if da is not None and db is not None and dk is not None and db > da:
        return min(max((dk - da) / (db - da), 0.0), 1.0)
    return (t[k] - t[a]) / (t[b] - t[a]) if t[b] > t[a] else 0.0
