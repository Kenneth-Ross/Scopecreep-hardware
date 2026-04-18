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
