from schdoc.mermaid import render_mermaid
from schdoc.models import (
    Component, ConnectorPin, PinRef, PowerRail, SchematicSummary, Zone,
)


def _summary() -> SchematicSummary:
    r2 = Component(
        designator="R2", value="10k", description="Resistor",
        part_number="", manufacturer="", footprint="", is_connector=False,
        pins=[
            ConnectorPin(number="1", name="", net="NET_4"),
            ConnectorPin(number="2", name="", net="NET_8"),
        ],
    )
    q1 = Component(
        designator="Q1", value="MOSFET", description="NMOS",
        part_number="", manufacturer="", footprint="", is_connector=False,
        pins=[
            ConnectorPin(number="1", name="G", net="NET_4"),
            ConnectorPin(number="2", name="D", net="12V"),
            ConnectorPin(number="3", name="S", net="NET_8"),
        ],
    )
    zone = Zone(name="Power Tree", components=[r2, q1], key_nets=[])
    rail_12v = PowerRail(name="12V", nominal_voltage=12.0, source_designator=None, loads=["Q1"])
    nets = {
        "NET_4": [
            PinRef(designator="R2", pin_name="", pin_number="1", electrical_type=4),
            PinRef(designator="Q1", pin_name="G", pin_number="1", electrical_type=0),
        ],
        "NET_8": [
            PinRef(designator="R2", pin_name="", pin_number="2", electrical_type=4),
            PinRef(designator="Q1", pin_name="S", pin_number="3", electrical_type=0),
        ],
        "12V": [
            PinRef(designator="Q1", pin_name="D", pin_number="2", electrical_type=0),
        ],
    }
    return SchematicSummary(
        board_name="Test", understanding="",
        power_rails=[rail_12v], zones=[zone], connectors=[],
        probe_points=[], nets=nets,
    )


def test_header_and_components():
    md = render_mermaid(_summary())
    assert md.startswith("flowchart LR")
    assert 'subgraph R2["R2 • 10k"]' in md
    assert 'subgraph Q1["Q1 • MOSFET"]' in md


def test_pin_label_power_annotation():
    md = render_mermaid(_summary())
    # Q1.D is on 12V → annotated, not edged
    assert '"D = 12V"' in md


def test_net_hub_and_edges():
    md = render_mermaid(_summary())
    # Hub nodes present for signal nets
    assert 'net__NET_4(("NET_4"))' in md
    assert 'net__NET_8(("NET_8"))' in md
    # Both pins of each signal net connect to their hub
    assert "R2__p1 --- net__NET_4" in md
    assert "Q1__p1 --- net__NET_4" in md
    assert "R2__p2 --- net__NET_8" in md
    assert "Q1__p3 --- net__NET_8" in md


def test_power_net_has_no_hub():
    md = render_mermaid(_summary())
    assert 'net__12V' not in md  # power rails don't get hubs


def test_singleton_net_skipped():
    summary = _summary()
    summary.nets["NET_LONE"] = [
        PinRef(designator="R2", pin_name="", pin_number="1", electrical_type=4),
    ]
    md = render_mermaid(summary)
    assert "net__NET_LONE" not in md
