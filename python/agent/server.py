# python/agent/server.py
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .models import HardwareContext, SessionState, TestSession
from .runner import run_session
from .config import SCOPE_BACKEND, SCOPE_BITSTREAM, SCOPE_URL, SESSION_TTL_SECONDS


class _LazyDevice:
    """Defer the real driver's ``connect()`` until first attribute access.

    ``start_session`` should succeed on machines without the Analog Discovery
    plugged in so the UI (planning, schematic preview, report scaffolding)
    can be exercised without hardware. The PyDwfError only surfaces when a
    tool call actually touches psu/scope/dio — at which point the runner's
    try/except marks the session FAILED with a useful message instead of
    returning HTTP 500 at the REST layer.

    This is not a mock: the first hardware access still constructs the real
    driver and calls its real ``connect()``. Only the timing changes.
    """

    def __init__(self, factory):
        self._factory = factory
        self._inner = None

    def _get(self):
        if self._inner is None:
            self._inner = self._factory()
        return self._inner

    def __getattr__(self, name: str):
        # __getattr__ runs only when normal attribute lookup fails, so
        # references to _factory / _inner on self don't recurse.
        return getattr(self._get(), name)

    def disconnect(self) -> None:
        if self._inner is not None:
            try:
                self._inner.disconnect()
            finally:
                self._inner = None


def _build_device(backend: str, bitstream: str, url: str):
    """Pick the hardware driver implementation by backend name.

    Returns a [_LazyDevice] — no USB / FTDI I/O happens until the runner
    actually touches the device via a tool call.
    """
    if backend == "waveforms":
        def factory():
            from drivers.analog_discovery.waveforms_driver import WaveFormsAnalogDiscovery
            device = WaveFormsAnalogDiscovery()
            device.connect()
            return device
        return _LazyDevice(factory)
    if backend == "pti":
        def factory():
            from drivers.analog_discovery.driver import AnalogDiscovery
            device = AnalogDiscovery(bitstream)
            device.connect(url=url)
            return device
        return _LazyDevice(factory)
    raise ValueError(f"Unknown SCOPE_BACKEND {backend!r}. Valid: waveforms, pti")

router = APIRouter(prefix="/agent")

_sessions: dict[str, TestSession] = {}


class StartSessionRequest(BaseModel):
    schematic: dict[str, Any]
    config: dict[str, Any] = {}


@router.post("/sessions")
async def start_session(req: StartSessionRequest):
    backend = req.config.get("scope_backend", SCOPE_BACKEND)
    bitstream = req.config.get("scope_bitstream", SCOPE_BITSTREAM)
    url = req.config.get("scope_url", SCOPE_URL)

    device = _build_device(backend, bitstream, url)

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
