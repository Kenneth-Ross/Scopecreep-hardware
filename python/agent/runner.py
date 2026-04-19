# python/agent/runner.py
from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI, OpenAIError

from . import config
from .config import OPENAI_MODEL, AGENT_MAX_TOKENS
from .models import HardwareContext, SessionState, TestSession
from .tools import OPENAI_TOOL_SCHEMAS, dispatch_tool


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
        "1. FIRST STEP (mandatory): call `publish_test_plan` with a 1–2 sentence summary and "
        "one entry per probe point covering probe_point_label, net, probe_type, description, "
        "and expected_range. Do NOT call any hardware tool before this.\n"
        "2. The session will pause on publish_test_plan. When the user approves via /resume, "
        "continue by calling the hardware tools.\n"
        "3. Always call `require_probe` before `scope_capture` — the user must physically place the probe.\n"
        "4. Always call `record_result` after evaluating each probe point.\n"
        "5. Test power rails first (probe_type == 'power_rail'), then signal nets.\n"
        "6. Use `psu_configure` to power the board before testing if not already powered.\n"
        "7. Do not invent probe points not listed above.\n"
        "8. If v_mean is near zero after enabling PSU, the board may be disconnected — record FAIL.\n"
        "9. Work through all probe points until every one has a recorded result."
    )


async def run_session(session: TestSession, hw: HardwareContext) -> None:
    """Drive the OpenAI tool-use loop for one test session."""
    client = AsyncOpenAI()
    system = build_system_prompt(session.schematic)
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": "Please begin testing the board. Work through all probe points."},
    ]

    try:
        for _ in range(config.AGENT_MAX_TOOL_ROUNDS):
            if session.state in (SessionState.COMPLETE, SessionState.FAILED):
                break

            resp = await client.chat.completions.create(
                model=OPENAI_MODEL,
                max_tokens=AGENT_MAX_TOKENS,
                tools=OPENAI_TOOL_SCHEMAS,
                messages=messages,
            )
            try:
                from _openai_usage import log_usage
                log_usage(resp, label="agent.runner")
            except Exception:
                pass
            choice = resp.choices[0]
            msg = choice.message
            tool_calls = msg.tool_calls or []

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_msg)

            if choice.finish_reason == "stop" and not tool_calls:
                session.state = SessionState.COMPLETE
                break

            if not tool_calls:
                session.state = SessionState.FAILED
                session.error = f"Unexpected finish_reason: {choice.finish_reason}"
                break

            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError as exc:
                    result = {"error": f"bad tool arguments: {exc}"}
                else:
                    result = await dispatch_tool(tc.function.name, args, session, hw)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })

                if session.state == SessionState.PLAN_READY:
                    await session._resume_event.wait()
                    session._resume_event.clear()
                    # User rejected the plan or cancelled the session.
                    if session.state not in (SessionState.PLAN_READY, SessionState.PLANNING):
                        return
                    session.state = SessionState.PLANNING
                elif session.state == SessionState.PROBE_REQUIRED:
                    await session._resume_event.wait()
                    session._resume_event.clear()
                    if session.state != SessionState.PROBE_REQUIRED:
                        return
                    session.state = SessionState.CAPTURING
        else:
            session.state = SessionState.FAILED
            session.error = f"Agent exceeded {config.AGENT_MAX_TOOL_ROUNDS} tool rounds"

    except OpenAIError as exc:
        session.state = SessionState.FAILED
        session.error = f"OpenAI error: {exc}"
    except Exception as exc:
        session.state = SessionState.FAILED
        session.error = str(exc)
    finally:
        try:
            hw.analog_discovery.disconnect()
        except Exception:
            pass
