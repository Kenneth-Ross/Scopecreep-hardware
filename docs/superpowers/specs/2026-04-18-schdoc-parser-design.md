# SchDoc Parser Design

**Date:** 2026-04-18  
**Branch:** feat/dps150-backend  
**Scope:** Parse Altium `.SchDoc` files into a structured Markdown summary that gives an AI agent sufficient context to generate hardware tests and a Mermaid architecture diagram.

---

## Goal

Produce a single Markdown file from a `.SchDoc` schematic that an AI agent can read to:
1. Understand what the board is and what it does
2. Identify test points and probe nodes
3. Generate a concrete hardware test plan (power rail checks, signal tests, connector pin tests)
4. Emit a Mermaid diagram of its board understanding

No LLM is involved in parsing — the markdown is assembled entirely from parsed schematic data.

---

## Scope

**In scope:**
- OLE container extraction (`FileHeader` stream)
- Record parsing (pipe-delimited key=value)
- Net resolution (coordinate-based union-find)
- `SchematicSummary` dataclass assembly
- Markdown rendering to disk

**Out of scope:**
- FastAPI endpoint / HTTP upload
- Kotlin plugin integration
- Mermaid diagram generation (LLM responsibility)
- Multi-sheet hierarchy traversal (single sheet only, v1)

---

## File Layout

```
python/
  schdoc/
    __init__.py
    parser.py       # OLE extraction + record parsing + net resolution → SchematicSummary
    llm.py          # LLM pass: SchematicSummary → understanding: str (board overview prose)
    renderer.py     # SchematicSummary → Markdown string
    models.py       # SchematicSummary dataclass + supporting types
    cli.py          # python -m schdoc.cli <path.SchDoc> → writes <path>.md
```

---

## Data Pipeline

```
.SchDoc (OLE Compound Document)
  └─ olefile.OleFileIO
       └─ FileHeader stream (bytes)
            └─ record_stream_parser()     → list[dict]  (raw records)
                 ├─ component_builder()   → list[Component]
                 ├─ net_resolver()        → dict[net_name, list[PinRef]]
                 └─ zone_extractor()      → list[Zone]
                      └─ SchematicSummary
                           ├─ llm_understanding()  → understanding: str  (LLM pass)
                           └─ renderer.render()    → Markdown string → .md file
```

The LLM pass is optional — if no API key is configured, the renderer falls back to a programmatic prose summary.

---

## Stage 1: Record Extraction (`parser.py`)

**Input:** path to `.SchDoc`  
**Output:** `list[dict]` — one dict per schematic record

```
ole = olefile.OleFileIO(path)
data = ole.openstream('FileHeader').read()
```

Split `data` into records: each record is prefixed by a 4-byte little-endian uint32 length, followed by that many bytes of pipe-delimited ASCII content.

Parse each content block:
- Strip leading `|`
- Split on `|`
- Split each token on first `=` → key/value dict
- Attach `_stream_pos` (0-indexed record position in stream) to each dict

**Records used:**

| RECORD | Name | Purpose |
|--------|------|---------|
| 1 | Component | Component instance (designator, description, location) |
| 2 | Pin | Pin name, number, electrical type, XY coords |
| 17 | Power Port | Named power rail + XY |
| 18 | Port | Hierarchical/cross-sheet signal port + XY |
| 22 | No-connect | Intentionally unconnected pin marker |
| 25 | Net Label | Named net + XY |
| 27 | Wire | Polyline wire segments (XY chain) |
| 29 | Junction | T-junction confirmation + XY |
| 34 | Designator | Human-readable designator label (e.g. "U1") |
| 41 | Parameter | Value, Comment, Part Number, Manufacturer per component |
| 43 | Zone Annotation | Functional block label (e.g. "CAN", "Power Tree") |
| 45 | Footprint | PCB footprint name per component |

**Records discarded:**

RECORD=6,7,8,12,13,14 (graphical primitives: lines, arcs, polygons, ellipses, rectangles), RECORD=4 (pin name labels), RECORD=31 (sheet font/style), RECORD=44/46/47/48 (model link metadata), all color/style fields, all GUID fields, coordinate data after net resolution.

---

## Stage 2: Component Assembly (`parser.py`)

Build a `Component` per RECORD=1. Parent-child ownership uses `_stream_pos`: a child record with `OwnerIndex=N` belongs to the component at stream position `N+1`.

