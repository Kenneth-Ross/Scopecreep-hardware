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
