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
    order = {"physical_tp": 0, "power_rail": 1, "signal": 2}
    sorted_probes = sorted(summary.probe_points, key=lambda p: order.get(p.probe_type, 3))
    for probe in sorted_probes:
        lines.append(
            f"| {probe.label} | {probe.net} | {probe.designator}.{probe.pin_name} "
            f"| {probe.probe_type} | {probe.expected_range} |"
        )
    return "\n".join(lines)
