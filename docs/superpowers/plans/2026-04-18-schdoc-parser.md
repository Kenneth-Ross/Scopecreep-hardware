# SchDoc Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse Altium `.SchDoc` files into a structured Markdown summary (board understanding, power topology, functional blocks, connector pin maps, probe inventory) that gives an AI agent enough context to generate hardware tests.

**Architecture:** `olefile` opens the OLE container; a stream parser splits the `FileHeader` byte stream into pipe-delimited records; component assembly + coordinate-based union-find net resolution build a `SchematicSummary` dataclass; an Anthropic Haiku LLM pass writes the board understanding prose; a renderer emits the final Markdown to disk.

**Tech Stack:** Python 3.11+, `olefile`, `anthropic` SDK, `dataclasses`, `pathlib`, `struct`, `re`, `json`, `pytest`

---

## File Map

| Path | Role |
|------|------|
| `python/schdoc/__init__.py` | Package marker |
| `python/schdoc/models.py` | All dataclasses (`Component`, `ConnectorPin`, `PinRef`, `PowerRail`, `Zone`, `ProbePoint`, `SchematicSummary`) |
| `python/schdoc/parser.py` | OLE read → record parse → component build → net resolve → zone extract → `SchematicSummary` |
| `python/schdoc/llm.py` | Anthropic call → `understanding: str`; fallback to programmatic prose |
| `python/schdoc/renderer.py` | `SchematicSummary` → Markdown string |
| `python/schdoc/cli.py` | Argparse entrypoint: parse → LLM pass → render → write `.md` |
| `python/schdoc/tests/__init__.py` | Package marker |
| `python/schdoc/tests/test_parser.py` | Unit tests for record parsing, component build, net resolve, zone extract |
| `python/schdoc/tests/test_llm.py` | Unit tests for LLM pass (mocked client) + fallback |
| `python/schdoc/tests/test_renderer.py` | Unit tests for Markdown renderer sections |
| `python/schdoc/tests/test_integration.py` | Integration test against `Main.SchDoc` |
| `python/requirements.txt` | Add `olefile`, `anthropic` |

---

## Task 1: Scaffold and Models

**Files:**
- Create: `python/schdoc/__init__.py`
- Create: `python/schdoc/models.py`
- Create: `python/schdoc/tests/__init__.py`
- Modify: `python/requirements.txt`

- [ ] **Step 1: Add dependencies**

Edit `python/requirements.txt` to add at the end:
```
olefile>=0.47
anthropic>=0.40
```

- [ ] **Step 2: Write the failing import test**

Create `python/schdoc/tests/test_parser.py`:
```python
from schdoc.models import (
    ConnectorPin, PinRef, Component, PowerRail,
    Zone, ProbePoint, SchematicSummary,
)


def test_models_importable():
    pin = ConnectorPin(number="1", name="VIN", net="12V")
    assert pin.net == "12V"

    ref = PinRef(designator="U1", pin_name="VIN", pin_number="1", electrical_type=0)
    assert ref.designator == "U1"

    comp = Component(
        designator="U1", value="TPS62932", description="Buck converter",
        part_number="", manufacturer="", footprint="", is_connector=False, pins=[pin],
    )
    assert comp.is_connector is False

    rail = PowerRail(name="3V3", nominal_voltage=3.3, source_designator="U1", loads=["U2"])
    assert rail.nominal_voltage == 3.3

    zone = Zone(name="Power Tree", components=[comp], key_nets=["3V3"])
    assert zone.name == "Power Tree"

    probe = ProbePoint(
        label="J3", net="3V3", designator="J3", pin_name="1",
        pin_number="1", probe_type="physical_tp", expected_range="3.3V ± 5%",
    )
    assert probe.probe_type == "physical_tp"

    summary = SchematicSummary(
        board_name="Test Board", understanding="",
        power_rails=[rail], zones=[zone],
        connectors=[comp], probe_points=[probe], nets={},
    )
    assert summary.board_name == "Test Board"
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /home/alex/jbhack/python
python -m pytest schdoc/tests/test_parser.py::test_models_importable -v
```
Expected: `ModuleNotFoundError: No module named 'schdoc'`

- [ ] **Step 4: Create package scaffold**

Create `python/schdoc/__init__.py` (empty):
```python
```

Create `python/schdoc/tests/__init__.py` (empty):
```python
```

- [ ] **Step 5: Create models**

Create `python/schdoc/models.py`:
```python
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class ConnectorPin:
    number: str
    name: str
    net: str


@dataclass
class PinRef:
    designator: str
    pin_name: str
    pin_number: str
    electrical_type: int


@dataclass
class Component:
    designator: str
    value: str
    description: str
    part_number: str
    manufacturer: str
    footprint: str
    is_connector: bool
    pins: list[ConnectorPin] = field(default_factory=list)


@dataclass
class PowerRail:
    name: str
    nominal_voltage: float | None
    source_designator: str | None
    loads: list[str] = field(default_factory=list)


@dataclass
class Zone:
    name: str
    components: list[Component] = field(default_factory=list)
    key_nets: list[str] = field(default_factory=list)


@dataclass
class ProbePoint:
    label: str
    net: str
    designator: str
    pin_name: str
    pin_number: str
    probe_type: str   # "physical_tp" | "power_rail" | "signal"
    expected_range: str


@dataclass
class SchematicSummary:
    board_name: str
    understanding: str
    power_rails: list[PowerRail] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    connectors: list[Component] = field(default_factory=list)
    probe_points: list[ProbePoint] = field(default_factory=list)
    nets: dict[str, list[PinRef]] = field(default_factory=dict)
```

- [ ] **Step 6: Install dependencies and run test**

