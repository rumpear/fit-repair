"""Data model shared by all stages of the pipeline."""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# FIT stores coordinates as "semicircles": 2^31 semicircles == 180 degrees.
SEMICIRCLES_PER_DEGREE = 2**31 / 180.0

# FIT timestamps count seconds from 1989-12-31T00:00:00Z.
FIT_EPOCH = 631065600

# Integer base types we know how to rewrite, with their "invalid" sentinel.
INVALID_VALUES = {
    "enum": 0xFF,
    "sint8": 0x7F,
    "uint8": 0xFF,
    "sint16": 0x7FFF,
    "uint16": 0xFFFF,
    "sint32": 0x7FFFFFFF,
    "uint32": 0xFFFFFFFF,
    "uint8z": 0,
    "uint16z": 0,
    "uint32z": 0,
}


def to_semicircles(degrees: float | None) -> int | None:
    return None if degrees is None else round(degrees * SEMICIRCLES_PER_DEGREE)


def to_degrees(semicircles: int | None) -> float | None:
    return None if semicircles is None else semicircles / SEMICIRCLES_PER_DEGREE


@dataclass(frozen=True)
class FieldRef:
    """Where a raw field lives in the file and how to encode a new value for it."""

    offset: int
    size: int  # total, all array elements
    fmt: str  # endian-prefixed struct format of one element, e.g. "<I"
    base_type: str
    scale: float
    add: float  # FIT profile "offset": value = raw / scale - add
    count: int = 1  # number of array elements

    def encode(self, value: float | datetime | Sequence[float | None] | None) -> bytes:
        if self.count == 1:
            return self._encode_one(value)
        values = list(value or ())[: self.count]
        values += [None] * (self.count - len(values))
        return b"".join(self._encode_one(v) for v in values)

    def _encode_one(self, value: float | datetime | None) -> bytes:
        if isinstance(value, datetime):
            value = value.timestamp() - FIT_EPOCH
        if value is None:
            raw = INVALID_VALUES[self.base_type]
        else:
            lo, hi = self._valid_range()
            raw = min(max(round((value + self.add) * self.scale), lo), hi)
        return struct.pack(self.fmt, raw)

    def _valid_range(self) -> tuple[int, int]:
        bits = self.size // self.count * 8
        if self.base_type.startswith("sint"):
            return -(2 ** (bits - 1)), 2 ** (bits - 1) - 2
        if self.base_type.endswith("z"):
            return 1, 2**bits - 1
        return 0, 2**bits - 2


@dataclass
class Message:
    """A decoded FIT data message plus the locations of its raw fields."""

    name: str
    values: dict[str, Any]
    fields: dict[str, FieldRef]
    # The whole raw message (header byte included) in Activity.data.
    offset: int = 0
    size: int = 0

    def get(self, name: str) -> Any:
        return self.values.get(name)

    def time(self, name: str = "timestamp") -> float | None:
        value = self.values.get(name)
        return value.timestamp() if isinstance(value, datetime) else None


@dataclass
class Activity:
    data: bytes
    messages: list[Message]
    # (segment start, CRC offset) for every FIT segment in the file.
    crc_ranges: list[tuple[int, int]]
    # (offset, size) of every definition message, in file order.
    definitions: list[tuple[int, int]] = field(default_factory=list)

    def by_name(self, name: str) -> list[Message]:
        return [m for m in self.messages if m.name == name]


@dataclass
class Track:
    """Per-record time series extracted from `record` messages."""

    t: list[float]  # POSIX seconds
    lat: list[float | None]  # degrees
    lon: list[float | None]
    dist: list[float | None]  # metres, cumulative
    speed: list[float | None]  # m/s

    def __len__(self) -> int:
        return len(self.t)

    def has_fix(self, i: int) -> bool:
        la, lo = self.lat[i], self.lon[i]
        if la is None or lo is None:
            return False
        if not (-90 <= la <= 90 and -180 <= lo <= 180):
            return False
        return not (la == 0 and lo == 0)  # "null island"


@dataclass(frozen=True)
class Config:
    # Upper bound for a bicycle, used both for GPS reachability and speed sensor data.
    max_speed: float = 100 / 3.6  # m/s
    # GPS noise allowance when checking whether one fix is reachable from another.
    gps_tolerance: float = 50.0  # m
    # Used only to grow an already detected speed spike over its ramps.
    max_accel: float = 4.0  # m/s^2
    # Removed GPS points are re-filled by interpolation when the hole is this short.
    interpolate_gap: float = 30.0  # s
    # Cross-check GPS against the distance stream (wheel sensor).
    sensor_check: bool = True
    consistency_window: float = 60.0  # s
    min_consistency: float = 0.5
    min_check_duration: float = 120.0  # s


@dataclass
class Change:
    """A human-readable description of one rewritten summary field."""

    message: str
    field: str
    old: Any
    new: Any


@dataclass
class Patches:
    """Byte-level edits to apply to the original file."""

    edits: dict[int, bytes] = field(default_factory=dict)

    def set(self, data: bytes, ref: FieldRef, value: float | None) -> bool:
        encoded = ref.encode(value)
        if data[ref.offset : ref.offset + ref.size] == encoded:
            self.edits.pop(ref.offset, None)
            return False
        self.edits[ref.offset] = encoded
        return True

    def __len__(self) -> int:
        return len(self.edits)
