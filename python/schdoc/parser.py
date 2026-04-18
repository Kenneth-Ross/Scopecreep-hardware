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


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[tuple[int, int], tuple[int, int]] = {}

    def find(self, x: tuple[int, int]) -> tuple[int, int]:
        if x not in self._parent:
            self._parent[x] = x
            return x
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: tuple[int, int], b: tuple[int, int]) -> None:
        self._parent[self.find(a)] = self.find(b)


def _xy(rec: dict, prefix: str = "Location") -> tuple[int, int] | None:
    try:
        return (int(rec[f"{prefix}.X"]), int(rec[f"{prefix}.Y"]))
    except (KeyError, ValueError):
        return None


def resolve_nets(
    records: list[dict], components: list[Component]
) -> dict[str, list[PinRef]]:
    """
    Build a net->PinRef map using coordinate union-find.
    Also populates Component.pins with ConnectorPin objects.
    """
    uf = _UnionFind()
    coord_name: dict[tuple[int, int], str] = {}

    # Wire segment endpoints
    for rec in records:
        if rec.get("RECORD") != "27":
            continue
        try:
            n = int(rec.get("LocationCount", 0))
        except ValueError:
            continue
        pts = []
        for i in range(1, n + 1):
            try:
                pt = (int(rec[f"X{i}"]), int(rec[f"Y{i}"]))
                pts.append(pt)
                uf.find(pt)  # register
            except (KeyError, ValueError):
                pass
        for i in range(len(pts) - 1):
            uf.union(pts[i], pts[i + 1])

    # Power ports
    for rec in records:
        if rec.get("RECORD") != "17":
            continue
        pt = _xy(rec)
        if pt and rec.get("Text"):
            uf.find(pt)
            coord_name[pt] = rec["Text"]

    # Net labels
    for rec in records:
        if rec.get("RECORD") != "25":
            continue
        pt = _xy(rec)
        if pt and rec.get("Text"):
            uf.find(pt)
            coord_name[pt] = rec["Text"]

    # Junctions
    for rec in records:
        if rec.get("RECORD") == "29":
            pt = _xy(rec)
            if pt:
                uf.find(pt)

    # Build index: _stream_pos of RECORD=1 -> Component
    comp_by_pos: dict[int, Component] = {}
    comp_rec_list = [r for r in records if r.get("RECORD") == "1"]
    for idx, crec in enumerate(comp_rec_list):
        if idx < len(components):
            comp_by_pos[crec["_stream_pos"]] = components[idx]

    # Collect pin records per component
    pin_records: list[tuple[Component, dict]] = []
    for rec in records:
        if rec.get("RECORD") != "2":
            continue
        owner_idx = rec.get("OwnerIndex")
        if owner_idx is None:
            continue
        parent_pos = int(owner_idx)   # direct equality: OwnerIndex == parent _stream_pos
        comp = comp_by_pos.get(parent_pos)
        if comp:
            pin_records.append((comp, rec))

    # Register pin locations in union-find; clear any stale pins from prior calls
    seen_comps: set[int] = set()
    for comp, prec in pin_records:
        if id(comp) not in seen_comps:
            comp.pins = []
            seen_comps.add(id(comp))
        pt = _xy(prec)
        if pt:
            uf.find(pt)

    # Name clusters from coord_name
    root_name: dict[tuple[int, int], str] = {}
    for pt, name in coord_name.items():
        root = uf.find(pt)
        if root not in root_name:
            root_name[root] = name

    # Assign NET_N to unnamed clusters that have pins
    cluster_counter = 0
    nets: dict[str, list[PinRef]] = {}

    for comp, prec in pin_records:
        pt = _xy(prec)
        if not pt:
            continue
        root = uf.find(pt)
        if root not in root_name:
            root_name[root] = f"NET_{cluster_counter}"
            cluster_counter += 1
        net_name = root_name[root]
        ref = PinRef(
            designator=comp.designator,
            pin_name=prec.get("Name", ""),
            pin_number=prec.get("Designator", ""),
            electrical_type=int(prec.get("Electrical", -1)),
        )
        nets.setdefault(net_name, []).append(ref)

        # Populate ConnectorPin on the component
        comp.pins.append(ConnectorPin(
            number=prec.get("Designator", ""),
            name=prec.get("Name", ""),
            net=net_name,
        ))

    return nets
