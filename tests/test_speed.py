import pytest

from fitbuilder import Point, straight_ride


def _ride_with_burst() -> tuple[list[Point], list[tuple[float, float]], float]:
    """Ride, stop, auto-pause, then a GPS-driven 200 km/h burst while standing still.

    Mirrors what a head unit does when jammed GPS wakes it from auto-pause.
    """
    points = straight_ride(300)  # 0..299 riding at 5 m/s
    t, d = points[-1].t, points[-1].dist
    lat, lon = points[-1].lat, points[-1].lon
    for _ in range(20):  # standing at a traffic light
        t += 1
        points.append(Point(t, lat, lon, d, 0.0))
    pause = (t + 1, t + 120)
    t = pause[1]
    burst_start = len(points)
    for k in range(9):  # 56 m/s for nine records
        points.append(Point(t, lat, lon, d, 56.0))
        t += 1
        d += 56.0 if k < 8 else 0.0
    burst = d - points[burst_start - 1].dist
    for _ in range(10):  # still standing
        points.append(Point(t, lat, lon, d, 0.0))
        t += 1
    for k in range(300):  # riding on
        points.append(Point(t, lat, lon, d, 5.0))
        t += 1
        d += 5.0
    return points, [pause], burst


def test_speed_burst_is_removed_from_distance(run):
    points, pauses, burst = _ride_with_burst()
    laps = [(0, 299), (300, len(points) - 1)]
    r = run(points, laps=laps, pauses=pauses)

    total = points[-1].dist - burst
    assert burst == pytest.approx(8 * 56.0)
    assert max(rec["speed"] for rec in r.records) == pytest.approx(5.0)
    assert r.records[-1]["distance"] == pytest.approx(total, abs=0.01)
    distances = [rec["distance"] for rec in r.records]
    assert distances == sorted(distances)

    assert r.session["total_distance"] == pytest.approx(total, abs=0.01)
    assert r.session["max_speed"] == pytest.approx(5.0)
    orig_avg = points[-1].dist / (points[-1].t - points[0].t)
    assert r.session["avg_speed"] == pytest.approx(orig_avg * total / points[-1].dist, abs=0.001)

    # The first lap had no burst and stays as recorded.
    assert r.laps[0]["total_distance"] == pytest.approx(points[299].dist)
    assert r.laps[1]["total_distance"] == pytest.approx(total - points[299].dist, abs=0.01)
    assert r.laps[1]["max_speed"] == pytest.approx(5.0)


def test_speed_ramp_around_spike_is_repaired(run):
    points = straight_ride(200)
    profile = [15.0, 30.0, 40.0, 30.0, 15.0]
    d = points[99].dist
    for k, v in enumerate(profile):
        points[100 + k].speed = v
        d += v
        points[100 + k].dist = d
    for i in range(105, 200):
        d += 5.0
        points[i].dist = d
    r = run(points)

    assert r.result.speed.windows == [(100, 104)]
    assert [r.records[i]["speed"] for i in range(100, 105)] == pytest.approx([5.0] * 5)
    assert r.records[-1]["distance"] == pytest.approx(199 * 5.0, abs=0.01)


def test_distance_jump_without_speed_spike_is_repaired(run):
    points = straight_ride(200)
    for i in range(100, 200):
        points[i].dist += 5000 if i == 100 else 0
    for i in range(101, 200):
        points[i].dist += 5000
    r = run(points)

    assert r.records[-1]["distance"] == pytest.approx(199 * 5.0, abs=0.01)
    assert r.session["total_distance"] == pytest.approx(199 * 5.0, abs=0.01)
