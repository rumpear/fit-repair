"""Minimal FIT encoder for building synthetic activities in tests."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from fitdecode.utils import compute_crc

FIT_EPOCH = 631065600  # 1989-12-31T00:00:00Z
START = 1_700_000_000  # arbitrary POSIX start time
HOME = (50.40, 30.60)
LIMA = (-12.0422, -77.0584)

ENUM, UINT8, UINT16, SINT32, UINT32, UINT32Z = 0x00, 0x02, 0x84, 0x85, 0x86, 0x8C
_FMT = {ENUM: "B", UINT8: "B", UINT16: "H", SINT32: "i", UINT32: "I", UINT32Z: "I"}
_INVALID = {ENUM: 0xFF, UINT8: 0xFF, UINT16: 0xFFFF, SINT32: 0x7FFFFFFF, UINT32: 0xFFFFFFFF, UINT32Z: 0}

FILE_ID = (0, [(0, ENUM), (1, UINT16), (2, UINT16), (3, UINT32Z), (4, UINT32)])
EVENT = (21, [(253, UINT32), (0, ENUM), (1, ENUM)])
RECORD = (20, [(253, UINT32), (0, SINT32), (1, SINT32), (5, UINT32), (6, UINT16), (3, UINT8)])
LAP = (19, [(253, UINT32), (2, UINT32), (3, SINT32), (4, SINT32), (5, SINT32), (6, SINT32),
            (7, UINT32), (8, UINT32), (9, UINT32), (13, UINT16), (14, UINT16), (254, UINT16)])
# Fields are (number, base type) or (number, base type, array length).
SESSION = (18, [(253, UINT32), (2, UINT32), (3, SINT32), (4, SINT32), (7, UINT32), (8, UINT32),
                (9, UINT32), (14, UINT16), (15, UINT16), (29, SINT32), (30, SINT32), (31, SINT32),
                (32, SINT32), (5, ENUM), (6, ENUM), (16, UINT8), (17, UINT8), (18, UINT8),
                (11, UINT16), (26, UINT16), (65, UINT32, 5)])
ACTIVITY = (34, [(253, UINT32), (0, UINT32), (1, UINT16), (2, ENUM), (3, ENUM), (4, ENUM), (5, UINT32)])


@dataclass
class Point:
    t: float
    lat: float | None
    lon: float | None
    dist: float
    speed: float
    hr: int = 130


def semi(deg: float | None) -> int | None:
    return None if deg is None else round(deg * 2**31 / 180)


def fit_time(t: float) -> int:
    return int(t) - FIT_EPOCH


def offset(lat: float, lon: float, east_m: float, north_m: float) -> tuple[float, float]:
    dlat = north_m / 111_320
    dlon = east_m / (111_320 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


def straight_ride(
    n: int, speed: float = 5.0, start: tuple[float, float] = HOME, t0: float = START
) -> list[Point]:
    """n records one second apart riding due east at a constant speed."""
    return [Point(t0 + i, *offset(*start, speed * i, 0), dist=speed * i, speed=speed) for i in range(n)]


class _Encoder:
    def __init__(self) -> None:
        self.body = bytearray()

    def define(self, local: int, mesg: tuple[int, list[tuple[int, ...]]]) -> None:
        num, fields = mesg
        self.body += struct.pack("<BBBHB", 0x40 | local, 0, 0, num, len(fields))
        for def_num, base, *count in fields:
            size = struct.calcsize(_FMT[base]) * (count[0] if count else 1)
            self.body += struct.pack("<BBB", def_num, size, base)

    def data(self, local: int, mesg: tuple[int, list[tuple[int, ...]]], values: list) -> None:
        self.body += bytes([local])
        for (_, base, *count), value in zip(mesg[1], values):
            for v in value if count else [value]:
                self.body += struct.pack("<" + _FMT[base], _INVALID[base] if v is None else v)

    def build(self) -> bytes:
        header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(self.body), b".FIT")
        header += struct.pack("<H", compute_crc(header))
        data = header + bytes(self.body)
        return data + struct.pack("<H", compute_crc(data))


def build(
    points: list[Point],
    laps: list[tuple[int, int]] | None = None,
    pauses: list[tuple[float, float | None]] = (),
    stopped: bool = False,
    cadence: int = 80,
) -> bytes:
    """Encode an activity the way a head unit would, summaries computed from points.

    A pause without a start never ends. `stopped` adds the events a head unit
    writes when the rider presses stop and saves the ride.
    """
    enc = _Encoder()
    enc.define(0, FILE_ID)
    enc.data(0, FILE_ID, [4, 1, 1, 12345, fit_time(points[0].t)])

    enc.define(1, EVENT)
    enc.data(1, EVENT, [fit_time(points[0].t), 0, 0])
    for stop, start in pauses:
        enc.data(1, EVENT, [fit_time(stop), 0, 1])
        if start is not None:
            enc.data(1, EVENT, [fit_time(start), 0, 0])
    if stopped:
        enc.data(1, EVENT, [fit_time(points[-1].t), 0, 4])  # timer stop_all
        enc.data(1, EVENT, [fit_time(points[-1].t), 8, 9])  # session stop_disable_all

    enc.define(2, RECORD)
    for p in points:
        enc.data(2, RECORD, [fit_time(p.t), semi(p.lat), semi(p.lon), round(p.dist * 100),
                             round(p.speed * 1000), p.hr])

    enc.define(3, LAP)
    laps = laps or [(0, len(points) - 1)]
    for n, (a, b) in enumerate(laps):
        seg = points[a : b + 1]
        dur = seg[-1].t - seg[0].t
        dist = seg[-1].dist - (points[a - 1].dist if a > 0 else seg[0].dist)
        enc.data(3, LAP, [fit_time(seg[-1].t), fit_time(seg[0].t), semi(seg[0].lat), semi(seg[0].lon),
                          semi(seg[-1].lat), semi(seg[-1].lon), round(dur * 1000), round(dur * 1000),
                          round(dist * 100), round(dist / dur * 1000) if dur else 0,
                          round(max(p.speed for p in seg) * 1000), n])

    fixes = [p for p in points if p.lat is not None]
    dur = points[-1].t - points[0].t
    total = points[-1].dist - points[0].dist
    enc.define(4, SESSION)
    enc.data(4, SESSION, [fit_time(points[-1].t), fit_time(points[0].t), semi(points[0].lat),
                          semi(points[0].lon), round(dur * 1000), round(dur * 1000), round(total * 100),
                          round(total / dur * 1000), round(max(p.speed for p in points) * 1000),
                          semi(max(p.lat for p in fixes)), semi(max(p.lon for p in fixes)),
                          semi(min(p.lat for p in fixes)), semi(min(p.lon for p in fixes)), 2, 7,
                          round(sum(p.hr for p in points) / len(points)), max(p.hr for p in points),
                          cadence, round(dur / 10), len(laps), [round(dur * 1000), 0, 0, 0, 0]])

    enc.define(5, ACTIVITY)
    enc.data(5, ACTIVITY, [fit_time(points[-1].t), round(dur * 1000), 1, 0, 26, 1,
                           fit_time(points[-1].t) + 3 * 3600])
    return enc.build()
