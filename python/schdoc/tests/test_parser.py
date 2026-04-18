import struct
from schdoc.models import (
    ConnectorPin, PinRef, Component, PowerRail,
    Zone, ProbePoint, SchematicSummary,
)
from schdoc.parser import parse_stream


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
