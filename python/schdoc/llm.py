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
