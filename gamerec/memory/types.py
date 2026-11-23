"""Value types shared by scanner and watcher."""
from __future__ import annotations

import struct

FORMATS = {
    "int8": "<b", "uint8": "<B",
    "int16": "<h", "uint16": "<H",
    "int32": "<i", "uint32": "<I",
    "int64": "<q", "uint64": "<Q",
    "float": "<f", "double": "<d",
}


def size_of(vtype: str) -> int:
    if vtype.startswith("bytes:"):
        return int(vtype.split(":", 1)[1])
    return struct.calcsize(FORMATS[vtype])


def decode(vtype: str, raw: bytes):
    if vtype.startswith("bytes:"):
        return raw.hex()
    v = struct.unpack(FORMATS[vtype], raw)[0]
    if isinstance(v, float):
        return round(v, 6)
    return v


def encode(vtype: str, value) -> bytes:
    if vtype.startswith("bytes:"):
        return bytes.fromhex(value)
    return struct.pack(FORMATS[vtype], value)


def parse_int(v) -> int:
    return int(v, 0) if isinstance(v, str) else int(v)
