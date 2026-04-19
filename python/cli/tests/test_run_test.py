import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from cli.run_test import main
from planner.models import TestPlan, TestCase, PsuSetting, ProbeStep, ExpectedRange


def _plan():
    return TestPlan(
        board_name="Main", summary="s",
        test_cases=[TestCase(
            id="TC-01", title="t", rationale="r",
            psu=PsuSetting(voltage=3.3, current_limit=0.2, rail_name="3V3"),
            probe=ProbeStep(label="TP3", net="VCC_3V3", location_hint="C12",
                            probe_type="physical_tp", scope_channel="CH1",
                            user_instructions="Probe."),
            expected=ExpectedRange(kind="pct", nominal=3.3, tolerance=5.0),
            measurement="v_mean",
        )],
    )


class FakePsu:
    def set(self, v, i): pass
    def output_on(self): pass
    def output_off(self): pass
    def close(self): pass


class FakeScope:
    def capture(self, channel):
        return {"v_mean": 3.31, "v_pp": 0.04, "v_rms": 3.31, "v_min": 3.29, "v_max": 3.33}
    def close(self): pass


def _schdoc_path():
    # Main.SchDoc lives at repo root in this worktree
    p = Path(__file__).resolve().parents[3] / "Main.SchDoc"
    if not p.exists():
        pytest.skip(f"Main.SchDoc not present at {p}")
    return p


def test_run_test_writes_report(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_VOLTAGE", "15.0")
    monkeypatch.setenv("MAX_CURRENT", "2.0")
    schdoc = _schdoc_path()
    report_dir = tmp_path / "reports"
    stdin_data = "y\n\n\n"

    with patch("cli.run_test.generate_plan", return_value=_plan()), \
         patch("cli.run_test.Dps150Adapter", return_value=FakePsu()), \
         patch("cli.run_test.AnalogDiscoveryScopeAdapter", return_value=FakeScope()), \
         patch("sys.stdin", io.StringIO(stdin_data)):
        rc = main([str(schdoc), "--report-dir", str(report_dir)])

    assert rc == 0
    produced = list(report_dir.glob("*.json"))
    assert produced
    data = json.loads(produced[0].read_text())
    assert data["board_name"] == "Main"
    assert data["results"][0]["verdict"] == "PASS"


def test_run_test_aborts_when_not_approved(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_VOLTAGE", "15.0")
    monkeypatch.setenv("MAX_CURRENT", "2.0")
    schdoc = _schdoc_path()
    report_dir = tmp_path / "reports"

    with patch("cli.run_test.generate_plan", return_value=_plan()), \
         patch("cli.run_test.Dps150Adapter") as psu_ctor, \
         patch("cli.run_test.AnalogDiscoveryScopeAdapter") as scope_ctor, \
         patch("sys.stdin", io.StringIO("n\n")):
        rc = main([str(schdoc), "--report-dir", str(report_dir)])

    assert rc == 1
    psu_ctor.assert_not_called()
    scope_ctor.assert_not_called()
