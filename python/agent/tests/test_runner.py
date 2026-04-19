import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from agent.models import TestSession, SessionState, HardwareContext


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


def _make_end_turn_response():
    """Anthropic response that ends the loop immediately."""
    block = MagicMock()
    block.type = "text"
    block.text = "All done."
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


def _make_tool_use_response(tool_name: str, tool_input: dict, tool_id: str = "tu_001"):
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = tool_name
    block.input = tool_input
    resp = MagicMock()
    resp.stop_reason = "tool_use"
    resp.content = [block]
    return resp


@pytest.mark.asyncio
async def test_run_session_end_turn_immediately():
    from agent.runner import run_session

    session = _make_session()
    hw = _make_hw()

    with patch("agent.runner.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=_make_end_turn_response())

        await run_session(session, hw)

    assert session.state == SessionState.COMPLETE


@pytest.mark.asyncio
async def test_run_session_records_result_via_tool():
    from agent.runner import run_session

    session = _make_session()
    hw = _make_hw()

    record_call = _make_tool_use_response("record_result", {
        "probe_point_label": "TP1",
        "verdict": "PASS",
        "reasoning": "OK",
        "measurements": {"v_mean": 3.31, "v_pp": 0.04},
    }, tool_id="tu_001")
    end_call = _make_end_turn_response()

    responses = [record_call, end_call]

    with patch("agent.runner.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(side_effect=responses)

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

    psu_call = _make_tool_use_response("psu_configure", {
        "channel": 0, "voltage": 3.3, "enabled": True,
    })

    with patch("agent.runner.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=psu_call)

        original = cfg.AGENT_MAX_TOOL_ROUNDS
        cfg.AGENT_MAX_TOOL_ROUNDS = 3
        try:
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

    probe_call = _make_tool_use_response("require_probe", {
        "probe_point_label": "TP1",
        "net": "VCC_3V3",
        "location_hint": "Left of C12",
        "probe_type": "power_rail",
        "instructions": "Place CH1 on TP1.",
    }, tool_id="tu_probe")
    end_call = _make_end_turn_response()

    with patch("agent.runner.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(side_effect=[probe_call, end_call])

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

    with patch("agent.runner.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(side_effect=RuntimeError("API timeout"))

        await run_session(session, hw)

    assert session.state == SessionState.FAILED
    assert "API timeout" in (session.error or "")
