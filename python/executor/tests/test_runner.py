import io
import pytest

from planner.models import (
    TestPlan, TestCase, PsuSetting, ProbeStep, ExpectedRange,
)
from executor.runner import run, SafetyError


class FakePsu:
    def __init__(self):
        self.log: list[tuple] = []
    def set(self, v, i):
        self.log.append(("set", v, i))
    def output_on(self):
        self.log.append(("on",))
    def output_off(self):
        self.log.append(("off",))
    def close(self):
        self.log.append(("close",))


class FakeScope:
    def __init__(self, v_mean=3.31):
        self.v_mean = v_mean
        self.closed = False
    def capture(self, channel):
        return {"v_min": self.v_mean - 0.02, "v_max": self.v_mean + 0.02,
                "v_mean": self.v_mean, "v_pp": 0.04, "v_rms": self.v_mean}
    def close(self):
        self.closed = True


class FakeScopeRaises(FakeScope):
    def capture(self, channel):
        raise RuntimeError("scope broke")


def _single_case_plan(voltage=12.0, current_limit=0.5, nominal=3.3):
    tc = TestCase(
        id="TC-01", title="3V3 rail", rationale="why",
        psu=PsuSetting(voltage=voltage, current_limit=current_limit, rail_name="12V"),
        probe=ProbeStep(label="TP3", net="VCC_3V3", location_hint="C12",
                        probe_type="physical_tp", scope_channel="CH1",
                        user_instructions="Place probe."),
        expected=ExpectedRange(kind="pct", nominal=nominal, tolerance=5.0),
        measurement="v_mean",
    )
    return TestPlan(board_name="Main", summary="s", test_cases=[tc])


def _stdin(lines):
    return io.StringIO("\n".join(lines) + "\n")


def test_happy_path_pass(monkeypatch):
    monkeypatch.setenv("MAX_VOLTAGE", "15.0")
    monkeypatch.setenv("MAX_CURRENT", "2.0")
    psu, scope = FakePsu(), FakeScope(v_mean=3.31)
    report = run(_single_case_plan(), psu=psu, scope=scope, stdin=_stdin(["", ""]))
    assert report.overall == "PASS"
    assert report.results[0].verdict == "PASS"
    assert ("on",) in psu.log
    assert ("off",) in psu.log
    assert scope.closed is True


def test_psu_off_after_scope_error(monkeypatch):
    monkeypatch.setenv("MAX_VOLTAGE", "15.0")
    monkeypatch.setenv("MAX_CURRENT", "2.0")
    psu, scope = FakePsu(), FakeScopeRaises()
    report = run(_single_case_plan(), psu=psu, scope=scope, stdin=_stdin(["", ""]))
    assert ("off",) in psu.log
    assert report.results[0].verdict == "ERROR"


def test_safety_violation_aborts_before_output_on(monkeypatch):
    monkeypatch.setenv("MAX_VOLTAGE", "5.0")
    monkeypatch.setenv("MAX_CURRENT", "2.0")
    psu, scope = FakePsu(), FakeScope()
    with pytest.raises(SafetyError):
        run(_single_case_plan(voltage=12.0), psu=psu, scope=scope, stdin=_stdin(["", ""]))
    assert ("on",) not in psu.log
    assert ("off",) in psu.log


def test_current_limit_safety(monkeypatch):
    monkeypatch.setenv("MAX_VOLTAGE", "15.0")
    monkeypatch.setenv("MAX_CURRENT", "0.1")
    psu, scope = FakePsu(), FakeScope()
    with pytest.raises(SafetyError):
        run(_single_case_plan(voltage=5.0, current_limit=0.5), psu=psu, scope=scope, stdin=_stdin(["", ""]))


def test_marginal_verdict(monkeypatch):
    monkeypatch.setenv("MAX_VOLTAGE", "15.0")
    monkeypatch.setenv("MAX_CURRENT", "2.0")
    psu, scope = FakePsu(), FakeScope(v_mean=3.5)
    report = run(_single_case_plan(), psu=psu, scope=scope, stdin=_stdin(["", ""]))
    assert report.results[0].verdict == "MARGINAL"
