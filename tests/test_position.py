import math

import pytest

from fitbuilder import HOME, LIMA, Point, offset, straight_ride
from fit_cleaner import geo
from fit_cleaner.model import Config


def _spoof(p: Point, i: int) -> None:
    """Move a point to Lima with ~20 m jitter, like a spoofer does."""
    p.lat, p.lon = offset(*LIMA, 20 * (-1) ** i, 0)


def _freeze(points: list[Point], a: int, b: int) -> None:
    """Repeat the position of point a-1 (with cm-level noise) over [a, b]."""
    lat, lon = points[a - 1].lat, points[a - 1].lon
    for i in range(a, b + 1):
        points[i].lat, points[i].lon = offset(lat, lon, 0.01 * (i % 2), 0)


def test_clean_ride_is_untouched(run):
    r = run(straight_ride(600), laps=[(0, 299), (300, 599)])
    assert len(r.result.patches) == 0
    assert r.data_out == r.data_in


def test_spoofed_tail_is_removed(run):
    points = straight_ride(900)
    for i in range(600, 900):
        _spoof(points[i], i)
    r = run(points, laps=[(0, 449), (450, 899)])

    assert all(r.position(i) is not None for i in range(600))
    assert all(r.position(i) is None for i in range(600, 900))
    # Distance comes from the wheel sensor and is fine.
    assert r.records[-1]["distance"] == pytest.approx(points[-1].dist)
    assert r.session["total_distance"] == pytest.approx(points[-1].dist)
    # Bounding box no longer reaches South America.
    assert r.session["swc_lat"] * 180 / 2**31 == pytest.approx(HOME[0], abs=1e-4)
    assert r.laps[1]["start_position_lat"] is not None
    assert r.laps[1]["end_position_lat"] is None


def test_frozen_position_before_spoof_is_removed(run):
    points = straight_ride(900)
    _freeze(points, 500, 650)
    for i in range(651, 900):
        _spoof(points[i], i)
    r = run(points)

    assert all(r.position(i) is not None for i in range(500))
    assert all(r.position(i) is None for i in range(500, 900))
    assert set(r.result.position.stale) >= set(range(500, 651))


def test_frozen_then_reacquired_keeps_both_sides(run):
    points = straight_ride(700)
    _freeze(points, 300, 399)
    # The receiver converges back onto the true position over three fixes.
    for i, err in zip(range(400, 403), (600, 300, 100)):
        points[i].lat, points[i].lon = offset(points[i].lat, points[i].lon, 0, err)
    r = run(points)

    assert all(r.position(i) is not None for i in range(300))
    assert all(r.position(i) is None for i in range(300, 403))
    assert all(r.position(i) is not None for i in range(403, 700))


def test_short_spike_is_interpolated(run):
    points = straight_ride(600)
    truth = {i: (points[i].lat, points[i].lon) for i in range(300, 303)}
    for i in truth:
        points[i].lat, points[i].lon = offset(points[i].lat, points[i].lon, 0, 700)
    r = run(points)

    assert r.result.position.interpolated == [300, 301, 302]
    for i, (lat, lon) in truth.items():
        assert geo.distance(*r.position(i), lat, lon) < 2


def test_interpolation_can_be_disabled(run):
    points = straight_ride(600)
    for i in range(300, 303):
        points[i].lat, points[i].lon = offset(points[i].lat, points[i].lon, 0, 700)
    r = run(points, cfg=Config(interpolate_gap=0))

    assert all(r.position(i) is None for i in range(300, 303))


def test_spoof_at_start_is_removed(run):
    points = straight_ride(700)
    for i in range(100):
        _spoof(points[i], i)
    r = run(points)

    assert all(r.position(i) is None for i in range(100))
    assert all(r.position(i) is not None for i in range(100, 700))
    assert r.session["start_position_lat"] is None


def test_loop_ride_is_kept(run):
    # 100 m radius circuit: straight-line displacement is tiny compared to the
    # wheel distance, but the GPS path length matches it.
    radius, speed = 100.0, 6.0
    points = []
    for i in range(1800):
        angle = speed * i / radius
        lat, lon = offset(*HOME, radius * math.cos(angle), radius * math.sin(angle))
        points.append(Point(points[0].t + i if points else 1_700_000_000, lat, lon, speed * i, speed))
    r = run(points)

    assert r.result.position.rejected == []
    assert len(r.result.patches) == 0


def test_dead_wheel_sensor_does_not_discard_gps(run):
    points = straight_ride(1800)
    for p in points:
        p.dist, p.speed = 0.0, 0.0
    r = run(points)

    assert r.result.position.rejected == []
    assert r.result.position.warnings


def test_sensor_check_can_be_disabled(run):
    points = straight_ride(900)
    _freeze(points, 500, 650)
    for i in range(651, 900):
        _spoof(points[i], i)
    r = run(points, cfg=Config(sensor_check=False))

    # Without the wheel sensor a frozen position looks like standing still,
    # but the jump to Lima is still impossible.
    assert all(r.position(i) is not None for i in range(651))
    assert all(r.position(i) is None for i in range(651, 900))
