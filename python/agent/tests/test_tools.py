import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import numpy as np

from agent.models import TestSession, SessionState, HardwareContext, ProbeInstruction


def _make_hw(scope_samples=None):
    """Return a mocked HardwareContext."""
    if scope_samples is None:
        scope_samples = np.array([3.28, 3.30, 3.32, 3.31, 3.29], dtype=np.float64)

    scope = MagicMock()
    scope.configure_channel = MagicMock()
    scope.arm_trigger = MagicMock()
    scope.read_samples = MagicMock(return_value=scope_samples)

    psu = MagicMock()
    psu.set_voltage = MagicMock()
    psu.enable = MagicMock()

    ad = MagicMock()
    ad.scope = scope
    ad.psu = psu

    return HardwareContext(analog_discovery=ad)


def _make_session(probe_points=None):
    schematic = {
        "board_name": "TestBoard",
        "understanding": "A test board.",
        "probe_points": probe_points or [
            {"label": "TP1", "net": "VCC_3V3", "expected_range": "3.3V ± 5%",
             "probe_type": "power_rail", "designator": "TP1",
             "pin_name": "1", "pin_number": "1"},
        ],
    }
    return TestSession(schematic=schematic)


# --- psu_configure ---

def test_handle_psu_configure_enable():
    from agent.tools import handle_psu_configure
    hw = _make_hw()
    result = handle_psu_configure({"channel": 0, "voltage": 3.3, "enabled": True}, hw)
    assert result["status"] == "ok"
    hw.analog_discovery.psu.set_voltage.assert_called_once_with(0, 3.3)
    hw.analog_discovery.psu.enable.assert_called_once_with(0, True)


def test_handle_psu_configure_disable():
    from agent.tools import handle_psu_configure
    hw = _make_hw()
    result = handle_psu_configure({"channel": 0, "voltage": 0.0, "enabled": False}, hw)
    assert result["status"] == "ok"
    hw.analog_discovery.psu.enable.assert_called_once_with(0, False)
    hw.analog_discovery.psu.set_voltage.assert_not_called()


def test_handle_psu_configure_negative_voltage_rejected():
    from agent.tools import handle_psu_configure
    hw = _make_hw()
    result = handle_psu_configure({"channel": 0, "voltage": -5.0, "enabled": True}, hw)
    assert "error" in result
    hw.analog_discovery.psu.set_voltage.assert_not_called()


def test_handle_psu_configure_voltage_clamp():
    from agent.tools import handle_psu_configure
    hw = _make_hw()
    result = handle_psu_configure({"channel": 0, "voltage": 99.0, "enabled": True}, hw)
    assert "error" in result
    hw.analog_discovery.psu.set_voltage.assert_not_called()


# --- scope_capture ---

def test_handle_scope_capture_returns_stats():
    from agent.tools import handle_scope_capture
    hw = _make_hw(scope_samples=np.array([3.28, 3.30, 3.32, 3.31, 3.29]))
    result = handle_scope_capture(
        {"channel": 0, "voltage_range": 5.0, "sample_rate": 1e6, "num_samples": 5,
         "trigger_source": "none", "trigger_level": 0.0, "trigger_edge": "rising"},
        hw,
    )
    assert "v_mean" in result
    assert "v_min" in result
    assert "v_max" in result
    assert "v_pp" in result
    assert "v_rms" in result
    assert abs(result["v_mean"] - 3.3) < 0.05


def test_handle_scope_capture_calls_driver():
    from agent.tools import handle_scope_capture
    hw = _make_hw()
    handle_scope_capture({"channel": 1, "voltage_range": 10.0}, hw)
    hw.analog_discovery.scope.configure_channel.assert_called_once_with(
        channel=1, voltage_range=10.0, sample_rate=1_000_000, num_samples=1024
    )


# --- require_probe ---

def test_handle_require_probe_sets_state():
    from agent.tools import handle_require_probe
    session = _make_session()
    result = handle_require_probe(
        {
            "probe_point_label": "TP1",
            "net": "VCC_3V3",
            "location_hint": "Left of C12",
            "probe_type": "power_rail",
            "instructions": "Place CH1 on TP1.",
        },
        session,
    )
    assert session.state == SessionState.PROBE_REQUIRED
    assert session.current_probe.probe_point_label == "TP1"
    assert result["status"] == "probe_required"


# --- handle_record_result ---

@pytest.mark.asyncio
async def test_handle_record_result_pass():
    from agent.tools import handle_record_result
    session = _make_session()
    result = await handle_record_result(
        {
            "probe_point_label": "TP1",
            "verdict": "PASS",
            "reasoning": "Looks good.",
            "measurements": {"v_mean": 3.31, "v_pp": 0.04},
        },
        session,
    )
    assert result["verdict"] == "PASS"
    assert len(session.results) == 1
    assert session.results[0].verdict == "PASS"
    assert session.results[0].tier == 1


@pytest.mark.asyncio
async def test_handle_record_result_fail():
    from agent.tools import handle_record_result
    session = _make_session()
    result = await handle_record_result(
        {
            "probe_point_label": "TP1",
            "verdict": "FAIL",
            "reasoning": "Way off.",
            "measurements": {"v_mean": 1.0, "v_pp": 0.1},
        },
        session,
    )
    assert result["verdict"] == "FAIL"
    assert session.results[0].verdict == "FAIL"


@pytest.mark.asyncio
async def test_handle_record_result_marginal_escalates_to_tier2():
    from agent.tools import handle_record_result
    session = _make_session()

    with patch("agent.tools.evaluate_tier2", new=AsyncMock(return_value=("FAIL", "Ripple too high."))):
        result = await handle_record_result(
            {
                "probe_point_label": "TP1",
                "verdict": "MARGINAL",
                "reasoning": "Slightly off.",
                "measurements": {"v_mean": 3.10, "v_pp": 0.08},  # just outside 5% band
            },
            session,
        )
    assert result["tier"] == 2
    assert session.results[0].verdict == "FAIL"  # tier-2 overrides to FAIL
    assert session.results[0].tier == 2


# --- dispatch_tool ---

@pytest.mark.asyncio
async def test_dispatch_unknown_tool():
    from agent.tools import dispatch_tool
    session = _make_session()
    hw = _make_hw()
    result = await dispatch_tool("nonexistent_tool", {}, session, hw)
    assert "error" in result


@pytest.mark.asyncio
async def test_dispatch_psu_safety_violation_fails_session():
    from agent.tools import dispatch_tool
    from agent.models import SessionState
    session = _make_session()
    hw = _make_hw()
    result = await dispatch_tool(
        "psu_configure", {"channel": 0, "voltage": 99.0, "enabled": True}, session, hw
    )
    assert "error" in result
    assert session.state == SessionState.FAILED
    assert session.error is not None
