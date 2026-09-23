import os
from pathlib import Path

import pytest

import fitbuilder
from conftest import decode
from fit_cleaner import writer
from fit_cleaner.merge import MergeConfig, MergeError, merge
from fit_cleaner.merge_cli import main
from fit_cleaner.model import Config
from fit_cleaner.pipeline import clean
from fit_cleaner.reader import parse_activity, read_activity, timer_pauses
from fitbuilder import HOME, START, offset, straight_ride

# First recording: 300 s at 5 m/s. The second starts 135 s later, 40 m further on.
A_END = START + 299
A_DIST = 5.0 * 299
B_START = A_END + 135
B_POS = offset(*offset(*HOME, A_DIST, 0), 40, 0)


def recording(n: int, t0: float, start=HOME, hr: int = 130, laps=None, **kw) -> bytes:
    points = straight_ride(n, t0=t0, start=start)
    for p in points:
        p.hr = hr
    return fitbuilder.build(points, laps=laps, stopped=True, **kw)


def run_merge(*files: bytes, cfg: MergeConfig = MergeConfig()):
    result = merge([parse_activity(f) for f in files], cfg)
    out = writer.apply_patches(result.activity, result.patches)
    assert writer.verify(out) == len(result.activity.messages)
    return result, out, decode(out)


def two_recordings() -> tuple[bytes, bytes]:
    a = recording(300, START, hr=120, cadence=70)
    b = recording(400, B_START, start=B_POS, hr=150, cadence=90, laps=[(0, 199), (200, 399)])
    return a, b


def test_merges_two_recordings_into_one_activity():
    result, _, msgs = run_merge(*two_recordings())

    assert [len(msgs[name]) for name in ("file_id", "session", "activity", "record", "lap")] == [1, 1, 1, 700, 3]
    dist = [r["distance"] for r in msgs["record"]]
    assert dist == sorted(dist)
    assert dist[300] == pytest.approx(A_DIST)  # no distance added across the break
    assert dist[-1] == pytest.approx(A_DIST + 5.0 * 399)
    assert [lap["message_index"] for lap in msgs["lap"]] == [0, 1, 2]

    s = msgs["session"][0]
    timer = 299 + 399
    assert s["total_distance"] == pytest.approx(A_DIST + 5.0 * 399)
    assert s["total_timer_time"] == pytest.approx(timer)
    assert s["total_elapsed_time"] == pytest.approx(B_START + 399 - START)
    assert s["avg_speed"] == pytest.approx(s["total_distance"] / timer, abs=1e-3)
    assert s["avg_heart_rate"] == round((120 * 299 + 150 * 399) / timer)
    assert s["max_heart_rate"] == 150
    assert s["avg_cadence"] == round((70 * 299 + 90 * 399) / timer)
    assert s["total_calories"] == round(299 / 10) + round(399 / 10)
    assert s["time_in_hr_zone"] == (timer, 0, 0, 0, 0)
    assert s["num_laps"] == 3
    assert s["start_time"].timestamp() == START
    assert msgs["activity"][0]["total_timer_time"] == pytest.approx(timer)
    assert result.unmerged == []


def test_break_between_recordings_becomes_a_pause():
    result, out, msgs = run_merge(*two_recordings())

    pauses = timer_pauses(parse_activity(out).by_name("event"))
    assert pauses == [(A_END, B_START)]
    ends = [(e["event"], e["event_type"]) for e in msgs["event"] if e["event_type"] in ("stop_all", "stop_disable_all")]
    assert ends == [("timer", "stop_all"), ("session", "stop_disable_all")]  # only the last recording's
    assert (result.events_converted, result.events_dropped) == (1, 1)


def test_end_of_recording_is_dropped_when_timer_is_already_paused():
    points = straight_ride(300)
    a = fitbuilder.build(points, pauses=[(A_END - 20, None)], stopped=True)
    result, out, _ = run_merge(a, two_recordings()[1])

    assert (result.events_converted, result.events_dropped) == (0, 2)
    assert timer_pauses(parse_activity(out).by_name("event")) == [(A_END - 20, B_START)]


def test_input_order_does_not_matter():
    a, b = two_recordings()
    result, out, _ = run_merge(b, a)
    assert result.order == [1, 0]
    assert out == run_merge(a, b)[1]