```bash
cd /home/alex/jbhack/python
pip install olefile anthropic
python -m pytest schdoc/tests/test_parser.py::test_models_importable -v
```
Expected: `PASSED`

- [ ] **Step 7: Commit**

```bash
cd /home/alex/jbhack
git add python/requirements.txt python/schdoc/
git commit -m "feat: scaffold schdoc package with data models"
```

---

## Task 2: Record Stream Parser

**Files:**
- Create: `python/schdoc/parser.py`
- Modify: `python/schdoc/tests/test_parser.py`

- [ ] **Step 1: Write failing tests for `parse_stream`**

Append to `python/schdoc/tests/test_parser.py`:
```python
import struct
from schdoc.parser import parse_stream


def _make_bytes(*records: str) -> bytes:
    """Pack strings as length-prefixed pipe-delimited record bytes."""
    out = b""
    for r in records:
        encoded = r.encode("latin-1")
        out += struct.pack("<I", len(encoded)) + encoded
    return out


def test_parse_stream_single_record():
    data = _make_bytes("|RECORD=1|ComponentDescription=Test IC|Location.X=100|Location.Y=200|")
    records = parse_stream(data)
    assert len(records) == 1
    assert records[0]["RECORD"] == "1"
    assert records[0]["ComponentDescription"] == "Test IC"
    assert records[0]["Location.X"] == "100"
    assert records[0]["_stream_pos"] == 0


def test_parse_stream_multiple_records():
    data = _make_bytes(
        "|RECORD=1|ComponentDescription=IC|Location.X=100|Location.Y=100|",
        "|RECORD=2|OwnerIndex=0|Name=VIN|Designator=1|Electrical=0|Location.X=110|Location.Y=100|",
    )
    records = parse_stream(data)
    assert len(records) == 2
    assert records[1]["RECORD"] == "2"
    assert records[1]["OwnerIndex"] == "0"
    assert records[1]["_stream_pos"] == 1


def test_parse_stream_skips_empty_records():
    data = _make_bytes(
        "|RECORD=31|HEADER=SchDoc|",
        "",
        "|RECORD=1|ComponentDescription=IC|Location.X=50|Location.Y=50|",
    )
    records = parse_stream(data)
    assert len(records) == 2
    assert records[0]["RECORD"] == "31"
    assert records[1]["RECORD"] == "1"
    assert records[1]["_stream_pos"] == 2
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/alex/jbhack/python
python -m pytest schdoc/tests/test_parser.py -k "parse_stream" -v
```
Expected: `ImportError: cannot import name 'parse_stream'`

- [ ] **Step 3: Implement `parse_stream`**

Create `python/schdoc/parser.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd /home/alex/jbhack/python
python -m pytest schdoc/tests/test_parser.py -k "parse_stream" -v
```
Expected: all 3 `parse_stream` tests `PASSED`

- [ ] **Step 5: Commit**

```bash
cd /home/alex/jbhack
git add python/schdoc/parser.py python/schdoc/tests/test_parser.py
git commit -m "feat: implement OLE record stream parser"
```

---

## Task 3: Component Assembly

**Files:**
- Modify: `python/schdoc/parser.py`
- Modify: `python/schdoc/tests/test_parser.py`

- [ ] **Step 1: Write failing tests**

Append to `python/schdoc/tests/test_parser.py`:
```python
from schdoc.parser import parse_stream, build_components


def test_build_components_basic():
    data = _make_bytes(
        "|RECORD=1|ComponentDescription=Buck Converter|Location.X=100|Location.Y=200|",
        "|RECORD=34|OwnerIndex=0|Name=Designator|Text=U1|",
        "|RECORD=41|OwnerIndex=0|Name=Comment|Text=TPS62932|",
        "|RECORD=41|OwnerIndex=0|Name=Part Number|Text=TPS62932DRLR|",
        "|RECORD=41|OwnerIndex=0|Name=Manufacturer|Text=TI|",
        "|RECORD=45|OwnerIndex=0|ModelDatafileEntity0=FP-DRL0008A|",
    )
    records = parse_stream(data)
    components = build_components(records)
    assert len(components) == 1
    c = components[0]
    assert c.designator == "U1"
    assert c.value == "TPS62932"
    assert c.description == "Buck Converter"
    assert c.part_number == "TPS62932DRLR"
    assert c.manufacturer == "TI"
    assert c.footprint == "FP-DRL0008A"
    assert c.is_connector is False


def test_build_components_connector_detection():
    data = _make_bytes(
        "|RECORD=1|ComponentDescription=Molex Microfit 12-pos|Location.X=10|Location.Y=10|",
        "|RECORD=34|OwnerIndex=0|Name=Designator|Text=J1|",
        "|RECORD=41|OwnerIndex=0|Name=Comment|Text=436450400|",
    )
    records = parse_stream(data)
    components = build_components(records)
    assert components[0].is_connector is True


def test_build_components_multi():
    data = _make_bytes(
        "|RECORD=1|ComponentDescription=IC A|Location.X=10|Location.Y=10|",
        "|RECORD=34|OwnerIndex=0|Name=Designator|Text=U1|",
        "|RECORD=41|OwnerIndex=0|Name=Comment|Text=PartA|",
        "|RECORD=1|ComponentDescription=IC B|Location.X=200|Location.Y=200|",
        "|RECORD=34|OwnerIndex=3|Name=Designator|Text=U2|",
        "|RECORD=41|OwnerIndex=3|Name=Comment|Text=PartB|",
    )
    records = parse_stream(data)
    components = build_components(records)
    assert len(components) == 2
    assert components[0].designator == "U1"
    assert components[1].designator == "U2"
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/alex/jbhack/python
python -m pytest schdoc/tests/test_parser.py -k "build_components" -v
```
Expected: `ImportError: cannot import name 'build_components'`

