"""Concatenate the raw messages of several FIT files into one file.

Every definition message is copied, so each data message is decoded with the
same definition it had in its own file, even when the files use local message
numbers differently. An unused definition is allowed by the FIT protocol, so
dropping a data message never breaks the ones after it.
"""

from __future__ import annotations

import struct

from fitdecode.utils import compute_crc

from fit_repair.model import Activity, Message

HEADER_SIZE = 14


def splice(files: list[Activity], kept: list[list[Message]]) -> bytes:
    """One FIT file with the given data messages of each file, in file order.

    Every file must be a single FIT segment starting at offset 0.
    """
    body = bytearray()
    for activity, messages in zip(files, kept):
        chunks = sorted([*activity.definitions, *((m.offset, m.size) for m in messages)])
        for offset, size in chunks:
            body += activity.data[offset : offset + size]

    protocol = max(activity.data[1] for activity in files)
    profile = max(struct.unpack_from("<H", activity.data, 2)[0] for activity in files)
    header = struct.pack("<BBHI4s", HEADER_SIZE, protocol, profile, len(body), b".FIT")
    header += struct.pack("<H", compute_crc(header))
    data = header + bytes(body)
    return data + struct.pack("<H", compute_crc(data))