def test_three_recordings():
    a, b = two_recordings()
    c_start = B_START + 399 + 60
    c = recording(100, c_start, start=offset(*B_POS, 5.0 * 399, 0))
    _, _, msgs = run_merge(c, a, b)

    dist = [r["distance"] for r in msgs["record"]]
    assert dist == sorted(dist)
    assert dist[-1] == pytest.approx(5.0 * (299 + 399 + 99))
    assert [lap["message_index"] for lap in msgs["lap"]] == [0, 1, 2, 3]
    assert msgs["session"][0]["total_elapsed_time"] == pytest.approx(c_start + 99 - START)


def test_bridge_gap_adds_the_straight_line():
    _, _, msgs = run_merge(*two_recordings(), cfg=MergeConfig(bridge_gap=True))
    assert msgs["record"][300]["distance"] == pytest.approx(A_DIST + 40, abs=0.5)
    assert msgs["session"][0]["total_distance"] == pytest.approx(A_DIST + 40 + 5.0 * 399, abs=0.5)


def test_overlapping_recordings_are_rejected():
    a = recording(300, START)
    b = recording(300, START + 200)
    with pytest.raises(MergeError, match="пересекаются"):
        merge([parse_activity(a), parse_activity(b)])


def test_long_break_needs_force():
    a = recording(300, START)
    b = recording(300, START + 3 * 3600)
    files = [parse_activity(a), parse_activity(b)]
    with pytest.raises(MergeError, match="разные поездки"):
        merge(files)
    assert merge(files, MergeConfig(force=True)).session["num_laps"] == 2


def test_merged_file_can_be_cleaned():
    a, _ = two_recordings()
    points = straight_ride(400, t0=B_START, start=B_POS)
    for i in range(300, 400):
        points[i].lat, points[i].lon = fitbuilder.LIMA
    b = fitbuilder.build(points, stopped=True)
    _, out, _ = run_merge(a, b)

    result = clean(parse_activity(out), Config())
    assert sorted(result.position.rejected) == list(range(600, 700))


def test_cli_names_output_after_the_earliest_recording(tmp_path, capsys):
    a, b = two_recordings()
    (tmp_path / "first.fit").write_bytes(a)
    (tmp_path / "second.fit").write_bytes(b)

    assert main([str(tmp_path / "second.fit"), str(tmp_path / "first.fit")]) == 0
    assert "пауза, дистанция не добавлена" in capsys.readouterr().out
    msgs = decode((tmp_path / "first.merged.fit").read_bytes())
    assert len(msgs["record"]) == 700


def test_cli_dry_run_writes_nothing(tmp_path):
    a, b = two_recordings()
    (tmp_path / "a.fit").write_bytes(a)
    (tmp_path / "b.fit").write_bytes(b)
    assert main([str(tmp_path / "a.fit"), str(tmp_path / "b.fit"), "--dry-run"]) == 0
    assert not (tmp_path / "a.merged.fit").exists()


def test_cli_refuses_to_overwrite_input(tmp_path):
    a, b = two_recordings()
    (tmp_path / "a.fit").write_bytes(a)
    (tmp_path / "b.fit").write_bytes(b)
    with pytest.raises(SystemExit):
        main([str(tmp_path / "a.fit"), str(tmp_path / "b.fit"), "-o", str(tmp_path / "b.fit")])


@pytest.mark.skipif(
    not os.environ.get("FIT_MERGE_SAMPLES"),
    reason=f"set FIT_MERGE_SAMPLES=<ride1.fit>{os.pathsep}<ride2.fit>...",
)
def test_real_samples(tmp_path):
    paths = [Path(p) for p in os.environ["FIT_MERGE_SAMPLES"].split(os.pathsep)]
    out = tmp_path / "sample.merged.fit"
    assert main([*map(str, paths), "-o", str(out)]) == 0

    parts = [read_activity(p).by_name("session")[0].get("total_distance") for p in paths]
    msgs = decode(out.read_bytes())
    dist = [r["distance"] for r in msgs["record"] if r.get("distance") is not None]
    assert dist == sorted(dist)
    assert msgs["session"][0]["total_distance"] == pytest.approx(sum(parts), abs=0.1)
