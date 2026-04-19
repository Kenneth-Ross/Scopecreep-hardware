import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

SCHEMATIC = {
    "board_name": "TestBoard",
    "understanding": "A test board.",
    "probe_points": [
        {"label": "TP1", "net": "VCC_3V3", "expected_range": "3.3V ± 5%",
         "probe_type": "power_rail", "designator": "TP1", "pin_name": "1", "pin_number": "1"},
    ],
}


def _make_client():
    """Return TestClient with hardware and runner mocked."""
    from fastapi import FastAPI
    from agent.server import router

    app = FastAPI()
    app.include_router(router)

    with patch("agent.server.AnalogDiscovery") as mock_ad_cls, \
         patch("agent.server.run_session", new=AsyncMock()):
        mock_ad = MagicMock()
        mock_ad_cls.return_value = mock_ad
        client = TestClient(app, raise_server_exceptions=False)
        yield client


@pytest.fixture
def client():
    yield from _make_client()


def _start_session(client):
    return client.post("/agent/sessions", json={"schematic": SCHEMATIC})


def test_start_session_returns_session_id(client):
    r = _start_session(client)
    assert r.status_code == 200
    data = r.json()
    assert "session_id" in data
    assert data["status"] == "planning"


def test_get_session_returns_status(client):
    r = _start_session(client)
    sid = r.json()["session_id"]
    r2 = client.get(f"/agent/sessions/{sid}")
    assert r2.status_code == 200
    assert r2.json()["session_id"] == sid
    assert "status" in r2.json()
    assert "progress" in r2.json()


def test_get_session_not_found(client):
    r = client.get("/agent/sessions/does-not-exist")
    assert r.status_code == 404


def test_resume_wrong_state_returns_400(client):
    r = _start_session(client)
    sid = r.json()["session_id"]
    r2 = client.post(f"/agent/sessions/{sid}/resume")
    assert r2.status_code == 400


def test_resume_probe_required_state(client):
    from agent.models import SessionState
    r = _start_session(client)
    sid = r.json()["session_id"]

    from agent.server import _sessions
    _sessions[sid].state = SessionState.PROBE_REQUIRED
    _sessions[sid]._resume_event = asyncio.Event()

    r2 = client.post(f"/agent/sessions/{sid}/resume")
    assert r2.status_code == 200
    assert r2.json()["status"] == "resuming"


def test_get_report_not_complete_returns_202(client):
    r = _start_session(client)
    sid = r.json()["session_id"]
    r2 = client.get(f"/agent/sessions/{sid}/report")
    assert r2.status_code == 202


def test_get_report_complete(client):
    from agent.models import SessionState, TestResult
    r = _start_session(client)
    sid = r.json()["session_id"]

    from agent.server import _sessions
    session = _sessions[sid]
    session.state = SessionState.COMPLETE
    session.results.append(TestResult(
        probe_point_label="TP1", net="VCC_3V3", verdict="PASS",
        expected_range="3.3V ± 5%", measurements={"v_mean": 3.31},
        reasoning="In range.", tier=1,
    ))

    r2 = client.get(f"/agent/sessions/{sid}/report")
    assert r2.status_code == 200
    data = r2.json()
    assert data["overall"] == "PASS"
    assert len(data["results"]) == 1


def test_cancel_session(client):
    r = _start_session(client)
    sid = r.json()["session_id"]
    r2 = client.delete(f"/agent/sessions/{sid}")
    assert r2.status_code == 200
    assert r2.json()["status"] == "cancelled"
    r3 = client.get(f"/agent/sessions/{sid}")
    assert r3.status_code == 404


def test_cancel_not_found(client):
    r = client.delete("/agent/sessions/no-such-id")
    assert r.status_code == 404