- [ ] **Step 3: Implement `build_components`**

Append to `python/schdoc/parser.py` (after `parse_stream`):
```python
_CONNECTOR_KEYWORDS = {"connector", "molex", "header", "socket", "plug"}


def _is_connector(designator: str, description: str, footprint: str) -> bool:
    if designator.upper().startswith("J"):
        return True
    text = (description + " " + footprint).lower()
    return any(kw in text for kw in _CONNECTOR_KEYWORDS)


def build_components(records: list[dict]) -> list[Component]:
    """Build Component objects from parsed records using OwnerIndex parent-child linking."""
    # Index RECORD=1 entries by stream_pos
    comp_records: dict[int, dict] = {
        rec["_stream_pos"]: rec
        for rec in records
        if rec.get("RECORD") == "1"
    }

    # For each component record, collect its child records
    children: dict[int, list[dict]] = {pos: [] for pos in comp_records}
    for rec in records:
        if rec.get("RECORD") == "1":
            continue
        owner_idx = rec.get("OwnerIndex")
        if owner_idx is None:
            continue
        parent_pos = int(owner_idx)   # direct equality: OwnerIndex == parent _stream_pos
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
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd /home/alex/jbhack/python
python -m pytest schdoc/tests/test_parser.py -k "build_components" -v
```
Expected: all 3 `build_components` tests `PASSED`

- [ ] **Step 5: Commit**

```bash
cd /home/alex/jbhack
git add python/schdoc/parser.py python/schdoc/tests/test_parser.py
git commit -m "feat: implement component assembly from schematic records"
```

---

## Task 4: Net Resolution

**Files:**
- Modify: `python/schdoc/parser.py`
- Modify: `python/schdoc/tests/test_parser.py`

- [ ] **Step 1: Write failing tests**

Append to `python/schdoc/tests/test_parser.py`:
```python
from schdoc.parser import parse_stream, build_components, resolve_nets


def test_resolve_nets_simple_wire():
    # Wire from (100,100) to (200,100), pin at (200,100), net label at (100,100)
    data = _make_bytes(
        # Component + pin at (200, 100)
        "|RECORD=1|ComponentDescription=IC|Location.X=220|Location.Y=100|",
        "|RECORD=34|OwnerIndex=0|Name=Designator|Text=U1|",
        "|RECORD=41|OwnerIndex=0|Name=Comment|Text=IC|",
        "|RECORD=2|OwnerIndex=0|Name=VIN|Designator=1|Electrical=0|Location.X=200|Location.Y=100|",
        # Wire: (100,100)-(200,100)
        "|RECORD=27|LocationCount=2|X1=100|Y1=100|X2=200|Y2=100|",
        # Net label at (100,100)
        "|RECORD=25|Text=12V|Location.X=100|Location.Y=100|",
    )
    records = parse_stream(data)
    components = build_components(records)
    nets = resolve_nets(records, components)
    assert "12V" in nets
    pins_on_12v = nets["12V"]
    assert any(p.designator == "U1" and p.pin_name == "VIN" for p in pins_on_12v)


def test_resolve_nets_power_port():
    data = _make_bytes(
        "|RECORD=1|ComponentDescription=IC|Location.X=120|Location.Y=100|",
        "|RECORD=34|OwnerIndex=0|Name=Designator|Text=U1|",
        "|RECORD=41|OwnerIndex=0|Name=Comment|Text=IC|",
        "|RECORD=2|OwnerIndex=0|Name=GND|Designator=2|Electrical=7|Location.X=100|Location.Y=100|",
        "|RECORD=17|Text=GND|Location.X=100|Location.Y=100|",
    )
    records = parse_stream(data)
    components = build_components(records)
    nets = resolve_nets(records, components)
    assert "GND" in nets
    assert any(p.designator == "U1" for p in nets["GND"])


def test_resolve_nets_unnamed_cluster():
    # Two pins connected by wire, no label
    data = _make_bytes(
        "|RECORD=1|ComponentDescription=R|Location.X=120|Location.Y=100|",
        "|RECORD=34|OwnerIndex=0|Name=Designator|Text=R1|",
        "|RECORD=41|OwnerIndex=0|Name=Comment|Text=10k|",
        "|RECORD=2|OwnerIndex=0|Name=1|Designator=1|Electrical=4|Location.X=100|Location.Y=100|",
        "|RECORD=1|ComponentDescription=R|Location.X=220|Location.Y=100|",
        "|RECORD=34|OwnerIndex=4|Name=Designator|Text=R2|",
        "|RECORD=41|OwnerIndex=4|Name=Comment|Text=10k|",
        "|RECORD=2|OwnerIndex=4|Name=1|Designator=1|Electrical=4|Location.X=200|Location.Y=100|",
        "|RECORD=27|LocationCount=2|X1=100|Y1=100|X2=200|Y2=100|",
    )
    records = parse_stream(data)
    components = build_components(records)
    nets = resolve_nets(records, components)
    # One unnamed net connecting R1 and R2
    unnamed = [n for n in nets if n.startswith("NET_")]
    assert len(unnamed) == 1
    refs = nets[unnamed[0]]
    designators = {r.designator for r in refs}
    assert designators == {"R1", "R2"}
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/alex/jbhack/python
python -m pytest schdoc/tests/test_parser.py -k "resolve_nets" -v
```
Expected: `ImportError: cannot import name 'resolve_nets'`

- [ ] **Step 3: Implement `resolve_nets`**

