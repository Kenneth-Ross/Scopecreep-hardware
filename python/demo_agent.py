"""
End-to-end agent orchestration demo — no hardware, no API key required.

Parses the real Main.SchDoc, then walks a full test session through every
state (planning → probe_required → capturing → evaluating → complete) using
stub hardware. Prints each step so you can follow the flow.

Run from python/:
    python demo_agent.py
"""

import asyncio
import json
import numpy as np
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

SCHEMATIC_PATH = Path(__file__).parent.parent / "Main.SchDoc"


# ---------------------------------------------------------------------------
# 1. Parse the real schematic
# ---------------------------------------------------------------------------

def load_schematic() -> dict:
    from schdoc.parser import parse
    s = parse(SCHEMATIC_PATH)
    # Add a manual 3V3 power rail probe point since the parser found CAN TPs
    return {
        "board_name": s.board_name,
        "understanding": (
            "CAN bus interface board. 3V3 rail powers the MCU and CAN transceiver. "
            "12V is the main supply. CANPT_H/L and CANVEH_H/L are the two CAN bus sides."
        ),
        "power_rails": [
            {"name": r.name, "nominal_voltage": r.nominal_voltage}
            for r in s.power_rails
        ],
        "probe_points": [
            {
                "label": "3V3_RAIL",
                "net": "3V3",
                "expected_range": "3.3V ± 5%",
                "probe_type": "power_rail",
                "designator": "U1",
                "pin_name": "VOUT",
                "pin_number": "1",
            },
            {
                "label": "J5.1",
                "net": "CANPT_H",
                "expected_range": "CAN differential 1.5–3.0V (active)",
                "probe_type": "physical_tp",
                "designator": "J5",
                "pin_name": "1",
                "pin_number": "1",
            },
        ],
    }


# ---------------------------------------------------------------------------
# 2. Stub hardware
# ---------------------------------------------------------------------------

def make_stub_hw(v_mean: float = 3.31):
    """Return a MagicMock AnalogDiscovery whose scope always reads v_mean."""
    from agent.models import HardwareContext
    ad = MagicMock()
    ad.psu.set_voltage = MagicMock()
    ad.psu.enable = MagicMock()
    ad.scope.configure_channel = MagicMock()
    ad.scope.arm_trigger = MagicMock()
    ad.scope.read_samples = MagicMock(return_value=np.full(1024, v_mean))
    return HardwareContext(analog_discovery=ad)


# ---------------------------------------------------------------------------
# 3. State machine walkthrough
# ---------------------------------------------------------------------------

async def run_session_demo(schematic: dict):
    from agent.models import TestSession, SessionState
    from agent.tools import dispatch_tool

    session = TestSession(schematic=schematic, total_probe_points=len(schematic["probe_points"]))
    hw = make_stub_hw(v_mean=3.31)

    def banner(title):
        print(f"\n{'─' * 60}")
        print(f"  {title}")
        print(f"{'─' * 60}")

    def show_state(result=None):
        if result:
            print(f"  tool result → {json.dumps(result)}")
        print(f"  session.state = {session.state.value}")
        if session.current_probe:
            p = session.current_probe
            print(f"  current_probe = {p.probe_point_label} | {p.instructions}")
        if session.error:
            print(f"  session.error = {session.error}")

    # -- PSU on -----------------------------------------------------------
    banner("psu_configure: power up 3.3 V rail")
    r = await dispatch_tool("psu_configure", {"channel": 0, "voltage": 3.3, "enabled": True}, session, hw)
    show_state(r)
    hw.analog_discovery.psu.set_voltage.assert_called_once_with(0, 3.3)
    print("  ✓ set_voltage(0, 3.3) was called on hardware")

    # -- require_probe ----------------------------------------------------
    banner("require_probe: pause for user to place CH1 on 3V3 net")
    r = await dispatch_tool("require_probe", {
        "probe_point_label": "3V3_RAIL",
        "net": "3V3",
        "location_hint": "U1 pin 1, near C5",
        "probe_type": "power_rail",
        "instructions": "Place CH1 probe tip on 3V3 net. GND clip to GND plane.",
    }, session, hw)
    show_state(r)
    assert session.state == SessionState.PROBE_REQUIRED

    # -- /resume ----------------------------------------------------------
    banner("/resume: user placed probe, session unblocked")
    session._resume_event.set()
    session.state = SessionState.CAPTURING
    show_state()

    # -- scope_capture ----------------------------------------------------
    banner("scope_capture: read 1024 samples from CH0")
    r = await dispatch_tool("scope_capture", {
        "channel": 0,
        "voltage_range": 5,
        "sample_rate": 1_000_000,
        "num_samples": 1024,
        "trigger_source": "none",
        "trigger_level": 0.0,
        "trigger_edge": "rising",
    }, session, hw)
    show_state({k: round(v, 4) if isinstance(v, float) else v for k, v in r.items()})
    assert abs(r["v_mean"] - 3.31) < 0.001

    # -- record_result (tier-1 evaluates) ---------------------------------
    banner("record_result: tier-1 evaluator judges 3.31 V against '3.3V ± 5%'")
    r = await dispatch_tool("record_result", {
        "probe_point_label": "3V3_RAIL",
        "verdict": "PASS",
        "reasoning": "v_mean 3.31 V is within the 3.135–3.465 V band.",
        "measurements": {"v_mean": r["v_mean"], "v_pp": r["v_pp"], "v_rms": r["v_rms"]},
    }, session, hw)
    show_state(r)
    assert session.results[0].tier == 1
    assert session.results[0].verdict == "PASS"
    print(f"  ✓ tier-1 verdict: {session.results[0].verdict}")

    # -- safety clamp demo ------------------------------------------------
    banner("psu_configure: attempt 12 V (over 5 V limit) → session FAILED")
    session2 = TestSession(schematic=schematic, total_probe_points=0)
    hw2 = make_stub_hw()
    r = await dispatch_tool("psu_configure", {"channel": 0, "voltage": 12.0, "enabled": True}, session2, hw2)
    print(f"  tool result → {json.dumps(r)}")
    print(f"  session.state = {session2.state.value}")
    if session2.error:
        print(f"  session.error = {session2.error}")
    assert session2.state == SessionState.FAILED
    hw2.analog_discovery.psu.set_voltage.assert_not_called()
    print("  ✓ set_voltage was NOT called — hardware protected")

    # -- final report -----------------------------------------------------
    banner("Session report")
    session.state = SessionState.COMPLETE
    verdicts = [r.verdict for r in session.results]
    overall = "PASS" if all(v == "PASS" for v in verdicts) else "FAIL"
    print(f"  board_name : {schematic['board_name']}")
    print(f"  overall    : {overall}")
    for res in session.results:
        print(f"  {res.probe_point_label:15s}  {res.verdict:8s}  tier={res.tier}  {res.reasoning}")


