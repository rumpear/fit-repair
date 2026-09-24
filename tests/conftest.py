from __future__ import annotations

import io
from dataclasses import dataclass

import fitdecode
import pytest

import fitbuilder
from fit_repair import writer
from fit_repair.model import Config
from fit_repair.pipeline import Result, clean
from fit_repair.reader import parse_activity


@dataclass
class Run:
    result: Result
    data_in: bytes
    data_out: bytes
    records: list[dict]
    laps: list[dict]
    session: dict

    def position(self, i: int) -> tuple[float, float] | None:
        rec = self.records[i]
        if rec.get("position_lat") is None:
            return None
        return fitbuilder_deg(rec["position_lat"]), fitbuilder_deg(rec["position_long"])


def fitbuilder_deg(semicircles: int) -> float:
    return semicircles * 180 / 2**31


def decode(data: bytes) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage):
                values: dict = {}
                for f in frame.fields:
                    values.setdefault(f.name, f.value)
                out.setdefault(frame.name, []).append(values)
    return out


@pytest.fixture
def run():
    def _run(points, laps=None, pauses=(), cfg: Config = Config()) -> Run:
        data = fitbuilder.build(points, laps, pauses)
        activity = parse_activity(data)
        result = clean(activity, cfg)
        out = writer.apply_patches(activity, result.patches)
        assert len(out) == len(data)
        assert writer.verify(out) == len(activity.messages)
        msgs = decode(out)
        return Run(result, data, out, msgs["record"], msgs.get("lap", []), msgs["session"][0])

    return _run