Append to `python/schdoc/parser.py`:
```python
class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[tuple[int, int], tuple[int, int]] = {}

    def find(self, x: tuple[int, int]) -> tuple[int, int]:
        if x not in self._parent:
            self._parent[x] = x
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, a: tuple[int, int], b: tuple[int, int]) -> None:
        self._parent[self.find(a)] = self.find(b)

    def roots(self) -> set[tuple[int, int]]:
        return {self.find(k) for k in self._parent}


def _xy(rec: dict, prefix: str = "Location") -> tuple[int, int] | None:
    try:
        return (int(rec[f"{prefix}.X"]), int(rec[f"{prefix}.Y"]))
    except (KeyError, ValueError):
        return None


def resolve_nets(
    records: list[dict], components: list[Component]
) -> dict[str, list[PinRef]]:
    """
    Build a net→PinRef map using coordinate union-find.
    Also populates Component.pins with ConnectorPin objects.
    """
    uf = _UnionFind()

    # Collect named points (power ports, net labels)
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

    # Junctions — just register as nodes
    for rec in records:
        if rec.get("RECORD") == "29":
            pt = _xy(rec)
            if pt:
                uf.find(pt)

    # Build index: RECORD=1 stream_pos → (component, raw pins)
    comp_by_pos: dict[int, Component] = {}
    comp_records_map: dict[int, dict] = {
        rec["_stream_pos"]: rec
        for rec in records
        if rec.get("RECORD") == "1"
    }
    comp_list = list(comp_records_map.items())

    # Match components (by order) to build_components output
    for idx, (pos, _) in enumerate(comp_list):
        if idx < len(components):
            comp_by_pos[pos] = components[idx]

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

    # Register pin locations in union-find
    for comp, prec in pin_records:
        pt = _xy(prec)
        if pt:
            uf.find(pt)

    # Union pin points with any coincident wire/label points already registered
    # (they self-union if isolated; wire endpoints already connected above)

    # Name clusters
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
        uf.find(pt)  # ensure registered
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
```

- [ ] **Step 4: Run tests**

```bash
cd /home/alex/jbhack/python
python -m pytest schdoc/tests/test_parser.py -k "resolve_nets" -v
```
Expected: all 3 `resolve_nets` tests `PASSED`

- [ ] **Step 5: Commit**

```bash
cd /home/alex/jbhack
git add python/schdoc/parser.py python/schdoc/tests/test_parser.py
git commit -m "feat: implement coordinate union-find net resolution"
```

---

## Task 5: Zone Extraction and `parse()` Entrypoint

**Files:**
- Modify: `python/schdoc/parser.py`
- Modify: `python/schdoc/tests/test_parser.py`

- [ ] **Step 1: Write failing tests**

Append to `python/schdoc/tests/test_parser.py`:
```python
from schdoc.parser import parse_stream, build_components, extract_zones


def test_extract_zones_assigns_component():
    # Component at (150, 150), zone bbox (100,100)-(200,200)
    records = [
        {"RECORD": "43", "_stream_pos": 0, "Name": "Power Tree",
         "Location.X": "100", "Location.Y": "100",
         "Corner.X": "200", "Corner.Y": "200"},
    ]
    comp = Component(
        designator="U1", value="TPS62932", description="Buck",
        part_number="", manufacturer="", footprint="",
        is_connector=False, pins=[],
    )
    # Manually set location on component for testing via a helper attribute
    zones = extract_zones(records, [comp], {
        "U1": (150, 150),
    })
    assert len(zones) == 1
    assert zones[0].name == "Power Tree"
    assert any(c.designator == "U1" for c in zones[0].components)


def test_extract_zones_other_group():
    records = []  # no zone annotations
    comp = Component(
        designator="U1", value="IC", description="",
        part_number="", manufacturer="", footprint="",
        is_connector=False, pins=[],
    )
    zones = extract_zones(records, [comp], {"U1": (50, 50)})
    assert len(zones) == 1
    assert zones[0].name == "Other"
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/alex/jbhack/python
python -m pytest schdoc/tests/test_parser.py -k "extract_zones" -v
```
Expected: `ImportError: cannot import name 'extract_zones'`

- [ ] **Step 3: Implement `extract_zones` and `parse()`**

