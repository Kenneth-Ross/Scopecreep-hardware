# python/agent/tools.py
from __future__ import annotations

import logging
from typing import Any
import numpy as np

from .config import MAX_VOLTAGE
from .evaluator import evaluate_tier1, evaluate_tier2
from .models import HardwareContext, ProbeInstruction, SessionState, TestResult, TestSession

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "psu_configure",
        "description": (
            "Set PSU voltage and enable/disable output on the Analog Discovery. "
            "Channel 0 = V+ (0–5 V). Call before scope_capture if the board needs power."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "integer", "description": "0 for V+, 1 for V-"},
                "voltage": {"type": "number", "description": "Target voltage in volts"},
                "enabled": {"type": "boolean", "description": "Enable or disable the rail"},
            },
            "required": ["channel", "voltage", "enabled"],
        },
    },
    {
        "name": "scope_capture",
        "description": (
            "Configure and capture a waveform on one oscilloscope channel. "
            "Returns v_min, v_max, v_mean, v_pp, v_rms. "
            "Always call require_probe first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "integer", "description": "0 or 1"},
                "voltage_range": {
                    "type": "number",
                    "description": "Full-scale input range V p-p. One of: 0.5, 1, 2, 5, 10, 25, 50",
                },
                "sample_rate": {"type": "number", "description": "Sample rate Hz", "default": 1000000},
                "num_samples": {"type": "integer", "description": "Samples to acquire", "default": 1024},
                "trigger_source": {"type": "string", "description": "'none'|'ch0'|'ch1'|'ext'", "default": "none"},
                "trigger_level": {"type": "number", "description": "Trigger threshold volts", "default": 0.0},
                "trigger_edge": {"type": "string", "description": "'rising'|'falling'", "default": "rising"},
            },
            "required": ["channel", "voltage_range"],
        },
    },
    {
        "name": "require_probe",
        "description": (
            "Pause the test session and instruct the user to place the oscilloscope probe. "
            "The agent loop blocks until the user calls /resume."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "probe_point_label": {"type": "string"},
                "net": {"type": "string"},
                "location_hint": {"type": "string", "description": "e.g. 'C12 drain, left pad'"},
                "probe_type": {"type": "string", "description": "'physical_tp'|'power_rail'|'signal'"},
                "instructions": {"type": "string", "description": "Full instruction sentence for the user"},
            },
            "required": ["probe_point_label", "net", "location_hint", "probe_type", "instructions"],
        },
    },
    {
        "name": "publish_test_plan",
        "description": (
            "Publish the overall test plan for user review. Call this FIRST, before any "
            "hardware tool (psu_configure / scope_capture / require_probe). The session "
            "pauses on publish; no hardware is touched until the user approves via "
            "/agent/sessions/{id}/resume. Call this exactly once."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "1–2 sentence summary of the overall test strategy.",
                },
                "test_cases": {
                    "type": "array",
                    "description": "Ordered list of every test you plan to run.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "probe_point_label": {"type": "string"},
                            "net": {"type": "string"},
                            "probe_type": {"type": "string"},
                            "description": {
                                "type": "string",
                                "description": "What you'll measure and why it passes/fails.",
                            },
                            "expected_range": {
                                "type": "string",
                                "description": "Expected measurement range, e.g. '3.2–3.4 V DC'.",
                            },
                        },
                        "required": ["probe_point_label", "description", "expected_range"],
                    },
                },
            },
            "required": ["summary", "test_cases"],
        },
    },
    {
        "name": "record_result",
        "description": (
            "Record a verdict for the current probe point after evaluating scope measurements. "
            "Include the measurements dict from the most recent scope_capture."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "probe_point_label": {"type": "string"},
                "verdict": {"type": "string", "enum": ["PASS", "FAIL", "MARGINAL"]},
                "reasoning": {"type": "string"},
                "measurements": {"type": "object", "description": "Stats dict from scope_capture"},
            },
            "required": ["probe_point_label", "verdict", "reasoning", "measurements"],
        },
    },
]


def _compute_stats(samples: np.ndarray) -> dict[str, float]:
    return {
        "v_min":  float(samples.min()),
        "v_max":  float(samples.max()),
        "v_mean": float(samples.mean()),
        "v_pp":   float(samples.max() - samples.min()),
        "v_rms":  float(np.sqrt(np.mean(samples ** 2))),
    }


