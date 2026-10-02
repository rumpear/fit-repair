from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import fitbuilder
from conftest import decode
from fit_repair.cli import main
from fit_repair.model import Config

UTC = timezone.utc


def _spoofed_everywhere(tmp_path: Path) -> Path:
    points = fitbuilder.straight_ride(400, start=fitbuilder.LIMA)
    path = tmp_path / "ride.fit"
    pause = (fitbuilder.START + 100, fitbuilder.START + 110)
    path.write_bytes(fitbuilder.build(points, laps=[(0, 199), (200, 399)], pauses=[pause]))
    return path


def _times(msgs: dict[str, list[dict]]) -> list[datetime]:
    return [v for name in ("file_id", "event", "record", "lap", "session", "activity")
            for m in msgs[name] for v in m.values() if isinstance(v, datetime)]


def test_strip_gps_clears_every_coordinate(run):
    points = fitbuilder.straight_ride(400, start=fitbuilder.LIMA)
    r = run(points, laps=[(0, 199), (200, 399)], cfg=Config(strip_gps=True))

    assert all(rec["position_lat"] is None and rec["position_long"] is None for rec in r.records)
    for msg in r.laps + [r.session]:
        assert all(v is None for k, v in msg.items() if k.endswith("_lat") or k.endswith("_long"))
    assert r.result.stripped == 400 * 2 + 2 * 4 + 6
    # Distance and the rest are untouched.
    assert [rec["distance"] for rec in r.records] == [p.dist for p in points]
    assert r.session["total_distance"] == points[-1].dist
    assert not r.result.changes


def test_start_time_shifts_every_time(tmp_path):
    src = _spoofed_everywhere(tmp_path)
    before = decode(src.read_bytes())
    # The builder's device clock is UTC+3; a naive start is taken in that zone.
    assert main([str(src), "--start-time", "2026-09-27 15:32:20"]) == 0
    after = decode((tmp_path / "ride.clean.fit").read_bytes())

    start = datetime(2026, 9, 27, 12, 32, 20, tzinfo=UTC)
    assert after["session"][0]["start_time"] == start
    shift = start - before["session"][0]["start_time"]
    old, new = _times(before), _times(after)
    assert len(old) == len(new) > 400
    assert all(b - a == shift for a, b in zip(old, new))
    act = after["activity"][0]
    assert act["local_timestamp"] - act["timestamp"] == timedelta(hours=3)
    # Coordinates are still repaired as usual: nothing spoofed inside the track.
    assert after["record"][0]["position_lat"] == before["record"][0]["position_lat"]


def test_start_time_with_explicit_offset(tmp_path):
    src = _spoofed_everywhere(tmp_path)
    assert main([str(src), "--start-time", "2026-09-27T15:32:20-05:00", "--strip-gps"]) == 0
    after = decode((tmp_path / "ride.clean.fit").read_bytes())
    assert after["session"][0]["start_time"] == datetime(2026, 9, 27, 20, 32, 20, tzinfo=UTC)
    assert all(r["position_lat"] is None for r in after["record"])


def test_report_shows_shift_and_strip(tmp_path, capsys):
    src = _spoofed_everywhere(tmp_path)
    assert main([str(src), "--start-time", "2026-09-27 15:32:20", "--strip-gps", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "-> 2026-09-27 15:32:20 (shifted by +" in out
    assert "all coordinates removed (--strip-gps): 814 fields" in out


def test_bad_start_time_is_rejected(tmp_path, capsys):
    src = _spoofed_everywhere(tmp_path)
    with pytest.raises(SystemExit):
        main([str(src), "--start-time", "yesterday"])
    assert "not a date and time" in capsys.readouterr().err
