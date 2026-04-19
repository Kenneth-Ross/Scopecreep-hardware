from unittest.mock import MagicMock, patch

from schdoc.models import SchematicSummary, PowerRail
from schdoc.llm import generate_understanding, _fallback_understanding


def _summary():
    return SchematicSummary(
        board_name="Main",
        understanding="",
        power_rails=[PowerRail(name="3V3", nominal_voltage=3.3, source_designator="U2", loads=["U3"])],
        zones=[],
        connectors=[],
        probe_points=[],
    )


def test_fallback_when_no_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert "3V3" in generate_understanding(_summary())


def test_openai_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    msg = MagicMock(); msg.content = "The board has a 3V3 LDO U2 feeding U3."
    choice = MagicMock(); choice.message = msg
    resp = MagicMock(); resp.choices = [choice]

    with patch("schdoc.llm.OpenAI") as m:
        m.return_value.chat.completions.create.return_value = resp
        result = generate_understanding(_summary())
        assert "3V3" in result


def test_openai_error_falls_back(monkeypatch):
    from openai import OpenAIError
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with patch("schdoc.llm.OpenAI") as m:
        m.return_value.chat.completions.create.side_effect = OpenAIError("boom")
        assert "3V3" in generate_understanding(_summary())
