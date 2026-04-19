import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from agent.models import TestSession, SessionState, HardwareContext


# ---------------------------------------------------------------------------
# OpenAI response shape helpers
# ---------------------------------------------------------------------------

def _openai_tool_call_response(name: str, args: dict, call_id: str = "call_1"):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    msg = MagicMock()
    msg.content = None
    msg.tool_calls = [tc]
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "tool_calls"
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _openai_final_response(text: str = "done"):
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = []
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _openai_unexpected_finish_response(finish_reason: str = "length"):
    """Response with no tool_calls and a non-stop finish_reason — triggers FAILED."""
    msg = MagicMock()
    msg.content = ""
    msg.tool_calls = []
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = finish_reason
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_session():
    return TestSession(schematic={
        "board_name": "TestBoard",
        "understanding": "A test board.",
        "probe_points": [
            {"label": "TP1", "net": "VCC_3V3", "expected_range": "3.3V ± 5%",
             "probe_type": "power_rail", "designator": "TP1", "pin_name": "1", "pin_number": "1"},
        ],
    })


def _make_hw():
    ad = MagicMock()
    ad.scope = MagicMock()
    ad.psu = MagicMock()
    return HardwareContext(analog_discovery=ad)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_session_end_turn_immediately():
    from agent.runner import run_session

    session = _make_session()
    hw = _make_hw()

    fake = AsyncMock()
    fake.chat.completions.create = AsyncMock(return_value=_openai_final_response())

    with patch("agent.runner.AsyncOpenAI", return_value=fake):
        await run_session(session, hw)

    assert session.state == SessionState.COMPLETE


@pytest.mark.asyncio
async def test_run_session_records_result_via_tool():
    from agent.runner import run_session

    session = _make_session()
    hw = _make_hw()

    record_call = _openai_tool_call_response("record_result", {
        "probe_point_label": "TP1",
        "verdict": "PASS",
        "reasoning": "OK",
        "measurements": {"v_mean": 3.31, "v_pp": 0.04},
    }, call_id="call_001")
    end_call = _openai_final_response()

    fake = AsyncMock()
    fake.chat.completions.create = AsyncMock(side_effect=[record_call, end_call])

    with patch("agent.runner.AsyncOpenAI", return_value=fake):
        await run_session(session, hw)

    assert session.state == SessionState.COMPLETE
    assert len(session.results) == 1
    assert session.results[0].verdict == "PASS"


@pytest.mark.asyncio
async def test_run_session_exceeds_max_rounds():
    from agent.runner import run_session
    from agent import config as cfg

    session = _make_session()
    hw = _make_hw()

    psu_call = _openai_tool_call_response("psu_configure", {
        "channel": 0, "voltage": 3.3, "enabled": True,
    })

    fake = AsyncMock()
    fake.chat.completions.create = AsyncMock(return_value=psu_call)

    original = cfg.AGENT_MAX_TOOL_ROUNDS
    cfg.AGENT_MAX_TOOL_ROUNDS = 3
    try:
        with patch("agent.runner.AsyncOpenAI", return_value=fake):
            await run_session(session, hw)
    finally:
        cfg.AGENT_MAX_TOOL_ROUNDS = original

    assert session.state == SessionState.FAILED
    assert "exceeded" in (session.error or "")


@pytest.mark.asyncio
async def test_run_session_probe_pause_resume():
    from agent.runner import run_session

    session = _make_session()
    hw = _make_hw()

    probe_call = _openai_tool_call_response("require_probe", {
        "probe_point_label": "TP1",
        "net": "VCC_3V3",
        "location_hint": "Left of C12",
        "probe_type": "power_rail",
        "instructions": "Place CH1 on TP1.",
    }, call_id="call_probe")
    end_call = _openai_final_response()

    fake = AsyncMock()
    fake.chat.completions.create = AsyncMock(side_effect=[probe_call, end_call])

    with patch("agent.runner.AsyncOpenAI", return_value=fake):
        task = asyncio.create_task(run_session(session, hw))
        await asyncio.sleep(0.05)  # let agent reach probe_required

        assert session.state == SessionState.PROBE_REQUIRED
        session._resume_event.set()  # simulate plugin calling /resume

        await task

    assert session.state == SessionState.COMPLETE


@pytest.mark.asyncio
async def test_run_session_hardware_error_fails_session():
    from agent.runner import run_session

    session = _make_session()
    hw = _make_hw()

    fake = AsyncMock()
    fake.chat.completions.create = AsyncMock(side_effect=RuntimeError("API timeout"))

    with patch("agent.runner.AsyncOpenAI", return_value=fake):
        await run_session(session, hw)

    assert session.state == SessionState.FAILED
    assert "API timeout" in (session.error or "")
