"""Mermaid connectivity-graph rendering for a SchematicSummary.

Components are drawn as subgraphs; each pin is a node inside the subgraph.
Nets that touch two or more pins are drawn as a central "hub" node, with
every pin on that net connecting to the hub. Power-rail nets (GND, 3V3, …)
are not drawn as edges — instead each pin on a power rail is annotated with
the rail name inside its pin label, to keep the diagram readable.
"""
from __future__ import annotations

import re

from .models import Component, SchematicSummary


_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_]")


def _sanitize(s: str) -> str:
    """Return a mermaid-safe identifier (letters, digits, underscore; leading letter)."""
    s = _ID_UNSAFE.sub("_", s or "")
    if not s or not (s[0].isalpha() or s[0] == "_"):
        s = "n_" + s
    return s


def _escape_label(s: str) -> str:
    return s.replace('"', '\\"')


def _pin_node_id(designator: str, pin_number: str, pin_name: str) -> str:
    raw = pin_number or pin_name or ""
    safe = _ID_UNSAFE.sub("_", raw)
    return f"{_sanitize(designator)}__p{safe}"


def _all_components(summary: SchematicSummary) -> list[Component]:
    """Deduplicate components by designator across zones + connectors list."""
    seen: set[str] = set()
    out: list[Component] = []
    for c in [cc for z in summary.zones for cc in z.components] + summary.connectors:
        if c.designator and c.designator not in seen:
            seen.add(c.designator)
            out.append(c)
    return out


def render_mermaid(summary: SchematicSummary) -> str:
    """Return the body (no ``` fencing) of a mermaid flowchart describing connectivity."""
    power_nets = {r.name for r in summary.power_rails}
    components = _all_components(summary)

    lines: list[str] = ["flowchart LR"]

    for comp in components:
        if not comp.pins:
            continue
        subg_id = _sanitize(comp.designator)
        title_parts = [comp.designator]
        if comp.value:
            title_parts.append(comp.value)
        title = _escape_label(" • ".join(title_parts))
        lines.append(f'  subgraph {subg_id}["{title}"]')
        seen_pins: set[str] = set()
        for pin in comp.pins:
            key = pin.number or pin.name
            if key in seen_pins:
                continue
            seen_pins.add(key)
            label = pin.name or pin.number or "?"
            if pin.net in power_nets:
                pin_label = f"{label} = {pin.net}"
            else:
                pin_label = label
            pid = _pin_node_id(comp.designator, pin.number, pin.name)
            lines.append(f'    {pid}(["{_escape_label(pin_label)}"])')
        lines.append("  end")

    for net_name, refs in summary.nets.items():
        if net_name in power_nets:
            continue
        if len(refs) < 2:
            continue
        hub_id = "net__" + _sanitize(net_name)
        lines.append(f'  {hub_id}(("{_escape_label(net_name)}"))')
        for ref in refs:
            pid = _pin_node_id(ref.designator, ref.pin_number, ref.pin_name)
            lines.append(f"  {pid} --- {hub_id}")

    return "\n".join(lines)
