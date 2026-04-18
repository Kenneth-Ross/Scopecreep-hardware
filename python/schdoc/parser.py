from __future__ import annotations
import re
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

    # Build index: _stream_pos of RECORD=1 → Component.
    # Only include positions that produced a component (have a non-empty Designator child),
    # mirroring build_components's filter to avoid index-skew on title-block RECORD=1 entries.
    designated_positions = {
        int(r.get("OwnerIndex", -1))
        for r in records
        if r.get("RECORD") == "34" and r.get("Name") == "Designator" and r.get("Text")
    }
    comp_by_pos: dict[int, Component] = {}
    comp_iter = iter(components)
    for crec in records:
        if crec.get("RECORD") != "1":
            continue
        if crec["_stream_pos"] in designated_positions:
            comp = next(comp_iter, None)
            if comp:
                comp_by_pos[crec["_stream_pos"]] = comp

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


def extract_zones(
    records: list[dict],
    components: list[Component],
    comp_locations: dict[str, tuple[int, int]],
) -> list[Zone]:
    """Assign components to named functional zones via RECORD=43 bounding boxes."""
    zone_records = [r for r in records if r.get("RECORD") == "43"]

    zones: list[Zone] = []
    zone_bounds: list[tuple[int, int, int, int]] = []
    for zrec in zone_records:
        try:
            x1 = int(zrec["Location.X"])
            y1 = int(zrec["Location.Y"])
            x2 = int(zrec["Corner.X"])
            y2 = int(zrec["Corner.Y"])
        except (KeyError, ValueError):
            continue
        lx, rx = min(x1, x2), max(x1, x2)
        ly, ry = min(y1, y2), max(y1, y2)
        zones.append(Zone(name=zrec.get("Name", "Unknown")))
        zone_bounds.append((lx, ly, rx, ry))

    for comp in components:
        loc = comp_locations.get(comp.designator)
        if not loc:
            continue
        for zone, (lx, ly, rx, ry) in zip(zones, zone_bounds):
            if lx <= loc[0] <= rx and ly <= loc[1] <= ry:
                zone.components.append(comp)
                break

    assigned = {c.designator for z in zones for c in z.components}
    unassigned = [c for c in components if c.designator not in assigned]
    if unassigned:
        zones.append(Zone(name="Other", components=unassigned))

    return zones


def _parse_nominal_voltage(name: str) -> float | None:
    n = name.upper().strip()
    if n == "GND":
        return 0.0
    m = re.match(r"^(\d+)V(\d+)$", n)
    if m:
        return float(f"{m.group(1)}.{m.group(2)}")
    m = re.match(r"^(\d+\.?\d*)V$", n)
    if m:
        return float(m.group(1))
    return None


def _build_power_rails(
    records: list[dict], nets: dict[str, list[PinRef]]
) -> list[PowerRail]:
    power_names = {r["Text"] for r in records if r.get("RECORD") == "17" and r.get("Text")}
    rails: list[PowerRail] = []
    for name in sorted(power_names):
        refs = nets.get(name, [])
        source = next(
            (r.designator for r in refs if r.electrical_type == 2), None
        ) or next(
            (r.designator for r in refs if r.electrical_type == 1), None
        )
        loads = [r.designator for r in refs if r.designator != source]
        rails.append(PowerRail(
            name=name,
            nominal_voltage=_parse_nominal_voltage(name),
            source_designator=source,
            loads=sorted(set(loads)),
        ))
    return rails


def _build_probe_points(
    components: list[Component],
    nets: dict[str, list[PinRef]],
    power_rail_names: set[str],
) -> list[ProbePoint]:
    probes: list[ProbePoint] = []

    def _expected_range(net: str) -> str:
        n = net.upper()
        if n == "GND":
            return "0V"
        v = _parse_nominal_voltage(net)
        if v is not None:
            pct = "± 5%" if v <= 5 else "± 10%"
            return f"{v}V {pct}"
        if "CANH" in n or "CANL" in n:
            return "CAN differential 1.5–3.0V (active)"
        if "ADC" in n or "PEDAL" in n or "BRAKE" in n:
            return "0–3.3V analog"
        return "TBD"

    # Physical test points first (TP* prefix, or J* with "test" in description)
    for comp in components:
        desc = comp.description.lower()
        is_tp = comp.designator.upper().startswith("TP") or (
            comp.designator.startswith("J") and "test" in desc
        )
        if is_tp:
            for pin in comp.pins:
                probes.append(ProbePoint(
                    label=f"{comp.designator}.{pin.number}",
                    net=pin.net,
                    designator=comp.designator,
                    pin_name=pin.name,
                    pin_number=pin.number,
                    probe_type="physical_tp",
                    expected_range=_expected_range(pin.net),
                ))

    # Power rail source pins
    for net_name, refs in nets.items():
        if net_name not in power_rail_names:
            continue
        for ref in refs:
            if ref.electrical_type == 2:
                probes.append(ProbePoint(
                    label=f"{ref.designator}.{ref.pin_name}",
                    net=net_name,
                    designator=ref.designator,
                    pin_name=ref.pin_name,
                    pin_number=ref.pin_number,
                    probe_type="power_rail",
                    expected_range=_expected_range(net_name),
                ))

    # High-value signal nets
    for net_name, refs in nets.items():
        if net_name in power_rail_names:
            continue
        n = net_name.upper()
        if any(kw in n for kw in ("CANH", "CANL", "ADC", "PEDAL", "BRAKE", "RTD", "RTM")):
            for ref in refs[:1]:
                probes.append(ProbePoint(
                    label=f"{ref.designator}.{ref.pin_name}",
                    net=net_name,
                    designator=ref.designator,
                    pin_name=ref.pin_name,
                    pin_number=ref.pin_number,
                    probe_type="signal",
                    expected_range=_expected_range(net_name),
                ))

    return probes


def parse(path: str | Path) -> SchematicSummary:
    """Parse a .SchDoc file and return a SchematicSummary."""
    with olefile.OleFileIO(str(path)) as ole:
        data = ole.openstream("FileHeader").read()
        add_data = ole.openstream("Additional").read() if ole.exists("Additional") else b""

    records = parse_stream(data)
    if add_data:
        records += parse_stream(add_data)

    components = build_components(records)

    comp_locations: dict[str, tuple[int, int]] = {}
    designated_positions = {
        int(r.get("OwnerIndex", -1))
        for r in records
        if r.get("RECORD") == "34" and r.get("Name") == "Designator" and r.get("Text")
    }
    comp_iter = iter(components)
    for crec in records:
        if crec.get("RECORD") != "1" or crec["_stream_pos"] not in designated_positions:
            continue
        comp = next(comp_iter, None)
        if comp:
            pt = _xy(crec)
            if pt:
                comp_locations[comp.designator] = pt

    nets = resolve_nets(records, components)
    zones = extract_zones(records, components, comp_locations)

    power_rail_names = {r["Text"] for r in records if r.get("RECORD") == "17" and r.get("Text")}
    power_rails = _build_power_rails(records, nets)
    connectors = [c for c in components if c.is_connector]
    probe_points = _build_probe_points(components, nets, power_rail_names)

    return SchematicSummary(
        board_name=Path(path).stem,
        understanding="",
        power_rails=power_rails,
        zones=zones,
        connectors=connectors,
        probe_points=probe_points,
        nets=nets,
    )
