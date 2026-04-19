import asyncio
from unittest.mock import MagicMock, patch

import pytest

from evaluator.tier2 import evaluate_tier2, Tier2Error


def _resp(content):
    msg = MagicMock(); msg.content = content
    choice = MagicMock(); choice.message = msg
    r = MagicMock(); r.choices = [choice]
    return r


def test_happy_path(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with patch("evaluator.tier2.OpenAI") as m:
        m.return_value.chat.completions.create.return_value = _resp(
            '{"verdict":"PASS","reasoning":"within noise band"}'
        )
        v, r = asyncio.run(evaluate_tier2({"v_mean": 3.45}, "3.3V ± 5%", {}, "ctx"))
    assert v == "PASS"
    assert "noise" in r


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(Tier2Error):
        asyncio.run(evaluate_tier2({}, "x", {}, ""))


def test_bad_json_raises(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with patch("evaluator.tier2.OpenAI") as m:
        m.return_value.chat.completions.create.return_value = _resp("not json")
        with pytest.raises(Exception):
            asyncio.run(evaluate_tier2({}, "x", {}, ""))


def test_bad_verdict_raises(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with patch("evaluator.tier2.OpenAI") as m:
        m.return_value.chat.completions.create.return_value = _resp('{"verdict":"MAYBE","reasoning":"x"}')
        with pytest.raises(Tier2Error):
            asyncio.run(evaluate_tier2({}, "x", {}, ""))
