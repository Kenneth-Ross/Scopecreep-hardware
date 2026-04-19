import pytest
from pydantic import ValidationError
from planner.models import TestPlan, TestCase, PsuSetting, ProbeStep, ExpectedRange, expected_range_to_legacy


def _valid_plan() -> dict:
    return {
        "board_name": "Main",
        "summary": "Test board with 12V input and 3V3 LDO.",
        "test_cases": [{
            "id": "TC-01",
            "title": "3V3 rail nominal",
            "rationale": "Verify U2 LDO output is in spec.",
            "psu": {"channel": "main", "voltage": 12.0, "current_limit": 0.5, "rail_name": "12V"},
            "probe": {
                "label": "TP3", "net": "VCC_3V3",
                "location_hint": "C12 left pad",
                "probe_type": "physical_tp",
                "scope_channel": "CH1",
                "user_instructions": "Place CH1 tip on TP3, GND clip to via."
            },
            "expected": {"kind": "pct", "nominal": 3.3, "tolerance": 5.0},
            "measurement": "v_mean",
        }],
    }


def test_accepts_valid_plan():
    plan = TestPlan.model_validate(_valid_plan())
    assert plan.board_name == "Main"
    assert plan.test_cases[0].psu.voltage == 12.0


def test_rejects_missing_board_name():
    bad = _valid_plan()
    del bad["board_name"]
    with pytest.raises(ValidationError):
        TestPlan.model_validate(bad)


def test_rejects_bad_probe_type():
    bad = _valid_plan()
    bad["test_cases"][0]["probe"]["probe_type"] = "bogus"
    with pytest.raises(ValidationError):
        TestPlan.model_validate(bad)


def test_rejects_negative_voltage():
    bad = _valid_plan()
    bad["test_cases"][0]["psu"]["voltage"] = -1.0
    with pytest.raises(ValidationError):
        TestPlan.model_validate(bad)


def test_pct_legacy():
    e = ExpectedRange(kind="pct", nominal=3.3, tolerance=5.0)
    assert expected_range_to_legacy(e) == "3.3V ± 5.0%"


def test_range_legacy():
    e = ExpectedRange(kind="range", lower=0.0, upper=0.5)
    assert expected_range_to_legacy(e) == "0.0-0.5V"