Append to `python/schdoc/parser.py`:
```python
def extract_zones(
    records: list[dict],
    components: list[Component],
    comp_locations: dict[str, tuple[int, int]],
) -> list[Zone]:
    """Assign components to named functional zones via RECORD=43 bounding boxes."""
    zone_records = [r for r in records if r.get("RECORD") == "43"]

    zones: list[Zone] = []
    for zrec in zone_records:
        try:
            x1 = int(zrec["Location.X"])
            y1 = int(zrec["Location.Y"])
            x2 = int(zrec["Corner.X"])
            y2 = int(zrec["Corner.Y"])
        except (KeyError, ValueError):
            continue
        # Normalize so (x1,y1) is top-left
        lx, rx = min(x1, x2), max(x1, x2)
        ly, ry = min(y1, y2), max(y1, y2)
        zones.append(Zone(name=zrec.get("Name", "Unknown")))
        zone_bounds = (lx, ly, rx, ry)
        for comp in components:
            loc = comp_locations.get(comp.designator)
            if loc and lx <= loc[0] <= rx and ly <= loc[1] <= ry:
                zones[-1].components.append(comp)

    assigned = {c.designator for z in zones for c in z.components}
    unassigned = [c for c in components if c.designator not in assigned]
    if unassigned:
        zones.append(Zone(name="Other", components=unassigned))

    return zones


def _parse_nominal_voltage(name: str) -> float | None:
    import re
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
    import re
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

    # Physical test points first
    for comp in components:
        desc = comp.description.lower()
        if comp.designator.startswith("J") and "test" in desc:
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

    # Power rail key nodes (source component pins on power rails)
    for net_name, refs in nets.items():
        if net_name not in power_rail_names:
            continue
        for ref in refs:
            if ref.electrical_type == 2:  # Output pin driving the rail
                probes.append(ProbePoint(
                    label=f"{ref.designator}.{ref.pin_name}",
                    net=net_name,
                    designator=ref.designator,
                    pin_name=ref.pin_name,
                    pin_number=ref.pin_number,
                    probe_type="power_rail",
                    expected_range=_expected_range(net_name),
                ))

    # Signal nets (CAN, ADC, etc.)
    for net_name, refs in nets.items():
        if net_name in power_rail_names:
            continue
        n = net_name.upper()
        if any(kw in n for kw in ("CANH", "CANL", "ADC", "PEDAL", "BRAKE", "RTD", "RTM")):
            for ref in refs[:1]:  # one representative probe point per signal net
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
    ole = olefile.OleFileIO(str(path))
    data = ole.openstream("FileHeader").read()
    ole.close()

    records = parse_stream(data)

    # Also read Additional stream for RECORD=43 zone bounding boxes if needed
    if ole.exists("Additional"):  # type: ignore[attr-defined]
        # Re-open to read Additional
        ole2 = olefile.OleFileIO(str(path))
        add_data = ole2.openstream("Additional").read()
        ole2.close()
        add_records = parse_stream(add_data)
        # RECORD=225 may contain bounding boxes for named zones
        records = records + add_records

    components = build_components(records)

    # Build component location map from RECORD=1
    comp_locations: dict[str, tuple[int, int]] = {}
    comp_rec_list = [r for r in records if r.get("RECORD") == "1"]
    for idx, crec in enumerate(comp_rec_list):
        if idx < len(components):
            pt = _xy(crec)
            if pt:
                comp_locations[components[idx].designator] = pt

    nets = resolve_nets(records, components)
    zones = extract_zones(records, components, comp_locations)

    power_rail_names = {r["Text"] for r in records if r.get("RECORD") == "17" and r.get("Text")}
    power_rails = _build_power_rails(records, nets)
    connectors = [c for c in components if c.is_connector]
    probe_points = _build_probe_points(components, nets, power_rail_names)

    board_name = Path(path).stem

    return SchematicSummary(
        board_name=board_name,
        understanding="",
        power_rails=power_rails,
        zones=zones,
        connectors=connectors,
        probe_points=probe_points,
        nets=nets,
    )
```

**Note:** The `parse()` function re-opens the OLE file to read the `Additional` stream after calling `ole.close()`. Fix this by using a context manager — open once, read both streams:

```python
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
    comp_rec_list = [r for r in records if r.get("RECORD") == "1"]
    for idx, crec in enumerate(comp_rec_list):
        if idx < len(components):
            pt = _xy(crec)
            if pt:
                comp_locations[components[idx].designator] = pt

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
```

- [ ] **Step 4: Run tests**

```bash
cd /home/alex/jbhack/python
python -m pytest schdoc/tests/test_parser.py -k "extract_zones" -v
```
Expected: both `extract_zones` tests `PASSED`

- [ ] **Step 5: Commit**

```bash
cd /home/alex/jbhack
git add python/schdoc/parser.py python/schdoc/tests/test_parser.py
git commit -m "feat: implement zone extraction and parse() entrypoint"
```

---

## Task 6: Integration Test Against Main.SchDoc

**Files:**
- Create: `python/schdoc/tests/test_integration.py`

- [ ] **Step 1: Write integration test**

Create `python/schdoc/tests/test_integration.py`:
```python
"""Integration test against the real Main.SchDoc file."""
import pytest
from pathlib import Path

SCHDOC = Path(__file__).parents[3] / "Main.SchDoc"


@pytest.mark.skipif(not SCHDOC.exists(), reason="Main.SchDoc not present")
def test_parse_main_schdoc_components():
    from schdoc.parser import parse
    summary = parse(SCHDOC)
    designators = {c.designator for c in summary.connectors + [
        c for z in summary.zones for c in z.components
    ]}
    # Known components from schematic research
    assert "U1" in designators   # TPS62932 buck converter
    assert "U2" in designators   # TCAN332DCNR CAN transceiver
    assert "J1" in designators   # Molex connector


@pytest.mark.skipif(not SCHDOC.exists(), reason="Main.SchDoc not present")
def test_parse_main_schdoc_power_rails():
    from schdoc.parser import parse
    summary = parse(SCHDOC)
    rail_names = {r.name for r in summary.power_rails}
    assert "3V3" in rail_names
    assert "GND" in rail_names


@pytest.mark.skipif(not SCHDOC.exists(), reason="Main.SchDoc not present")
def test_parse_main_schdoc_zones():
    from schdoc.parser import parse
    summary = parse(SCHDOC)
    zone_names = {z.name for z in summary.zones}
    assert "CAN" in zone_names or len(zone_names) >= 1


@pytest.mark.skipif(not SCHDOC.exists(), reason="Main.SchDoc not present")
def test_parse_main_schdoc_j1_connector_pins():
    from schdoc.parser import parse
    summary = parse(SCHDOC)
    j1 = next((c for c in summary.connectors if c.designator == "J1"), None)
    assert j1 is not None, "J1 connector not found"
    assert len(j1.pins) > 0, "J1 has no pins"
    pin_nets = {p.net for p in j1.pins}
    # J1 should have at least GND and a power/signal net
    assert len(pin_nets) > 1
```

- [ ] **Step 2: Run integration tests**

```bash
cd /home/alex/jbhack/python
python -m pytest schdoc/tests/test_integration.py -v
```
Expected: all 4 tests `PASSED`. If any fail, debug the parser against `Main.SchDoc` by inspecting raw records:

```python
# Debug helper — run interactively:
import olefile, struct
ole = olefile.OleFileIO("../Main.SchDoc")
data = ole.openstream("FileHeader").read()
from schdoc.parser import parse_stream
records = parse_stream(data)
# Find RECORD=43 entries
zones = [r for r in records if r.get("RECORD") == "43"]
print(zones)
```

- [ ] **Step 3: Commit**

```bash
cd /home/alex/jbhack
git add python/schdoc/tests/test_integration.py
git commit -m "test: add integration tests against Main.SchDoc"
```

---

## Task 7: LLM Pass (Anthropic Haiku)

**Files:**
- Create: `python/schdoc/llm.py`
- Create: `python/schdoc/tests/test_llm.py`

- [ ] **Step 1: Write failing tests**

Create `python/schdoc/tests/test_llm.py`:
```python
import json
from unittest.mock import MagicMock, patch
from schdoc.models import SchematicSummary, PowerRail, Zone, Component, ProbePoint
from schdoc.llm import generate_understanding, _fallback_understanding


def _make_summary() -> SchematicSummary:
    rail = PowerRail(name="3V3", nominal_voltage=3.3, source_designator="U1", loads=["U2"])
    comp = Component(
        designator="U1", value="TPS62932", description="Synchronous buck converter",
        part_number="", manufacturer="TI", footprint="", is_connector=False, pins=[],
    )
    zone = Zone(name="Power Tree", components=[comp], key_nets=["3V3"])
    probe = ProbePoint(
        label="J3.1", net="3V3", designator="J3", pin_name="1",
        pin_number="1", probe_type="physical_tp", expected_range="3.3V ± 5%",
    )
    return SchematicSummary(
        board_name="Main",
        understanding="",
        power_rails=[rail],
        zones=[zone],
        connectors=[],
        probe_points=[probe],
        nets={},
    )


def test_fallback_understanding_contains_board_name():
    summary = _make_summary()
    result = _fallback_understanding(summary)
    assert "Main" in result or "3V3" in result


def test_fallback_understanding_mentions_zones():
    summary = _make_summary()
    result = _fallback_understanding(summary)
    assert "Power Tree" in result


def test_generate_understanding_uses_llm(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    mock_client = MagicMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="This board is a vehicle interface.")]
    mock_client.messages.create.return_value = mock_msg

    with patch("schdoc.llm.anthropic.Anthropic", return_value=mock_client):
        summary = _make_summary()
        result = generate_understanding(summary)

    assert result == "This board is a vehicle interface."
    mock_client.messages.create.assert_called_once()
    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-haiku-4-5-20251001"
    assert call_kwargs["max_tokens"] == 512


def test_generate_understanding_falls_back_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    summary = _make_summary()
    result = generate_understanding(summary)
    # Should return non-empty fallback prose
    assert len(result) > 10


def test_generate_understanding_falls_back_on_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with patch("schdoc.llm.anthropic.Anthropic", side_effect=Exception("API down")):
        summary = _make_summary()
        result = generate_understanding(summary)
    assert len(result) > 10
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/alex/jbhack/python
python -m pytest schdoc/tests/test_llm.py -v
```
Expected: `ImportError: No module named 'schdoc.llm'`

- [ ] **Step 3: Implement `llm.py`**

Create `python/schdoc/llm.py`:
```python
from __future__ import annotations
import json
import os

import anthropic

from .models import SchematicSummary


_SYSTEM = (
    "You are a hardware engineer. Given structured schematic data, "
    "write a concise 3-5 sentence board overview a test engineer can use "
    "to understand the board before generating hardware tests. "
    "Be specific: name components, voltages, and signal types. "
    "Do not speculate beyond the data provided."
)


def _build_context(summary: SchematicSummary) -> str:
    all_components = [c for z in summary.zones for c in z.components] + summary.connectors
    seen = set()
    unique_components = []
    for c in all_components:
        if c.designator not in seen:
            seen.add(c.designator)
            unique_components.append(c)

    return json.dumps({
        "board_name": summary.board_name,
        "components": [
            {"designator": c.designator, "value": c.value, "description": c.description}
            for c in unique_components
        ],
        "power_rails": [
            {
                "name": r.name,
                "nominal_voltage": r.nominal_voltage,
                "source": r.source_designator,
                "loads": r.loads,
            }
            for r in summary.power_rails
        ],
        "zones": [
            {"name": z.name, "components": [c.designator for c in z.components]}
            for z in summary.zones
        ],
        "probe_points": [
            {"label": p.label, "net": p.net, "type": p.probe_type, "expected": p.expected_range}
            for p in summary.probe_points
        ],
    }, indent=2)


def _fallback_understanding(summary: SchematicSummary) -> str:
    parts = [f"Board: {summary.board_name}."]
    non_gnd = [r for r in summary.power_rails if r.name.upper() != "GND"]
    if non_gnd:
        rail_strs = [
            f"{r.name} ({r.nominal_voltage}V)" if r.nominal_voltage is not None else r.name
            for r in non_gnd
        ]
        parts.append(f"Power rails: {', '.join(rail_strs)}.")
    non_other = [z for z in summary.zones if z.name != "Other"]
    if non_other:
        parts.append(f"Functional blocks: {', '.join(z.name for z in non_other)}.")
    if summary.connectors:
        parts.append(
            f"Connectors: {', '.join(c.designator for c in summary.connectors)} "
            f"({len(summary.connectors)} total)."
        )
    physical_tps = [p for p in summary.probe_points if p.probe_type == "physical_tp"]
    if physical_tps:
        parts.append(f"Physical test points: {', '.join(p.label for p in physical_tps)}.")
    return " ".join(parts)


def generate_understanding(summary: SchematicSummary) -> str:
    """Call Anthropic Haiku to generate board understanding prose. Falls back to programmatic summary."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _fallback_understanding(summary)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=_SYSTEM,
            messages=[{"role": "user", "content": _build_context(summary)}],
        )
        return message.content[0].text
    except Exception:
        return _fallback_understanding(summary)
```