# ---------------------------------------------------------------------------
# 4. REST endpoint walkthrough
# ---------------------------------------------------------------------------

def run_rest_demo(schematic: dict):
    def banner(title):
        print(f"\n{'─' * 60}")
        print(f"  {title}")
        print(f"{'─' * 60}")

    app = FastAPI()
    with patch("agent.server.AnalogDiscovery"), \
         patch("agent.server.run_session", new=AsyncMock()):
        from agent.server import router
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=True)

        banner("POST /agent/sessions")
        r = client.post("/agent/sessions", json={"schematic": schematic})
        sid = r.json()["session_id"]
        print(f"  {r.status_code}  session_id={sid}  status={r.json()['status']}")

        banner("GET /agent/sessions/{id}")
        r = client.get(f"/agent/sessions/{sid}")
        print(f"  {r.status_code}  {json.dumps(r.json(), indent=4)}")

        banner("POST /resume while state=planning → 400")
        r = client.post(f"/agent/sessions/{sid}/resume")
        print(f"  {r.status_code}  {r.json()}")

        banner("Inject probe_required state then GET")
        from agent.server import _sessions
        from agent.models import SessionState, ProbeInstruction
        _sessions[sid].state = SessionState.PROBE_REQUIRED
        _sessions[sid].current_probe = ProbeInstruction(
            probe_point_label="3V3_RAIL", net="3V3",
            location_hint="U1 pin 1, near C5", probe_type="power_rail",
            instructions="Place CH1 probe tip on 3V3 net. GND clip to GND plane.",
        )
        _sessions[sid]._resume_event = asyncio.Event()
        r = client.get(f"/agent/sessions/{sid}")
        print(f"  {r.status_code}  {json.dumps(r.json(), indent=4)}")

        banner("POST /resume → 200 capturing")
        r = client.post(f"/agent/sessions/{sid}/resume")
        print(f"  {r.status_code}  {r.json()}")

        banner("GET /report while not complete → 202")
        _sessions[sid].state = SessionState.PLANNING
        r = client.get(f"/agent/sessions/{sid}/report")
        print(f"  {r.status_code}  {r.json()}")

        banner("GET /report after complete")
        from agent.models import TestResult
        _sessions[sid].state = SessionState.COMPLETE
        _sessions[sid].results.append(TestResult(
            probe_point_label="3V3_RAIL", net="3V3", verdict="PASS",
            expected_range="3.3V ± 5%", measurements={"v_mean": 3.31},
            reasoning="Within band.", tier=1,
        ))
        r = client.get(f"/agent/sessions/{sid}/report")
        print(f"  {r.status_code}  {json.dumps(r.json(), indent=4)}")

        banner("DELETE /agent/sessions/{id}")
        r = client.delete(f"/agent/sessions/{sid}")
        print(f"  {r.status_code}  {r.json()}")
        r = client.get(f"/agent/sessions/{sid}")
        print(f"  GET after delete → {r.status_code}  {r.json()}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("  jbhack agent orchestration demo")
    print(f"  schematic: {SCHEMATIC_PATH.name}")
    print("=" * 60)

    schematic = load_schematic()
    print(f"\nParsed schematic '{schematic['board_name']}':")
    print(f"  power rails   : {[r['name'] for r in schematic['power_rails']]}")
    print(f"  probe points  : {[p['label'] for p in schematic['probe_points']]}")

    print("\n\n=== PART 1: State machine + tools ===")
    asyncio.run(run_session_demo(schematic))

    print("\n\n=== PART 2: REST endpoints ===")
    run_rest_demo(schematic)

    print("\n\nAll assertions passed.\n")
