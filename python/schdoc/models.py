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