- [ ] **Step 4: Run tests**

```bash
cd /home/alex/jbhack/python
python -m pytest schdoc/tests/test_llm.py -v
```
Expected: all 5 `test_llm` tests `PASSED`

- [ ] **Step 5: Commit**

```bash
cd /home/alex/jbhack
git add python/schdoc/llm.py python/schdoc/tests/test_llm.py
git commit -m "feat: implement Anthropic Haiku LLM pass for board understanding"
```

---

## Task 8: Markdown Renderer

**Files:**
- Create: `python/schdoc/renderer.py`
- Create: `python/schdoc/tests/test_renderer.py`

- [ ] **Step 1: Write failing tests**

Create `python/schdoc/tests/test_renderer.py`:
```python
from schdoc.models import (
    SchematicSummary, PowerRail, Zone, Component,
    ConnectorPin, ProbePoint,
)
from schdoc.renderer import render


def _make_full_summary() -> SchematicSummary:
    pin = ConnectorPin(number="1", name="12V_IN", net="12V")
    gnd_pin = ConnectorPin(number="2", name="GND", net="GND")
    connector = Component(
        designator="J1", value="436450400", description="Molex Microfit 12-pos",
        part_number="436450400", manufacturer="Molex", footprint="FP-MOLEX",
        is_connector=True, pins=[pin, gnd_pin],
    )
    ic = Component(
        designator="U1", value="TPS62932", description="Synchronous buck converter",
        part_number="TPS62932DRLR", manufacturer="TI", footprint="FP-DRL",
        is_connector=False, pins=[],
    )
    rail_12v = PowerRail(name="12V", nominal_voltage=12.0, source_designator=None, loads=["U1"])
    rail_3v3 = PowerRail(name="3V3", nominal_voltage=3.3, source_designator="U1", loads=["U2", "U3"])
    rail_gnd = PowerRail(name="GND", nominal_voltage=0.0, source_designator=None, loads=["U1", "J1"])
    zone = Zone(name="Power Tree", components=[ic], key_nets=["3V3"])
    conn_zone = Zone(name="Connectors", components=[connector], key_nets=["12V"])
    probe_tp = ProbePoint(
        label="J3.1", net="3V3", designator="J3", pin_name="1",
        pin_number="1", probe_type="physical_tp", expected_range="3.3V ± 5%",
    )
    probe_rail = ProbePoint(
        label="U1.SW", net="3V3", designator="U1", pin_name="SW",
        pin_number="5", probe_type="power_rail", expected_range="3.3V ± 5%",
    )
    return SchematicSummary(
        board_name="Main",
        understanding="This is a vehicle interface board with CAN transceivers and a 12V→3.3V buck converter.",
        power_rails=[rail_12v, rail_3v3, rail_gnd],
        zones=[zone, conn_zone],
        connectors=[connector],
        probe_points=[probe_tp, probe_rail],
        nets={},
    )


def test_render_contains_all_sections():
    md = render(_make_full_summary())
    assert "## Board Understanding" in md
    assert "## Power Topology" in md
    assert "## Functional Blocks" in md
    assert "## Probe Inventory" in md


def test_render_board_understanding_prose():
    md = render(_make_full_summary())
    assert "vehicle interface board" in md


def test_render_power_topology_rails():
    md = render(_make_full_summary())
    assert "12V" in md
    assert "3V3" in md
    assert "3.3V" in md
    assert "U1" in md  # source of 3V3


def test_render_connector_pin_table():
    md = render(_make_full_summary())
    assert "J1" in md
    assert "Molex Microfit 12-pos" in md
    assert "12V_IN" in md
    assert "12V" in md
    # Table format
    assert "| Pin |" in md or "|Pin|" in md


def test_render_probe_inventory_table():
    md = render(_make_full_summary())
    assert "J3.1" in md
    assert "physical_tp" in md
    assert "3.3V ± 5%" in md
    assert "U1.SW" in md
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/alex/jbhack/python
python -m pytest schdoc/tests/test_renderer.py -v
```
Expected: `ImportError: No module named 'schdoc.renderer'`

- [ ] **Step 3: Implement `renderer.py`**