For each component, collect:
- **Designator** — from child RECORD=34 where `Name="Designator"`
- **Value/Comment** — from child RECORD=41 where `Name` is `"Comment"` or `"Value"`
- **Description** — `ComponentDescription` field on RECORD=1
- **Part Number** — from child RECORD=41 where `Name="Part Number"`
- **Manufacturer** — from child RECORD=41 where `Name="Manufacturer"`
- **Footprint** — from child RECORD=45, `ModelDatafileEntity0` field
- **Pins** — all child RECORD=2: `{name, number, electrical_type, x, y}`
- **Is connector** — true if designator prefix is `J` or footprint/description contains "Connector" / "Molex" / "Header" (case-insensitive)

**Electrical type mapping:**

| Value | Meaning |
|-------|---------|
| 0 | Input |
| 1 | I/O |
| 2 | Output |
| 4 | Passive |
| 7 | Power |

---

## Stage 3: Net Resolution (`parser.py`)

**Algorithm:** coordinate-based union-find over all endpoints.

Collect coordinate nodes from:
- Wire segment endpoints: every `(X_i, Y_i)` pair in RECORD=27 chains
- Pin locations: `(Location.X, Location.Y)` from RECORD=2
- Power port locations: RECORD=17
- Net label locations: RECORD=25
- Junction locations: RECORD=29

Two nodes are unioned if their coordinates are identical (integer match; Altium coordinates are integer mils). Build union-find clusters. Name each cluster from the first power port `Text` or net label `Text` found in the cluster. Unnamed clusters are assigned `NET_{cluster_id}`.

Output: `dict[net_name, list[PinRef]]` where `PinRef = {designator, pin_name, pin_number, electrical_type}`.

**Power rail identification:** any net whose name appears as a RECORD=17 `Text` value, or whose name matches common rail patterns (`GND`, `VCC`, `3V3`, `12V`, `5V`, `VBAT`, etc.).

---

## Stage 4: Zone Extraction (`parser.py`)

RECORD=43 annotations define named functional blocks. Each has a bounding box `(Location.X/Y, Corner.X/Y)` and a `Name`. Assign each component to a zone if its `Location.X/Y` falls within a zone bounding box. Components outside all zones go into an `"Other"` group.

---

## Data Model (`models.py`)

```python
@dataclass
class PinRef:
    designator: str
    pin_name: str
    pin_number: str
    electrical_type: int

@dataclass
class ConnectorPin:
    number: str
    name: str
    net: str

@dataclass
class Component:
    designator: str
    value: str
    description: str
    part_number: str
    manufacturer: str
    footprint: str
    is_connector: bool
    pins: list[ConnectorPin]   # populated after net resolution

@dataclass
class PowerRail:
    name: str
    nominal_voltage: float | None   # parsed from name heuristic (e.g. "3V3" → 3.3)
    source_designator: str | None   # component whose Output pin drives this rail
    loads: list[str]                # designators of components with pins on this rail

@dataclass
class Zone:
    name: str
    components: list[Component]
    key_nets: list[str]             # nets with ≥2 component connections in this zone

@dataclass
class ProbePoint:
    label: str          # e.g. "J3", "U1.FB", "U2.CANH"
    net: str
    designator: str
    pin_name: str
    pin_number: str
    probe_type: str     # "physical_tp" | "power_rail" | "signal"
    expected_range: str # e.g. "3.3V ± 5%", "0–3.3V", "CAN differential 1.5–3.0V"

@dataclass
class SchematicSummary:
    board_name: str
    understanding: str          # synthesized prose paragraph
    power_rails: list[PowerRail]
    zones: list[Zone]
    connectors: list[Component] # is_connector=True components
    probe_points: list[ProbePoint]
    nets: dict[str, list[PinRef]]
```

---

## Stage 5: Markdown Rendering (`renderer.py`)

Renders `SchematicSummary` to a Markdown string with four sections.

### Section 1: Board Understanding

Generated by an LLM pass (`llm_understanding()` in `parser.py`) using a structured prompt built from the parsed `SchematicSummary`. The LLM receives a compact JSON context (BOM, power rails, zones, ports, probe points) and is asked to produce a 3–5 sentence high-level summary covering:

- What the board is and its purpose
- Power architecture (input rail → conversion → output rails)
- Functional subsystems and their roles
- External interfaces and signals
- Physical test access available

**Prompt structure (`python/schdoc/llm.py`):**

