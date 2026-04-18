import asyncio
import pytest
from agent.models import SessionState, TestSession, ProbeInstruction, TestResult, HardwareContext


def test_session_default_state():
    s = TestSession(schematic={})
    assert s.state == SessionState.PLANNING
    assert s.results == []
    assert s.current_probe is None
    assert s.error is None
    assert s.total_probe_points == 0


def test_session_has_resume_event():
    s = TestSession(schematic={})
    assert not s._resume_event.is_set()


def test_resume_events_are_distinct_per_session():
    a = TestSession(schematic={})
    b = TestSession(schematic={})
    assert a._resume_event is not b._resume_event


def test_session_id_is_unique():
    a = TestSession(schematic={})
    b = TestSession(schematic={})
    assert a.session_id != b.session_id


def test_probe_instruction_fields():
    p = ProbeInstruction(
        probe_point_label="TP3",
        net="VCC_3V3",
        location_hint="C12 drain",
        probe_type="power_rail",
        instructions="Place CH1 on TP3.",
    )
    assert p.probe_point_label == "TP3"
    assert p.net == "VCC_3V3"


def test_test_result_fields():
    r = TestResult(
        probe_point_label="TP3",
        net="VCC_3V3",
        verdict="PASS",
        expected_range="3.3V ± 5%",
        measurements={"v_mean": 3.31},
        reasoning="Within tolerance.",
        tier=1,
    )
    assert r.verdict == "PASS"
    assert r.tier == 1


def test_hardware_context_holds_device():
    hw = HardwareContext(analog_discovery=object())
    assert hw.analog_discovery is not None


def test_session_state_enum_values():
    assert SessionState.PLANNING == "planning"
    assert SessionState.PROBE_REQUIRED == "probe_required"
    assert SessionState.CAPTURING == "capturing"
    assert SessionState.EVALUATING == "evaluating"
    assert SessionState.COMPLETE == "complete"
    assert SessionState.FAILED == "failed"
