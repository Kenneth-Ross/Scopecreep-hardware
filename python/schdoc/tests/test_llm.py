import json
from unittest.mock import MagicMock, patch
from schdoc.models import SchematicSummary, PowerRail, Zone, Component, ProbePoint
from schdoc.llm import generate_understanding, _fallback_understanding


def _make_summary() -> SchematicSummary:
    rail = PowerRail(name="3V3", nominal_voltage=3.3, source_designator="U1", loads=["U2"])
    comp = Component(
        designator="U1", value="TPS62932", description="Synchronous buck converter",
        part_number="", manufacturer="TI", footprint="", is_connector=False, pins=[],
    )
    zone = Zone(name="Power Tree", components=[comp], key_nets=["3V3"])
    probe = ProbePoint(
        label="J3.1", net="3V3", designator="J3", pin_name="1",
        pin_number="1", probe_type="physical_tp", expected_range="3.3V ± 5%",
    )
    return SchematicSummary(
        board_name="Main",
        understanding="",
        power_rails=[rail],
        zones=[zone],
        connectors=[],
        probe_points=[probe],
        nets={},
    )


def test_fallback_understanding_contains_board_name():
    summary = _make_summary()
    result = _fallback_understanding(summary)
    assert "Main" in result or "3V3" in result


def test_fallback_understanding_mentions_zones():
    summary = _make_summary()
    result = _fallback_understanding(summary)
    assert "Power Tree" in result


def test_generate_understanding_uses_llm(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    mock_client = MagicMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="This board is a vehicle interface.")]
    mock_client.messages.create.return_value = mock_msg

    with patch("schdoc.llm.anthropic.Anthropic", return_value=mock_client):
        summary = _make_summary()
        result = generate_understanding(summary)

    assert result == "This board is a vehicle interface."
    mock_client.messages.create.assert_called_once()
    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-haiku-4-5-20251001"
    assert call_kwargs["max_tokens"] == 512


def test_generate_understanding_falls_back_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    summary = _make_summary()
    result = generate_understanding(summary)
    assert len(result) > 10


def test_generate_understanding_falls_back_on_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with patch("schdoc.llm.anthropic.Anthropic", side_effect=Exception("API down")):
        summary = _make_summary()
        result = generate_understanding(summary)
    assert len(result) > 10
