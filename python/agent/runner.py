# python/agent/runner.py
from __future__ import annotations

import asyncio
import json
from typing import Any

import anthropic

from . import config
from .config import AGENT_MODEL, AGENT_MAX_TOKENS
from .models import HardwareContext, SessionState, TestSession
from .tools import TOOL_SCHEMAS, dispatch_tool


def build_system_prompt(schematic: dict[str, Any]) -> str:
    probe_points = schematic.get("probe_points", [])
    board_name = schematic.get("board_name", "Unknown Board")
    understanding = schematic.get("understanding", "")
    return (
        f"You are a hardware test agent validating the PCB: {board_name}\n\n"
        f"## Board Understanding\n{understanding}\n\n"
        f"## Probe Points ({len(probe_points)} total)\n"
        f"{json.dumps(probe_points, indent=2)}\n\n"
        "## Rules\n"
        "1. Always call `require_probe` before `scope_capture` — the user must physically place the probe.\n"
        "2. Always call `record_result` after evaluating each probe point.\n"
        "3. Test power rails first (probe_type == 'power_rail'), then signal nets.\n"
        "4. Use `psu_configure` to power the board before testing if not already powered.\n"
        "5. Do not invent probe points not listed above.\n"
        "6. If v_mean is near zero after enabling PSU, the board may be disconnected — record FAIL.\n"
        "7. Work through all probe points until every one has a recorded result."
    )


async def run_session(session: TestSession, hw: HardwareContext) -> None:
    """Drive the Claude tool-use loop for one test session."""
    client = anthropic.Anthropic()
    system = build_system_prompt(session.schematic)
    messages: list[dict] = [
        {"role": "user", "content": "Please begin testing the board. Work through all probe points."}
    ]

    try:
        for _ in range(config.AGENT_MAX_TOOL_ROUNDS):
            if session.state in (SessionState.COMPLETE, SessionState.FAILED):
                break

            response = client.messages.create(
                model=AGENT_MODEL,
                max_tokens=AGENT_MAX_TOKENS,
                system=system,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                session.state = SessionState.COMPLETE
                break

            if response.stop_reason != "tool_use":
                session.state = SessionState.FAILED
                session.error = f"Unexpected stop_reason: {response.stop_reason}"
                break

            tool_results: list[dict] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                result = await dispatch_tool(block.name, block.input, session, hw)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })

                if session.state == SessionState.PROBE_REQUIRED:
                    await session._resume_event.wait()
                    session._resume_event.clear()
                    session.state = SessionState.CAPTURING

            messages.append({"role": "user", "content": tool_results})

        else:
            session.state = SessionState.FAILED
            session.error = f"Agent exceeded {config.AGENT_MAX_TOOL_ROUNDS} tool rounds"

    except Exception as exc:
        session.state = SessionState.FAILED
        session.error = str(exc)
