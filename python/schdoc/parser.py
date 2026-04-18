from __future__ import annotations
import struct
from pathlib import Path

import olefile

from .models import (
    Component, ConnectorPin, PinRef, PowerRail,
    Zone, ProbePoint, SchematicSummary,
)


def parse_stream(data: bytes) -> list[dict]:
    """Split OLE FileHeader bytes into a list of record dicts."""
    records: list[dict] = []
    pos = 0
    stream_pos = 0
    while pos + 4 <= len(data):
        length = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if pos + length > len(data):
            break
        chunk = data[pos: pos + length]
        pos += length
        if not chunk.strip():
            stream_pos += 1
            continue
        text = chunk.decode("latin-1").strip("|").strip()
        rec: dict = {"_stream_pos": stream_pos}
        for token in text.split("|"):
            if "=" in token:
                key, _, val = token.partition("=")
                rec[key.strip()] = val.strip()
        stream_pos += 1
        if rec:
            records.append(rec)
    return records