Create `python/schdoc/renderer.py`:
```python
from __future__ import annotations
from .models import SchematicSummary, PowerRail, Zone, Component


def render(summary: SchematicSummary) -> str:
    sections = [
        _section_understanding(summary),
        _section_power_topology(summary),
        _section_functional_blocks(summary),
        _section_probe_inventory(summary),
    ]
    return "\n\n".join(sections)


def _section_understanding(summary: SchematicSummary) -> str:
    lines = [f"# {summary.board_name} — Schematic Summary", "", "## Board Understanding", ""]
    lines.append(summary.understanding or "_No board understanding available._")
    return "\n".join(lines)


def _section_power_topology(summary: SchematicSummary) -> str:
    lines = ["## Power Topology", ""]
    for rail in summary.power_rails:
        v = f"{rail.nominal_voltage}V" if rail.nominal_voltage is not None else "unknown voltage"
        lines.append(f"### {rail.name}")
        lines.append(f"- **Nominal:** {v}")
        if rail.source_designator:
            lines.append(f"- **Source:** {rail.source_designator}")
        else:
            lines.append("- **Source:** external / unresolved")
        if rail.loads:
            lines.append("- **Loads:**")
            for load in rail.loads:
                lines.append(f"  - {load}")
        lines.append("")
    return "\n".join(lines)


def _section_functional_blocks(summary: SchematicSummary) -> str:
    lines = ["## Functional Blocks", ""]
    for zone in summary.zones:
        lines.append(f"### {zone.name}")
        lines.append("")
        for comp in zone.components:
            if comp.is_connector:
                lines += _connector_table(comp)
            else:
                desc = comp.description or comp.value
                lines.append(f"- **{comp.designator}** ({comp.value}): {desc}")
        lines.append("")
    return "\n".join(lines)


def _connector_table(comp: Component) -> list[str]:
    desc = comp.description or comp.value
    lines = [
        f"#### {comp.designator} — {desc}",
        "",
        "| Pin | Net | Signal |",
        "|-----|-----|--------|",
    ]
    for pin in sorted(comp.pins, key=lambda p: _pin_sort_key(p.number)):
        lines.append(f"| {pin.number} | {pin.net} | {pin.name} |")
    lines.append("")
    return lines


def _pin_sort_key(number: str) -> int:
    try:
        return int(number)
    except ValueError:
        return 9999


def _section_probe_inventory(summary: SchematicSummary) -> str:
    lines = [
        "## Probe Inventory",
        "",
        "| Probe Point | Net | Component.Pin | Type | Expected Range |",
        "|-------------|-----|--------------|------|----------------|",
    ]
    # Physical TPs first, then power_rail, then signal
    order = {"physical_tp": 0, "power_rail": 1, "signal": 2}
    sorted_probes = sorted(summary.probe_points, key=lambda p: order.get(p.probe_type, 3))
    for probe in sorted_probes:
        lines.append(
            f"| {probe.label} | {probe.net} | {probe.designator}.{probe.pin_name} "
            f"| {probe.probe_type} | {probe.expected_range} |"
        )
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

```bash
cd /home/alex/jbhack/python
python -m pytest schdoc/tests/test_renderer.py -v
```
Expected: all 5 renderer tests `PASSED`

- [ ] **Step 5: Commit**

```bash
cd /home/alex/jbhack
git add python/schdoc/renderer.py python/schdoc/tests/test_renderer.py
git commit -m "feat: implement 4-section markdown renderer"
```

---

## Task 9: CLI + Full Pipeline Test

**Files:**
- Create: `python/schdoc/cli.py`

- [ ] **Step 1: Write failing CLI test**

Append to `python/schdoc/tests/test_integration.py`:
```python
import subprocess, sys
from pathlib import Path

SCHDOC = Path(__file__).parents[3] / "Main.SchDoc"


@pytest.mark.skipif(not SCHDOC.exists(), reason="Main.SchDoc not present")
def test_cli_writes_markdown(tmp_path):
    out = tmp_path / "output.md"
    result = subprocess.run(
        [sys.executable, "-m", "schdoc.cli", str(SCHDOC), "--output", str(out)],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert out.exists()
    content = out.read_text()
    assert "## Board Understanding" in content
    assert "## Power Topology" in content
    assert "## Functional Blocks" in content
    assert "## Probe Inventory" in content
    assert "3V3" in content or "GND" in content


@pytest.mark.skipif(not SCHDOC.exists(), reason="Main.SchDoc not present")
def test_cli_default_output_path(tmp_path):
    import shutil
    schdoc_copy = tmp_path / "Main.SchDoc"
    shutil.copy(SCHDOC, schdoc_copy)
    result = subprocess.run(
        [sys.executable, "-m", "schdoc.cli", str(schdoc_copy)],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    default_out = tmp_path / "Main.schematic_summary.md"
    assert default_out.exists()
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/alex/jbhack/python
python -m pytest schdoc/tests/test_integration.py -k "cli" -v
```
Expected: `ModuleNotFoundError: No module named 'schdoc.cli'` or similar

- [ ] **Step 3: Implement `cli.py`**

Create `python/schdoc/cli.py`:
```python
"""Entry point: python -m schdoc.cli <path.SchDoc> [--output <path>]"""
from __future__ import annotations
import argparse
from pathlib import Path

from .parser import parse
from .llm import generate_understanding
from .renderer import render


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse Altium .SchDoc to Markdown summary")
    parser.add_argument("schdoc", help="Path to .SchDoc file")
    parser.add_argument("--output", "-o", help="Output .md path (default: <schdoc>.schematic_summary.md)")
    args = parser.parse_args()

    schdoc_path = Path(args.schdoc)
    output_path = Path(args.output) if args.output else schdoc_path.with_suffix(".schematic_summary.md")

    print(f"Parsing {schdoc_path.name}...")
    summary = parse(schdoc_path)

    print("Generating board understanding...")
    summary.understanding = generate_understanding(summary)

    print("Rendering markdown...")
    md = render(summary)

    output_path.write_text(md, encoding="utf-8")
    print(f"Written to {output_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run all tests**

```bash
cd /home/alex/jbhack/python
python -m pytest schdoc/tests/ -v
```
Expected: all tests `PASSED` (integration tests skip if `Main.SchDoc` absent)

- [ ] **Step 5: Run CLI manually against Main.SchDoc**

```bash
cd /home/alex/jbhack/python
python -m schdoc.cli ../Main.SchDoc --output /tmp/board_summary.md
cat /tmp/board_summary.md
```
Verify all four sections appear, connector pin tables are present, probe inventory is populated.

- [ ] **Step 6: Run full test suite**

```bash
cd /home/alex/jbhack/python
python -m pytest schdoc/tests/ -v --tb=short
```
Expected: all unit + integration tests green.

- [ ] **Step 7: Final commit**

```bash
cd /home/alex/jbhack
git add python/schdoc/cli.py python/schdoc/tests/test_integration.py
git commit -m "feat: add CLI entrypoint and full pipeline integration tests"
```
