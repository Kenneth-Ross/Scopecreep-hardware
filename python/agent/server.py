# python/agent/server.py
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .models import HardwareContext, SessionState, TestSession
from .runner import run_session
from .config import SCOPE_BITSTREAM, SCOPE_URL, SESSION_TTL_SECONDS
from drivers.analog_discovery.driver import AnalogDiscovery

router = APIRouter(prefix="/agent")

_sessions: dict[str, TestSession] = {}


class StartSessionRequest(BaseModel):
    schematic: dict[str, Any]
    config: dict[str, Any] = {}


@router.post("/sessions")
async def start_session(req: StartSessionRequest):
    bitstream = req.config.get("scope_bitstream", SCOPE_BITSTREAM)
    url = req.config.get("scope_url", SCOPE_URL)

    device = AnalogDiscovery(bitstream)
    device.connect(url=url)

    hw = HardwareContext(analog_discovery=device)
    session = TestSession(
        schematic=req.schematic,
        total_probe_points=len(req.schematic.get("probe_points", [])),
    )
    _sessions[session.session_id] = session

    asyncio.create_task(run_session(session, hw))

    return {"session_id": session.session_id, "status": session.state.value}


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    session = _require_session(session_id)
    resp: dict[str, Any] = {
        "session_id": session.session_id,
        "status": session.state.value,
        "progress": {
            "completed": len(session.results),
            "total": session.total_probe_points,
        },
    }
    if session.state == SessionState.PROBE_REQUIRED and session.current_probe:
        p = session.current_probe
        resp["current_probe"] = {
            "label": p.probe_point_label,
            "net": p.net,
            "location_hint": p.location_hint,
            "probe_type": p.probe_type,
            "instructions": p.instructions,
        }
    if session.error:
        resp["error"] = session.error
    return resp


@router.post("/sessions/{session_id}/resume")
def resume_session(session_id: str):
    session = _require_session(session_id)
    if session.state != SessionState.PROBE_REQUIRED:
        raise HTTPException(
            status_code=400,
            detail=f"Session is not awaiting a probe (current state: {session.state.value})",
        )
    session._resume_event.set()
    return {"status": "capturing"}


@router.get("/sessions/{session_id}/report")
def get_report(session_id: str):
    session = _require_session(session_id)
    if session.state not in (SessionState.COMPLETE, SessionState.FAILED):
        raise HTTPException(status_code=202, detail="Session not yet complete")

    overall = "PASS"
    if any(r.verdict == "FAIL" for r in session.results):
        overall = "FAIL"
    elif any(r.verdict == "MARGINAL" for r in session.results):
        overall = "PARTIAL"

    return {
        "session_id": session.session_id,
        "board_name": session.schematic.get("board_name", ""),
        "overall": overall,
        "results": [
            {
                "probe_point": r.probe_point_label,
                "net": r.net,
                "verdict": r.verdict,
                "expected_range": r.expected_range,
                "measurements": r.measurements,
                "reasoning": r.reasoning,
                "tier": r.tier,
            }
            for r in session.results
        ],
    }


@router.delete("/sessions/{session_id}")
def cancel_session(session_id: str):
    session = _sessions.pop(session_id, None)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    session.state = SessionState.FAILED
    session.error = "Cancelled by client"
    session._resume_event.set()
    return {"status": "cancelled"}


def _require_session(session_id: str) -> TestSession:
    s = _sessions.get(session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if time.time() - s.created_at > SESSION_TTL_SECONDS:
        del _sessions[session_id]
        raise HTTPException(status_code=404, detail="Session expired")
    return s
