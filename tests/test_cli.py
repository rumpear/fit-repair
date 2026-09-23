import os
from pathlib import Path

import pytest

import fitbuilder
from conftest import decode
from fit_cleaner.cli import main


def _spoofed_file(tmp_path: Path) -> Path:
    points = fitbuilder.straight_ride(400)
    for i in range(300, 400):
        points[i].lat, points[i].lon = fitbuilder.LIMA
    path = tmp_path / "ride.fit"
    path.write_bytes(fitbuilder.build(points))
    return path


def test_dry_run_writes_nothing(tmp_path, capsys):
    src = _spoofed_file(tmp_path)
    assert main([str(src), "--dry-run"]) == 0
    assert not (tmp_path / "ride.clean.fit").exists()
    assert "удалено координат: 100" in capsys.readouterr().out


def test_writes_clean_copy_next_to_input(tmp_path):
    src = _spoofed_file(tmp_path)
    original = src.read_bytes()
    assert main([str(src)]) == 0

    assert src.read_bytes() == original
    records = decode((tmp_path / "ride.clean.fit").read_bytes())["record"]
    assert all(r["position_lat"] is None for r in records[300:])


def test_refuses_to_overwrite_input(tmp_path):
    src = _spoofed_file(tmp_path)
    with pytest.raises(SystemExit):
        main([str(src), "-o", str(src)])


@pytest.mark.skipif(not os.environ.get("FIT_CLEANER_SAMPLE"), reason="set FIT_CLEANER_SAMPLE=<path to a real .fit>")
def test_real_sample(tmp_path):
    out = tmp_path / "sample.clean.fit"
    assert main([os.environ["FIT_CLEANER_SAMPLE"], "-o", str(out)]) == 0
    msgs = decode(out.read_bytes())
    assert max(r["speed"] for r in msgs["record"] if r.get("speed") is not None) < 100 / 3.6