```python
SYSTEM = (
    "You are a hardware engineer. Given structured schematic data, "
    "write a concise 3-5 sentence board overview a test engineer can use "
    "to understand the board before generating hardware tests. "
    "Be specific: name components, voltages, and signal types. "
    "Do not speculate beyond the data provided."
)

user_content = json.dumps({
    "components": [...],   # designator, value, description per component
    "power_rails": [...],  # name, nominal_v, source, loads
    "zones": [...],        # zone name + component designators
    "ports": [...],        # cross-sheet signal names
    "probe_points": [...]  # physical TPs
}, indent=2)
```

**Provider:** Configurable via `SCHDOC_LLM_PROVIDER` env var (`openai` or `anthropic`). Defaults to `openai` (consistent with the rest of the agent stack). Model defaults: `gpt-4o-mini` (OpenAI) or `claude-haiku-4-5` (Anthropic) — fast and cheap for a single structured summarization call.

**Fallback:** If `SCHDOC_LLM_PROVIDER` is unset or the API call fails, falls back to programmatic prose assembly from the same structured fields.

### Section 2: Power Topology

For each power rail:
```markdown
### 3V3
- **Source:** U1 (TPS62932DRLR) — output pin SW/FB
- **Nominal:** 3.3V
- **Loads:**
  - U2 (TCAN332DCNR) — VCC pin
  - U3 (TCAN332DCNR) — VCC pin
  - A1 (STM32F746ZG Nucleo) — 3V3 pin
```

Nominal voltage parsed heuristically from rail name (`3V3` → 3.3V, `12V` → 12.0V, `GND` → 0V).

### Section 3: Functional Blocks

For each zone, list components and their key nets. For connector components, emit the full pin map table:

```markdown
### Connectors

#### J1 — Molex Microfit 12-pos
| Pin | Net | Signal |
|-----|-----|--------|
| 1   | 12V | Power input |
| 2   | GND | Ground |
| 3   | CANVEH_H | CAN Vehicle High |
...
```

Non-connector components listed as: `- **U1** (TPS62932DRLR): 12V→3V3 synchronous buck converter`

### Section 4: Probe Inventory

Physical test points first, then power rails, then high-value signal nodes.

```markdown
| Probe Point | Net | Component.Pin | Type | Expected Range |
|-------------|-----|--------------|------|----------------|
| J3 | 3V3 | J3.1 | physical_tp | 3.3V ± 5% |
| J4 | GND | J4.1 | physical_tp | 0V |
| U1.FB | FB | U1.FB | power_rail | 0.8V (voltage divider setpoint) |
| U2.CANH | CANVEH_H | U2.CANH | signal | CAN diff 1.5–3.0V active |
| J1.3 | CANVEH_H | J1.3 | signal | CAN diff 1.5–3.0V active |
```

**Expected range heuristics:**
- Power rail named `3V3` → `3.3V ± 5%`
- Power rail named `12V` → `12.0V ± 10%`
- `GND` → `0V`
- CAN nets (`CANH`/`CANL` in name) → `CAN differential 1.5–3.0V (active)`
- ADC inputs (pin connected to `ADC` port name) → `0–3.3V analog`
- Unknown → `TBD`

---

## CLI (`cli.py`)

```
python -m schdoc.cli Main.SchDoc
# writes Main.schematic_summary.md to same directory
```

Also accepts `--output <path>` to specify output file.

---

## Dependencies

- `olefile` — OLE container reading (pure Python, PyPI)
- `openai` — OpenAI SDK (default LLM provider for board understanding pass)
- `anthropic` — Anthropic SDK (optional alternative provider)
- Standard library only beyond that (`dataclasses`, `pathlib`, `struct`, `re`, `json`)

Added to `python/requirements.txt`: `olefile`, `openai`. `anthropic` is optional.

---

## Test File

`Main.SchDoc` (534 KB, repo root, currently untracked) is the reference file for development. Parser output should be validated against known board contents:
- 40 component instances
- Power rails: `12V`, `3V3`, `GND`
- Zones: "CAN", "12V-3V3 Power Tree", "Input Interface", "Connectors"
- Connectors: J1 (Molex Microfit 12-pos), J3–J6 (test point pads)
- Cross-sheet ports: `CANVEH_H/L_OUT`, `CANPT_H/L_OUT`, `ADC1 Pedal 1/2`, `ADC3 Brake 1/2`, `RTD Button`, `RTM Active`
