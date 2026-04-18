import struct
from schdoc.models import (
    ConnectorPin, PinRef, Component, PowerRail,
    Zone, ProbePoint, SchematicSummary,
)
from schdoc.parser import parse_stream, build_components, resolve_nets, extract_zones


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
    zones = extract_zones(records, [comp], {"U1": (150, 150)})
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
