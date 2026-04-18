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
