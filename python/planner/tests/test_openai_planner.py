import json
import os
from unittest.mock import MagicMock, patch

import pytest

from planner.models import TestPlan
from planner.openai_planner import generate_plan, PlannerError


class _Summary:
    board_name = "Main"
    power_rails = []
    zones = []
    connectors = []
    probe_points = []


def _fake_response(plan_dict):
    msg = MagicMock()
    msg.content = json.dumps(plan_dict)
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


VALID_PLAN = {
    "board_name": "Main",
    "summary": "ok",
    "test_cases": [{
        "id": "TC-01",
        "title": "3V3 rail nominal",
        "rationale": "why",
        "psu": {"channel": "main", "voltage": 12.0, "current_limit": 0.5, "rail_name": "12V"},
        "probe": {
            "label": "TP3", "net": "VCC_3V3", "location_hint": "C12",
            "probe_type": "physical_tp", "scope_channel": "CH1",
            "user_instructions": "Place probe."
        },
        "expected": {"kind": "pct", "nominal": 3.3, "tolerance": 5.0, "lower": None, "upper": None},
        "measurement": "v_mean",
    }],
}


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(PlannerError, match="OPENAI_API_KEY"):
        generate_plan(_Summary())


def test_happy_path(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with patch("planner.openai_planner.OpenAI") as m:
        m.return_value.chat.completions.create.return_value = _fake_response(VALID_PLAN)
        plan = generate_plan(_Summary())
    assert isinstance(plan, TestPlan)
    assert plan.test_cases[0].id == "TC-01"


def test_bad_schema_raises(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    bad = {"board_name": "x"}
    with patch("planner.openai_planner.OpenAI") as m:
        m.return_value.chat.completions.create.return_value = _fake_response(bad)
        with pytest.raises(PlannerError, match="schema"):
            generate_plan(_Summary())


def test_strictify_makes_every_object_additional_properties_false():
    from planner.openai_planner import _strictify
    from planner.models import TestPlan as _TP
    schema = _strictify(_TP.model_json_schema())

    def walk(s):
        if isinstance(s, dict):
            if s.get("type") == "object":
                assert s.get("additionalProperties") is False
                assert set(s.get("required", [])) == set(s.get("properties", {}).keys())
            for v in s.values():
                walk(v)
        elif isinstance(s, list):
            for v in s:
                walk(v)

    walk(schema)


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="no OPENAI_API_KEY")
def test_integration_real_openai():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from schdoc.parser import parse  # entry point is named `parse`, not `parse_schdoc`

    schdoc_path = Path(__file__).resolve().parents[3] / "Main.SchDoc"
    if not schdoc_path.exists():
        pytest.skip(f"Main.SchDoc not found at {schdoc_path}")

    summary = parse(str(schdoc_path))
    plan = generate_plan(summary)

    assert plan.board_name
    assert len(plan.test_cases) >= 1
    max_v = float(os.environ.get("MAX_VOLTAGE", "5.0"))
    for tc in plan.test_cases:
        assert 0 <= tc.psu.voltage <= max_v, (tc.id, tc.psu.voltage)
        assert tc.psu.current_limit > 0
