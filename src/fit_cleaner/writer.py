"""Apply byte-level patches to the original file and fix the CRCs.

The file layout never changes: every edited field keeps its size and position,
so all messages we do not touch (device info, developer fields, proprietary
data) are preserved byte for byte.
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

import fitdecode
from fitdecode.utils import compute_crc

from fit_cleaner.model import Activity, Patches


def apply_patches(activity: Activity, patches: Patches) -> bytes:
    buf = bytearray(activity.data)
    for offset, raw in patches.edits.items():
        buf[offset : offset + len(raw)] = raw
    for start, crc_offset in activity.crc_ranges:
        buf[crc_offset : crc_offset + 2] = struct.pack("<H", compute_crc(buf, start=start, end=crc_offset))
    return bytes(buf)


def verify(data: bytes) -> int:
    """Re-decode the file with strict CRC checking; return the number of data messages."""
    count = 0
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage):
                count += 1
    return count


def write(path: str | Path, data: bytes) -> None:
    Path(path).write_bytes(data)
