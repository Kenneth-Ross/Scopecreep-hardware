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
        if len(rec) > 1:
            records.append(rec)
    return records


_CONNECTOR_KEYWORDS = {"connector", "molex", "header", "socket", "plug"}


def _is_connector(designator: str, description: str, footprint: str) -> bool:
    if designator.upper().startswith("J"):
        return True
    text = (description + " " + footprint).lower()
    return any(kw in text for kw in _CONNECTOR_KEYWORDS)


def build_components(records: list[dict]) -> list[Component]:
    """Build Component objects from parsed records using OwnerIndex parent-child linking."""
    comp_records: dict[int, dict] = {
        rec["_stream_pos"]: rec
        for rec in records
        if rec.get("RECORD") == "1"
    }

    children: dict[int, list[dict]] = {pos: [] for pos in comp_records}
    for rec in records:
        if rec.get("RECORD") == "1":
            continue
        owner_idx = rec.get("OwnerIndex")
        if owner_idx is None:
            continue
        parent_pos = int(owner_idx)   # OwnerIndex == parent's _stream_pos directly
        if parent_pos in comp_records:
            children[parent_pos].append(rec)

    components: list[Component] = []
    for pos, crec in comp_records.items():
        kids = children[pos]
        designator = ""
        value = ""
        part_number = ""
        manufacturer = ""
        footprint = ""
        description = crec.get("ComponentDescription", "")

        for kid in kids:
            r = kid.get("RECORD")
            if r == "34" and kid.get("Name") == "Designator":
                designator = kid.get("Text", "")
            elif r == "41":
                name = kid.get("Name", "")
                text = kid.get("Text", "")
                if name in ("Comment", "Value") and not value:
                    value = text
                elif name == "Part Number":
                    part_number = text
                elif name == "Manufacturer":
                    manufacturer = text
            elif r == "45":
                footprint = kid.get("ModelDatafileEntity0", "")

        if designator:
            components.append(Component(
                designator=designator,
                value=value,
                description=description,
                part_number=part_number,
                manufacturer=manufacturer,
                footprint=footprint,
                is_connector=_is_connector(designator, description, footprint),
                pins=[],
            ))

    return components
