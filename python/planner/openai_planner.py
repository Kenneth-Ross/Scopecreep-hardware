# python/planner/openai_planner.py
from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI, OpenAIError
from pydantic import ValidationError

from .models import TestPlan

SYSTEM_PROMPT = (
    "You are a hardware test engineer. Given a parsed schematic summary, "
    "produce a JSON test plan that verifies each power rail and any explicit "
    "test points on the board. For each test case: pick a PSU voltage and "
    "current limit justified by the schematic's nominal rails; never exceed "
    "{max_voltage} volts on any channel; specify a physical probe location a "
    "human can find (reference designator + pin or pad, plus a nearby "
    "landmark); give an expected range grounded in the schematic. "
    "Prefer probe points the parser already extracted; you MAY add rail-level "
    "tests (ripple, load-step) only if the schematic supports it. Be concrete. "
    "Do not invent components not in the summary.\n\n"
    "ExpectedRange.kind field rules — match these EXACTLY:\n"
    "  kind='pct'     → set `nominal` AND `tolerance` (tolerance is percent, e.g. 5.0 for ±5%). "
    "Leave `lower` and `upper` null.\n"
    "  kind='abs_mv'  → set `nominal` AND `tolerance` (tolerance is millivolts, e.g. 165 for ±165mV). "
    "Leave `lower` and `upper` null.\n"
    "  kind='gt'      → set `lower` only (measurement must be > lower). Leave all others null.\n"
    "  kind='lt'      → set `upper` only (measurement must be < upper). Leave all others null.\n"
    "  kind='range'   → set `lower` AND `upper` (absolute volt bounds). Leave `nominal` and `tolerance` null.\n"
    "Never emit a test case that violates these rules — the plan will be rejected."
)


class PlannerError(RuntimeError):
    """Fatal planner error — surfaced to the CLI with a non-zero exit."""


def _strictify(schema: Any) -> Any:
    """Transform a Pydantic JSON schema into OpenAI strict-mode-compatible form."""
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            schema["additionalProperties"] = False
            props = schema.get("properties", {})
            schema["required"] = list(props.keys())
        for v in schema.values():
            _strictify(v)
        for key in ("anyOf", "oneOf", "allOf"):
            if key in schema:
                schema[key] = [_strictify(s) for s in schema[key]]
        defs = schema.get("$defs", {}) or schema.get("definitions", {})
        for v in defs.values():
            _strictify(v)
    elif isinstance(schema, list):
        for v in schema:
            _strictify(v)
    return schema


def _build_context(summary: Any, max_voltage: float) -> str:
    """Extract a JSON context from a SchematicSummary (duck-typed)."""
    seen: set[str] = set()
    components: list = []
    for z in getattr(summary, "zones", []):
        for c in getattr(z, "components", []):
            if c.designator not in seen:
                seen.add(c.designator)
                components.append(c)
    for c in getattr(summary, "connectors", []):
        if c.designator not in seen:
            seen.add(c.designator)
            components.append(c)

    return json.dumps({
        "max_voltage": max_voltage,
        "board_name": getattr(summary, "board_name", "Unknown"),
        "components": [
            {"designator": c.designator, "value": c.value, "description": c.description}
            for c in components
        ],
        "power_rails": [
            {
                "name": r.name,
                "nominal_voltage": r.nominal_voltage,
                "source": r.source_designator,
                "loads": r.loads,
            }
            for r in getattr(summary, "power_rails", [])
        ],
        "zones": [
            {"name": z.name, "components": [c.designator for c in z.components]}
            for z in getattr(summary, "zones", [])
        ],
        "probe_points": [
            {"label": p.label, "net": p.net, "type": p.probe_type, "expected": p.expected_range}
            for p in getattr(summary, "probe_points", [])
        ],
    }, indent=2)


def generate_plan(summary: Any) -> TestPlan:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise PlannerError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in."
        )

    model = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
    max_voltage = float(os.environ.get("MAX_VOLTAGE", "5.0"))
    system = SYSTEM_PROMPT.replace("{max_voltage}", str(max_voltage))
    context = _build_context(summary, max_voltage)

    client = OpenAI(api_key=api_key)

    schema = _strictify(TestPlan.model_json_schema())

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": context},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "TestPlan",
                    "schema": schema,
                    "strict": True,
                },
            },
        )
    except OpenAIError as exc:
        raise PlannerError(f"OpenAI API error: {exc}") from exc

    try:
        from _openai_usage import log_usage
        log_usage(resp, label="planner")
    except Exception:
        pass

    raw = resp.choices[0].message.content
    if not raw:
        raise PlannerError("OpenAI returned an empty message.")

    try:
        return TestPlan.model_validate_json(raw)
    except ValidationError as exc:
        raise PlannerError(f"Plan failed schema validation:\n{exc}\nRaw:\n{raw}") from exc