def handle_psu_configure(inputs: dict[str, Any], hw: HardwareContext) -> dict:
    channel = int(inputs["channel"])
    voltage = float(inputs["voltage"])
    enabled = bool(inputs["enabled"])

    if not (0.0 <= voltage <= MAX_VOLTAGE):
        return {"error": f"Voltage {voltage} V is outside safe range 0–{MAX_VOLTAGE} V"}

    if enabled:
        hw.analog_discovery.psu.set_voltage(channel, voltage)
    hw.analog_discovery.psu.enable(channel, enabled)
    return {"status": "ok", "channel": channel, "voltage": voltage, "enabled": enabled}


def handle_scope_capture(inputs: dict[str, Any], hw: HardwareContext) -> dict:
    channel      = int(inputs["channel"])
    voltage_range = float(inputs["voltage_range"])
    sample_rate  = float(inputs.get("sample_rate", 1_000_000))
    num_samples  = int(inputs.get("num_samples", 1024))
    trigger_source = str(inputs.get("trigger_source", "none"))
    trigger_level  = float(inputs.get("trigger_level", 0.0))
    trigger_edge   = str(inputs.get("trigger_edge", "rising"))

    scope = hw.analog_discovery.scope
    scope.configure_channel(
        channel=channel,
        voltage_range=voltage_range,
        sample_rate=sample_rate,
        num_samples=num_samples,
    )
    scope.arm_trigger(source=trigger_source, level=trigger_level, edge=trigger_edge)
    samples = scope.read_samples(channel)
    stats = _compute_stats(samples)
    stats["sample_rate"] = sample_rate
    stats["num_samples"] = num_samples
    return stats


def handle_require_probe(inputs: dict[str, Any], session: TestSession) -> dict:
    session.current_probe = ProbeInstruction(
        probe_point_label=inputs["probe_point_label"],
        net=inputs["net"],
        location_hint=inputs["location_hint"],
        probe_type=inputs["probe_type"],
        instructions=inputs["instructions"],
    )
    session.state = SessionState.PROBE_REQUIRED
    return {"status": "probe_required", "waiting_for_user": True}


async def handle_record_result(inputs: dict[str, Any], session: TestSession) -> dict:
    probe_points = session.schematic.get("probe_points", [])
    pp = next((p for p in probe_points if p["label"] == inputs["probe_point_label"]), {})
    expected_range = pp.get("expected_range", "unknown")
    net = pp.get("net", "")

    measurements = inputs["measurements"]
    tier = 1
    reasoning = inputs["reasoning"]

    if expected_range != "unknown":
        try:
            tier1_verdict = evaluate_tier1(measurements, expected_range)
        except ValueError:
            tier1_verdict = inputs["verdict"]
    else:
        tier1_verdict = inputs["verdict"]

    verdict = tier1_verdict

    if tier1_verdict == "MARGINAL":
        try:
            board_understanding = session.schematic.get("understanding", "")
            verdict, reasoning = await evaluate_tier2(
                measurements, expected_range, pp, board_understanding
            )
            tier = 2
        except Exception as exc:
            logging.warning("evaluate_tier2 failed, keeping MARGINAL: %s", exc)
            verdict = "MARGINAL"

    session.results.append(TestResult(
        probe_point_label=inputs["probe_point_label"],
        net=net,
        verdict=verdict,
        expected_range=expected_range,
        measurements=measurements,
        reasoning=reasoning,
        tier=tier,
    ))
    return {"status": "recorded", "verdict": verdict, "tier": tier}


def handle_publish_test_plan(inputs: dict[str, Any], session: TestSession) -> dict:
    """Store the proposed plan and pause the session awaiting user approval."""
    summary = inputs.get("summary", "")
    test_cases = inputs.get("test_cases", []) or []
    session.proposed_plan = [
        {"summary": summary, "test_cases": test_cases}
    ]
    session.state = SessionState.PLAN_READY
    return {
        "status": "published",
        "waiting_for_user_approval": True,
        "test_case_count": len(test_cases),
    }


async def dispatch_tool(
    name: str,
    inputs: dict[str, Any],
    session: TestSession,
    hw: HardwareContext,
) -> dict:
    if name == "publish_test_plan":
        return handle_publish_test_plan(inputs, session)
    if name == "psu_configure":
        result = handle_psu_configure(inputs, hw)
        if "error" in result:
            session.state = SessionState.FAILED
            session.error = result["error"]
        return result
    if name == "scope_capture":
        session.state = SessionState.CAPTURING
        return handle_scope_capture(inputs, hw)
    if name == "require_probe":
        return handle_require_probe(inputs, session)
    if name == "record_result":
        session.state = SessionState.EVALUATING
        return await handle_record_result(inputs, session)
    return {"error": f"Unknown tool: {name}"}


OPENAI_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": s["name"],
            "description": s["description"],
            "parameters": s["input_schema"],
        },
    }
    for s in TOOL_SCHEMAS
]
