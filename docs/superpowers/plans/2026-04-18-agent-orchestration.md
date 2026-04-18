# Agent Orchestration Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an end-to-end Python orchestration layer that reads a parsed schematic, drives Claude in a tool-calling loop to control real PSU and oscilloscope hardware, pauses for user probe placement, and produces a structured pass/fail test report exposed via a REST API.

**Architecture:** A `TestSession` dataclass tracks state through a simple enum (`planning → probe_required → capturing → evaluating → complete/failed`). An async Claude tool-use loop calls four tools (`psu_configure`, `scope_capture`, `require_probe`, `record_result`); `require_probe` blocks the loop on an `asyncio.Event` until the plugin calls `/agent/sessions/{id}/resume`. Pass/fail is determined by a two-tier evaluator: deterministic numeric thresholds first, Claude interpretation only for marginal results.

**Tech Stack:** Python 3.11+, FastAPI, `anthropic>=0.40`, numpy, pytest, asyncio

---

## File Map

| Path | Responsibility |
|---|---|
| `python/agent/__init__.py` | Package root |
| `python/agent/config.py` | Env-configurable constants |
| `python/agent/models.py` | `SessionState`, `TestSession`, `ProbeInstruction`, `TestResult`, `HardwareContext` |
| `python/agent/evaluator.py` | Tier-1 range parser + numeric verdict; Tier-2 Claude interpretation |
| `python/agent/tools.py` | Tool JSON schemas + async handler functions |
| `python/agent/runner.py` | Async Claude tool-use loop + system prompt builder |
| `python/agent/server.py` | FastAPI router (`/agent/sessions` CRUD + `/resume`) |
| `python/agent/tests/__init__.py` | Test package |
| `python/agent/tests/test_evaluator.py` | Evaluator unit tests |
| `python/agent/tests/test_tools.py` | Tool handler tests (mocked hardware) |
| `python/agent/tests/test_runner.py` | Runner tests (mocked Claude + hardware) |
| `python/agent/tests/test_server.py` | FastAPI route tests (TestClient) |
| `python/api/server.py` | Modify: mount agent router |

All tests run from the `python/` directory: `python -m pytest agent/tests/ -v`

---

## Task 1: Package skeleton and config

**Files:**
- Create: `python/agent/__init__.py`
- Create: `python/agent/config.py`
- Create: `python/agent/tests/__init__.py`

- [ ] **Step 1: Create the package files**

```bash
mkdir -p python/agent/tests
touch python/agent/__init__.py python/agent/tests/__init__.py
```

- [ ] **Step 2: Write failing test**

Create `python/agent/tests/test_config.py`:

```python
def test_config_imports():
    from agent.config import (
        AGENT_MODEL, AGENT_MAX_TOKENS, AGENT_MAX_TOOL_ROUNDS,
        SCOPE_BITSTREAM, SCOPE_URL, MAX_VOLTAGE, MAX_CURRENT,
        SESSION_TTL_SECONDS,
    )
    assert isinstance(AGENT_MODEL, str)
    assert AGENT_MAX_TOKENS > 0
    assert AGENT_MAX_TOOL_ROUNDS > 0
    assert MAX_VOLTAGE > 0
    assert MAX_CURRENT > 0
    assert SESSION_TTL_SECONDS > 0
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd python && python -m pytest agent/tests/test_config.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 4: Create config.py**

```python
# python/agent/config.py
import os

AGENT_MODEL = os.getenv("AGENT_MODEL", "claude-sonnet-4-6")
AGENT_MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "4096"))
AGENT_MAX_TOOL_ROUNDS = int(os.getenv("AGENT_MAX_TOOL_ROUNDS", "30"))
SCOPE_BITSTREAM = os.getenv("SCOPE_BITSTREAM", "")
SCOPE_URL = os.getenv("SCOPE_URL", "ftdi://0x0403:0x6014/1")
MAX_VOLTAGE = float(os.getenv("MAX_VOLTAGE", "5.0"))
MAX_CURRENT = float(os.getenv("MAX_CURRENT", "1.0"))
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd python && python -m pytest agent/tests/test_config.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add python/agent/ && git commit -m "feat: add agent package skeleton and config"
```

---

## Task 2: Session models

**Files:**
- Create: `python/agent/models.py`
- Create: `python/agent/tests/test_models.py`

- [ ] **Step 1: Write failing tests**

Create `python/agent/tests/test_models.py`:

```python
import asyncio
import pytest
from agent.models import SessionState, TestSession, ProbeInstruction, TestResult, HardwareContext


def test_session_default_state():
    s = TestSession(schematic={})
    assert s.state == SessionState.PLANNING
    assert s.results == []
    assert s.current_probe is None
    assert s.error is None
    assert s.total_probe_points == 0


def test_session_has_resume_event():
    s = TestSession(schematic={})
    assert not s._resume_event.is_set()


def test_session_id_is_unique():
    a = TestSession(schematic={})
    b = TestSession(schematic={})
    assert a.session_id != b.session_id


def test_probe_instruction_fields():
    p = ProbeInstruction(
        probe_point_label="TP3",
        net="VCC_3V3",
        location_hint="C12 drain",
        probe_type="power_rail",
        instructions="Place CH1 on TP3.",
    )
    assert p.probe_point_label == "TP3"
    assert p.net == "VCC_3V3"


def test_test_result_fields():
    r = TestResult(
        probe_point_label="TP3",
        net="VCC_3V3",
        verdict="PASS",
        expected_range="3.3V ± 5%",
        measurements={"v_mean": 3.31},
        reasoning="Within tolerance.",
        tier=1,
    )
    assert r.verdict == "PASS"
    assert r.tier == 1


def test_hardware_context_holds_device():
    hw = HardwareContext(analog_discovery=object())
    assert hw.analog_discovery is not None


def test_session_state_enum_values():
    assert SessionState.PLANNING == "planning"
    assert SessionState.PROBE_REQUIRED == "probe_required"
    assert SessionState.CAPTURING == "capturing"
    assert SessionState.EVALUATING == "evaluating"
    assert SessionState.COMPLETE == "complete"
    assert SessionState.FAILED == "failed"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd python && python -m pytest agent/tests/test_models.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create models.py**

```python
# python/agent/models.py
from __future__ import annotations
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SessionState(str, Enum):
    PLANNING = "planning"
    PROBE_REQUIRED = "probe_required"
    CAPTURING = "capturing"
    EVALUATING = "evaluating"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class ProbeInstruction:
    probe_point_label: str
    net: str
    location_hint: str
    probe_type: str
    instructions: str


@dataclass
class TestResult:
    probe_point_label: str
    net: str
    verdict: str          # "PASS" | "FAIL" | "MARGINAL"
    expected_range: str
    measurements: dict[str, Any]
    reasoning: str
    tier: int             # 1 or 2


@dataclass
class HardwareContext:
    """Holds live hardware driver handles for the duration of a session."""
    analog_discovery: Any   # AnalogDiscovery instance


@dataclass
class TestSession:
    schematic: dict[str, Any]
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: SessionState = SessionState.PLANNING
    current_probe: ProbeInstruction | None = None
    results: list[TestResult] = field(default_factory=list)
    total_probe_points: int = 0
    error: str | None = None
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self._resume_event: asyncio.Event = asyncio.Event()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd python && python -m pytest agent/tests/test_models.py -v
```

Expected: all 7 PASS

- [ ] **Step 5: Commit**

```bash
git add python/agent/models.py python/agent/tests/test_models.py
git commit -m "feat: add agent session models"
```

---

## Task 3: Tier-1 evaluator

**Files:**
- Create: `python/agent/evaluator.py`
- Create: `python/agent/tests/test_evaluator.py`

- [ ] **Step 1: Write failing tests**

Create `python/agent/tests/test_evaluator.py`:

```python
import pytest
from agent.evaluator import parse_expected_range, evaluate_tier1


# --- parse_expected_range ---

def test_parse_pct_tolerance():
    b = parse_expected_range("3.3V ± 5%")
    assert abs(b.lower - 3.135) < 1e-9
    assert abs(b.upper - 3.465) < 1e-9


def test_parse_mv_tolerance():
    b = parse_expected_range("3.3V ± 165mV")
    assert abs(b.lower - 3.135) < 1e-9
    assert abs(b.upper - 3.465) < 1e-9


def test_parse_volt_tolerance():
    b = parse_expected_range("3.3V ± 0.165V")
    assert abs(b.lower - 3.135) < 1e-9
    assert abs(b.upper - 3.465) < 1e-9


def test_parse_greater_than():
    b = parse_expected_range("> 2.5V")
    assert abs(b.lower - 2.5) < 1e-9
    assert b.upper is None


def test_parse_less_than():
    b = parse_expected_range("< 0.5V")
    assert b.lower is None
    assert abs(b.upper - 0.5) < 1e-9


def test_parse_range_hyphen():
    b = parse_expected_range("0-0.5V")
    assert abs(b.lower - 0.0) < 1e-9
    assert abs(b.upper - 0.5) < 1e-9


def test_parse_range_endash():
    b = parse_expected_range("0–0.5V")
    assert abs(b.lower - 0.0) < 1e-9
    assert abs(b.upper - 0.5) < 1e-9


def test_parse_unknown_raises():
    with pytest.raises(ValueError, match="Cannot parse"):
        parse_expected_range("something invalid")


# --- evaluate_tier1 ---

def test_tier1_pass_symmetric():
    verdict = evaluate_tier1({"v_mean": 3.31}, "3.3V ± 5%")
    assert verdict == "PASS"


def test_tier1_pass_at_boundary():
    verdict = evaluate_tier1({"v_mean": 3.135}, "3.3V ± 5%")
    assert verdict == "PASS"


def test_tier1_marginal_just_outside():
    # 3.135 - epsilon is just outside the 5% band but within 10%
    verdict = evaluate_tier1({"v_mean": 3.10}, "3.3V ± 5%")
    assert verdict == "MARGINAL"


def test_tier1_fail_far_out():
    # Way outside even 2x tolerance
    verdict = evaluate_tier1({"v_mean": 2.5}, "3.3V ± 5%")
    assert verdict == "FAIL"


def test_tier1_pass_greater_than():
    verdict = evaluate_tier1({"v_mean": 3.0}, "> 2.5V")
    assert verdict == "PASS"


def test_tier1_fail_greater_than():
    verdict = evaluate_tier1({"v_mean": 1.0}, "> 2.5V")
    assert verdict == "FAIL"


def test_tier1_marginal_greater_than():
    # Just below the lower bound but within 10% of it
    verdict = evaluate_tier1({"v_mean": 2.35}, "> 2.5V")
    assert verdict == "MARGINAL"


def test_tier1_pass_less_than():
    verdict = evaluate_tier1({"v_mean": 0.1}, "< 0.5V")
    assert verdict == "PASS"


def test_tier1_fail_less_than():
    verdict = evaluate_tier1({"v_mean": 1.0}, "< 0.5V")
    assert verdict == "FAIL"


def test_tier1_pass_range():
    verdict = evaluate_tier1({"v_mean": 0.25}, "0-0.5V")
    assert verdict == "PASS"


def test_tier1_fail_range():
    verdict = evaluate_tier1({"v_mean": 1.5}, "0-0.5V")
    assert verdict == "FAIL"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd python && python -m pytest agent/tests/test_evaluator.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create evaluator.py (Tier-1 only; Tier-2 added in Task 7)**

```python
# python/agent/evaluator.py
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any


@dataclass
class _Bounds:
    lower: float | None
    upper: float | None
    tolerance: float   # half-band size for MARGINAL detection


def parse_expected_range(s: str) -> _Bounds:
    """Parse an expected_range string into numeric bounds.

    Supported patterns:
        "3.3V ± 5%"      symmetric percentage
        "3.3V ± 165mV"   symmetric millivolt
        "3.3V ± 0.165V"  symmetric volt
        "> 2.5V"         one-sided lower bound
        "< 0.5V"         one-sided upper bound
        "0-0.5V"         absolute range (hyphen or en-dash)
    """
    s = s.strip()

    m = re.match(r'([\d.]+)\s*[Vv]\s*[±]\s*([\d.]+)\s*%', s)
    if m:
        center, pct = float(m.group(1)), float(m.group(2)) / 100
        tol = center * pct
        return _Bounds(center - tol, center + tol, tol)

    m = re.match(r'([\d.]+)\s*[Vv]\s*[±]\s*([\d.]+)\s*m[Vv]', s)
    if m:
        center, tol = float(m.group(1)), float(m.group(2)) / 1000
        return _Bounds(center - tol, center + tol, tol)

    m = re.match(r'([\d.]+)\s*[Vv]\s*[±]\s*([\d.]+)\s*[Vv]', s)
    if m:
        center, tol = float(m.group(1)), float(m.group(2))
        return _Bounds(center - tol, center + tol, tol)

    m = re.match(r'>\s*([\d.]+)\s*[Vv]', s)
    if m:
        lo = float(m.group(1))
        return _Bounds(lo, None, lo * 0.1)

    m = re.match(r'<\s*([\d.]+)\s*[Vv]', s)
    if m:
        hi = float(m.group(1))
        return _Bounds(None, hi, hi * 0.1)

    m = re.match(r'([\d.]+)\s*[-\u2013]\s*([\d.]+)\s*[Vv]', s)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return _Bounds(lo, hi, (hi - lo) / 2)

    raise ValueError(f"Cannot parse expected_range: {s!r}")


def evaluate_tier1(measurements: dict[str, Any], expected_range: str) -> str:
    """Return 'PASS', 'FAIL', or 'MARGINAL' based on v_mean vs expected_range.

    PASS     — v_mean is within the specified bounds.
    FAIL     — v_mean is more than 2x the tolerance outside the bounds.
    MARGINAL — v_mean is between 1x and 2x the tolerance outside the bounds.
    """
    v_mean = float(measurements.get("v_mean", 0.0))
    b = parse_expected_range(expected_range)

    in_range = True
    if b.lower is not None and v_mean < b.lower:
        in_range = False
    if b.upper is not None and v_mean > b.upper:
        in_range = False

    if in_range:
        return "PASS"

    tol = b.tolerance
    if b.lower is not None and b.upper is not None:
        center = (b.lower + b.upper) / 2
        if abs(v_mean - center) <= 2 * tol:
            return "MARGINAL"
    elif b.lower is not None:
        if v_mean >= b.lower - tol:
            return "MARGINAL"
    elif b.upper is not None:
        if v_mean <= b.upper + tol:
            return "MARGINAL"

    return "FAIL"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd python && python -m pytest agent/tests/test_evaluator.py -v
```

Expected: all 16 PASS

- [ ] **Step 5: Commit**

```bash
git add python/agent/evaluator.py python/agent/tests/test_evaluator.py
git commit -m "feat: add tier-1 numeric evaluator with range parser"
```

---

## Task 4: Tool schemas and handlers

**Files:**
- Create: `python/agent/tools.py`
- Create: `python/agent/tests/test_tools.py`

- [ ] **Step 1: Write failing tests**

Create `python/agent/tests/test_tools.py`:

```python
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import numpy as np

from agent.models import TestSession, SessionState, HardwareContext, ProbeInstruction


def _make_hw(scope_samples=None):
    """Return a mocked HardwareContext."""
    if scope_samples is None:
        scope_samples = np.array([3.28, 3.30, 3.32, 3.31, 3.29], dtype=np.float64)

    scope = MagicMock()
    scope.configure_channel = MagicMock()
    scope.arm_trigger = MagicMock()
    scope.read_samples = MagicMock(return_value=scope_samples)

    psu = MagicMock()
    psu.set_voltage = MagicMock()
    psu.enable = MagicMock()

    ad = MagicMock()
    ad.scope = scope
    ad.psu = psu

    return HardwareContext(analog_discovery=ad)


def _make_session(probe_points=None):
    schematic = {
        "board_name": "TestBoard",
        "understanding": "A test board.",
        "probe_points": probe_points or [
            {"label": "TP1", "net": "VCC_3V3", "expected_range": "3.3V ± 5%",
             "probe_type": "power_rail", "designator": "TP1",
             "pin_name": "1", "pin_number": "1"},
        ],
    }
    return TestSession(schematic=schematic)


# --- psu_configure ---

def test_handle_psu_configure_enable():
    from agent.tools import handle_psu_configure
    hw = _make_hw()
    result = handle_psu_configure({"channel": 0, "voltage": 3.3, "enabled": True}, hw)
    assert result["status"] == "ok"
    hw.analog_discovery.psu.set_voltage.assert_called_once_with(0, 3.3)
    hw.analog_discovery.psu.enable.assert_called_once_with(0, True)


def test_handle_psu_configure_disable():
    from agent.tools import handle_psu_configure
    hw = _make_hw()
    result = handle_psu_configure({"channel": 0, "voltage": 0.0, "enabled": False}, hw)
    assert result["status"] == "ok"
    hw.analog_discovery.psu.enable.assert_called_once_with(0, False)


def test_handle_psu_configure_voltage_clamp():
    from agent.tools import handle_psu_configure
    hw = _make_hw()
    result = handle_psu_configure({"channel": 0, "voltage": 99.0, "enabled": True}, hw)
    assert "error" in result
    hw.analog_discovery.psu.set_voltage.assert_not_called()


# --- scope_capture ---

def test_handle_scope_capture_returns_stats():
    from agent.tools import handle_scope_capture
    hw = _make_hw(scope_samples=np.array([3.28, 3.30, 3.32, 3.31, 3.29]))
    result = handle_scope_capture(
        {"channel": 0, "voltage_range": 5.0, "sample_rate": 1e6, "num_samples": 5,
         "trigger_source": "none", "trigger_level": 0.0, "trigger_edge": "rising"},
        hw,
    )
    assert "v_mean" in result
    assert "v_min" in result
    assert "v_max" in result
    assert "v_pp" in result
    assert "v_rms" in result
    assert abs(result["v_mean"] - 3.3) < 0.05


def test_handle_scope_capture_calls_driver():
    from agent.tools import handle_scope_capture
    hw = _make_hw()
    handle_scope_capture({"channel": 1, "voltage_range": 10.0}, hw)
    hw.analog_discovery.scope.configure_channel.assert_called_once_with(
        channel=1, voltage_range=10.0, sample_rate=1_000_000, num_samples=1024
    )


# --- require_probe ---

def test_handle_require_probe_sets_state():
    from agent.tools import handle_require_probe
    session = _make_session()
    result = handle_require_probe(
        {
            "probe_point_label": "TP1",
            "net": "VCC_3V3",
            "location_hint": "Left of C12",
            "probe_type": "power_rail",
            "instructions": "Place CH1 on TP1.",
        },
        session,
    )
    assert session.state == SessionState.PROBE_REQUIRED
    assert session.current_probe.probe_point_label == "TP1"
    assert result["status"] == "probe_required"


# --- handle_record_result ---

@pytest.mark.asyncio
async def test_handle_record_result_pass():
    from agent.tools import handle_record_result
    session = _make_session()
    result = await handle_record_result(
        {
            "probe_point_label": "TP1",
            "verdict": "PASS",
            "reasoning": "Looks good.",
            "measurements": {"v_mean": 3.31, "v_pp": 0.04},
        },
        session,
    )
    assert result["verdict"] == "PASS"
    assert len(session.results) == 1
    assert session.results[0].verdict == "PASS"
    assert session.results[0].tier == 1


@pytest.mark.asyncio
async def test_handle_record_result_fail():
    from agent.tools import handle_record_result
    session = _make_session()
    result = await handle_record_result(
        {
            "probe_point_label": "TP1",
            "verdict": "FAIL",
            "reasoning": "Way off.",
            "measurements": {"v_mean": 1.0, "v_pp": 0.1},
        },
        session,
    )
    assert result["verdict"] == "FAIL"
    assert session.results[0].verdict == "FAIL"


@pytest.mark.asyncio
async def test_handle_record_result_marginal_escalates_to_tier2():
    from agent.tools import handle_record_result
    session = _make_session()

    with patch("agent.evaluator.evaluate_tier2", new=AsyncMock(return_value=("FAIL", "Ripple too high."))):
        result = await handle_record_result(
            {
                "probe_point_label": "TP1",
                "verdict": "MARGINAL",
                "reasoning": "Slightly off.",
                "measurements": {"v_mean": 3.10, "v_pp": 0.08},  # just outside 5% band
            },
            session,
        )
    assert result["tier"] == 2
    assert session.results[0].tier == 2


# --- dispatch_tool ---

@pytest.mark.asyncio
async def test_dispatch_unknown_tool():
    from agent.tools import dispatch_tool
    session = _make_session()
    hw = _make_hw()
    result = await dispatch_tool("nonexistent_tool", {}, session, hw)
    assert "error" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd python && python -m pytest agent/tests/test_tools.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create tools.py**

```python
# python/agent/tools.py
from __future__ import annotations

from typing import Any, TYPE_CHECKING
import numpy as np

from .config import MAX_VOLTAGE
from .models import HardwareContext, ProbeInstruction, SessionState, TestResult, TestSession

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "psu_configure",
        "description": (
            "Set PSU voltage and enable/disable output on the Analog Discovery. "
            "Channel 0 = V+ (0–5 V). Call before scope_capture if the board needs power."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "integer", "description": "0 for V+, 1 for V-"},
                "voltage": {"type": "number", "description": "Target voltage in volts"},
                "enabled": {"type": "boolean", "description": "Enable or disable the rail"},
            },
            "required": ["channel", "voltage", "enabled"],
        },
    },
    {
        "name": "scope_capture",
        "description": (
            "Configure and capture a waveform on one oscilloscope channel. "
            "Returns v_min, v_max, v_mean, v_pp, v_rms. "
            "Always call require_probe first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "integer", "description": "0 or 1"},
                "voltage_range": {
                    "type": "number",
                    "description": "Full-scale input range V p-p. One of: 0.5, 1, 2, 5, 10, 25, 50",
                },
                "sample_rate": {"type": "number", "description": "Sample rate Hz", "default": 1000000},
                "num_samples": {"type": "integer", "description": "Samples to acquire", "default": 1024},
                "trigger_source": {"type": "string", "description": "'none'|'ch0'|'ch1'|'ext'", "default": "none"},
                "trigger_level": {"type": "number", "description": "Trigger threshold volts", "default": 0.0},
                "trigger_edge": {"type": "string", "description": "'rising'|'falling'", "default": "rising"},
            },
            "required": ["channel", "voltage_range"],
        },
    },
    {
        "name": "require_probe",
        "description": (
            "Pause the test session and instruct the user to place the oscilloscope probe. "
            "The agent loop blocks until the user calls /resume."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "probe_point_label": {"type": "string"},
                "net": {"type": "string"},
                "location_hint": {"type": "string", "description": "e.g. 'C12 drain, left pad'"},
                "probe_type": {"type": "string", "description": "'physical_tp'|'power_rail'|'signal'"},
                "instructions": {"type": "string", "description": "Full instruction sentence for the user"},
            },
            "required": ["probe_point_label", "net", "location_hint", "probe_type", "instructions"],
        },
    },
    {
        "name": "record_result",
        "description": (
            "Record a verdict for the current probe point after evaluating scope measurements. "
            "Include the measurements dict from the most recent scope_capture."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "probe_point_label": {"type": "string"},
                "verdict": {"type": "string", "enum": ["PASS", "FAIL", "MARGINAL"]},
                "reasoning": {"type": "string"},
                "measurements": {"type": "object", "description": "Stats dict from scope_capture"},
            },
            "required": ["probe_point_label", "verdict", "reasoning", "measurements"],
        },
    },
]


def _compute_stats(samples: np.ndarray) -> dict[str, float]:
    return {
        "v_min":  float(samples.min()),
        "v_max":  float(samples.max()),
        "v_mean": float(samples.mean()),
        "v_pp":   float(samples.max() - samples.min()),
        "v_rms":  float(np.sqrt(np.mean(samples ** 2))),
    }


def handle_psu_configure(inputs: dict[str, Any], hw: HardwareContext) -> dict:
    channel = int(inputs["channel"])
    voltage = float(inputs["voltage"])
    enabled = bool(inputs["enabled"])

    if voltage > MAX_VOLTAGE:
        return {"error": f"Voltage {voltage} V exceeds safety limit {MAX_VOLTAGE} V"}

    if enabled:
        hw.analog_discovery.psu.set_voltage(channel, voltage)
    hw.analog_discovery.psu.enable(channel, enabled)
    return {"status": "ok", "channel": channel, "voltage": voltage, "enabled": enabled}


def handle_scope_capture(inputs: dict[str, Any], hw: HardwareContext) -> dict:
    channel      = int(inputs["channel"])
    voltage_range = float(inputs["voltage_range"])
    sample_rate  = float(inputs.get("sample_rate", 1_000_000))
    num_samples  = int(inputs.get("num_samples", 1024))
    trigger_source = str(inputs.get("trigger_source", "none"))
    trigger_level  = float(inputs.get("trigger_level", 0.0))
    trigger_edge   = str(inputs.get("trigger_edge", "rising"))

    scope = hw.analog_discovery.scope
    scope.configure_channel(
        channel=channel,
        voltage_range=voltage_range,
        sample_rate=sample_rate,
        num_samples=num_samples,
    )
    scope.arm_trigger(source=trigger_source, level=trigger_level, edge=trigger_edge)
    samples = scope.read_samples(channel)
    stats = _compute_stats(samples)
    stats["sample_rate"] = sample_rate
    stats["num_samples"] = num_samples
    return stats


def handle_require_probe(inputs: dict[str, Any], session: TestSession) -> dict:
    session.current_probe = ProbeInstruction(
        probe_point_label=inputs["probe_point_label"],
        net=inputs["net"],
        location_hint=inputs["location_hint"],
        probe_type=inputs["probe_type"],
        instructions=inputs["instructions"],
    )
    session.state = SessionState.PROBE_REQUIRED
    return {"status": "probe_required", "waiting_for_user": True}


async def handle_record_result(inputs: dict[str, Any], session: TestSession) -> dict:
    from .evaluator import evaluate_tier1, evaluate_tier2

    probe_points = session.schematic.get("probe_points", [])
    pp = next((p for p in probe_points if p["label"] == inputs["probe_point_label"]), {})
    expected_range = pp.get("expected_range", "unknown")
    net = pp.get("net", "")

    measurements = inputs["measurements"]
    tier = 1
    reasoning = inputs["reasoning"]

    if expected_range != "unknown":
        try:
            tier1_verdict = evaluate_tier1(measurements, expected_range)
        except ValueError:
            tier1_verdict = inputs["verdict"]
    else:
        tier1_verdict = inputs["verdict"]

    verdict = tier1_verdict

    if tier1_verdict == "MARGINAL":
        try:
            board_understanding = session.schematic.get("understanding", "")
            verdict, reasoning = await evaluate_tier2(
                measurements, expected_range, pp, board_understanding
            )
            tier = 2
        except Exception:
            verdict = "MARGINAL"

    session.results.append(TestResult(
        probe_point_label=inputs["probe_point_label"],
        net=net,
        verdict=verdict,
        expected_range=expected_range,
        measurements=measurements,
        reasoning=reasoning,
        tier=tier,
    ))
    return {"status": "recorded", "verdict": verdict, "tier": tier}


async def dispatch_tool(
    name: str,
    inputs: dict[str, Any],
    session: TestSession,
    hw: HardwareContext,
) -> dict:
    if name == "psu_configure":
        return handle_psu_configure(inputs, hw)
    if name == "scope_capture":
        session.state = SessionState.CAPTURING
        return handle_scope_capture(inputs, hw)
    if name == "require_probe":
        return handle_require_probe(inputs, session)
    if name == "record_result":
        session.state = SessionState.EVALUATING
        return await handle_record_result(inputs, session)
    return {"error": f"Unknown tool: {name}"}
```

- [ ] **Step 4: Install pytest-asyncio (needed for async tests)**

```bash
cd python && pip install pytest-asyncio
```

Add `pytest-asyncio>=0.23` to `python/requirements.txt`.

- [ ] **Step 5: Add pytest-asyncio config**

Create `python/pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd python && python -m pytest agent/tests/test_tools.py -v
```

Expected: all 11 PASS

- [ ] **Step 7: Commit**

```bash
git add python/agent/tools.py python/agent/tests/test_tools.py python/requirements.txt python/pytest.ini
git commit -m "feat: add agent tool schemas and handlers"
```

---

## Task 5: Agent runner (async Claude loop)

**Files:**
- Create: `python/agent/runner.py`
- Create: `python/agent/tests/test_runner.py`

- [ ] **Step 1: Write failing tests**

Create `python/agent/tests/test_runner.py`:

```python
import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from agent.models import TestSession, SessionState, HardwareContext


def _make_session():
    return TestSession(schematic={
        "board_name": "TestBoard",
        "understanding": "A test board.",
        "probe_points": [
            {"label": "TP1", "net": "VCC_3V3", "expected_range": "3.3V ± 5%",
             "probe_type": "power_rail", "designator": "TP1", "pin_name": "1", "pin_number": "1"},
        ],
    })


def _make_hw():
    ad = MagicMock()
    ad.scope = MagicMock()
    ad.psu = MagicMock()
    return HardwareContext(analog_discovery=ad)


def _make_end_turn_response():
    """Anthropic response that ends the loop immediately."""
    block = MagicMock()
    block.type = "text"
    block.text = "All done."
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


def _make_tool_use_response(tool_name: str, tool_input: dict, tool_id: str = "tu_001"):
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = tool_name
    block.input = tool_input
    resp = MagicMock()
    resp.stop_reason = "tool_use"
    resp.content = [block]
    return resp


@pytest.mark.asyncio
async def test_run_session_end_turn_immediately():
    from agent.runner import run_session

    session = _make_session()
    hw = _make_hw()

    with patch("agent.runner.anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.return_value = _make_end_turn_response()

        await run_session(session, hw)

    assert session.state == SessionState.COMPLETE


@pytest.mark.asyncio
async def test_run_session_records_result_via_tool():
    from agent.runner import run_session

    session = _make_session()
    hw = _make_hw()

    record_call = _make_tool_use_response("record_result", {
        "probe_point_label": "TP1",
        "verdict": "PASS",
        "reasoning": "OK",
        "measurements": {"v_mean": 3.31, "v_pp": 0.04},
    }, tool_id="tu_001")
    end_call = _make_end_turn_response()

    responses = [record_call, end_call]

    with patch("agent.runner.anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = responses

        await run_session(session, hw)

    assert session.state == SessionState.COMPLETE
    assert len(session.results) == 1
    assert session.results[0].verdict == "PASS"


@pytest.mark.asyncio
async def test_run_session_exceeds_max_rounds():
    from agent.runner import run_session
    from agent import config as cfg

    session = _make_session()
    hw = _make_hw()

    psu_call = _make_tool_use_response("psu_configure", {
        "channel": 0, "voltage": 3.3, "enabled": True,
    })

    with patch("agent.runner.anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.return_value = psu_call

        original = cfg.AGENT_MAX_TOOL_ROUNDS
        cfg.AGENT_MAX_TOOL_ROUNDS = 3
        try:
            await run_session(session, hw)
        finally:
            cfg.AGENT_MAX_TOOL_ROUNDS = original

    assert session.state == SessionState.FAILED
    assert "exceeded" in (session.error or "")


@pytest.mark.asyncio
async def test_run_session_probe_pause_resume():
    from agent.runner import run_session

    session = _make_session()
    hw = _make_hw()

    probe_call = _make_tool_use_response("require_probe", {
        "probe_point_label": "TP1",
        "net": "VCC_3V3",
        "location_hint": "Left of C12",
        "probe_type": "power_rail",
        "instructions": "Place CH1 on TP1.",
    }, tool_id="tu_probe")
    end_call = _make_end_turn_response()

    with patch("agent.runner.anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = [probe_call, end_call]

        task = asyncio.create_task(run_session(session, hw))
        await asyncio.sleep(0.05)  # let agent reach probe_required

        assert session.state == SessionState.PROBE_REQUIRED
        session._resume_event.set()  # simulate plugin calling /resume

        await task

    assert session.state == SessionState.COMPLETE


@pytest.mark.asyncio
async def test_run_session_hardware_error_fails_session():
    from agent.runner import run_session

    session = _make_session()
    hw = _make_hw()

    with patch("agent.runner.anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = RuntimeError("API timeout")

        await run_session(session, hw)

    assert session.state == SessionState.FAILED
    assert "API timeout" in (session.error or "")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd python && python -m pytest agent/tests/test_runner.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create runner.py**

```python
# python/agent/runner.py
from __future__ import annotations

import asyncio
import json
from typing import Any

import anthropic

from .config import AGENT_MODEL, AGENT_MAX_TOKENS, AGENT_MAX_TOOL_ROUNDS
from .models import HardwareContext, SessionState, TestSession
from .tools import TOOL_SCHEMAS, dispatch_tool


def build_system_prompt(schematic: dict[str, Any]) -> str:
    probe_points = schematic.get("probe_points", [])
    board_name = schematic.get("board_name", "Unknown Board")
    understanding = schematic.get("understanding", "")
    return (
        f"You are a hardware test agent validating the PCB: {board_name}\n\n"
        f"## Board Understanding\n{understanding}\n\n"
        f"## Probe Points ({len(probe_points)} total)\n"
        f"{json.dumps(probe_points, indent=2)}\n\n"
        "## Rules\n"
        "1. Always call `require_probe` before `scope_capture` — the user must physically place the probe.\n"
        "2. Always call `record_result` after evaluating each probe point.\n"
        "3. Test power rails first (probe_type == 'power_rail'), then signal nets.\n"
        "4. Use `psu_configure` to power the board before testing if not already powered.\n"
        "5. Do not invent probe points not listed above.\n"
        "6. If v_mean is near zero after enabling PSU, the board may be disconnected — record FAIL.\n"
        "7. Work through all probe points until every one has a recorded result."
    )


async def run_session(session: TestSession, hw: HardwareContext) -> None:
    """Drive the Claude tool-use loop for one test session."""
    client = anthropic.Anthropic()
    system = build_system_prompt(session.schematic)
    messages: list[dict] = [
        {"role": "user", "content": "Please begin testing the board. Work through all probe points."}
    ]

    try:
        for _ in range(AGENT_MAX_TOOL_ROUNDS):
            if session.state in (SessionState.COMPLETE, SessionState.FAILED):
                break

            response = client.messages.create(
                model=AGENT_MODEL,
                max_tokens=AGENT_MAX_TOKENS,
                system=system,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                session.state = SessionState.COMPLETE
                break

            if response.stop_reason != "tool_use":
                session.state = SessionState.FAILED
                session.error = f"Unexpected stop_reason: {response.stop_reason}"
                break

            tool_results: list[dict] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                result = await dispatch_tool(block.name, block.input, session, hw)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })

                if session.state == SessionState.PROBE_REQUIRED:
                    await session._resume_event.wait()
                    session._resume_event.clear()
                    session.state = SessionState.CAPTURING

            messages.append({"role": "user", "content": tool_results})

        else:
            session.state = SessionState.FAILED
            session.error = f"Agent exceeded {AGENT_MAX_TOOL_ROUNDS} tool rounds"

    except Exception as exc:
        session.state = SessionState.FAILED
        session.error = str(exc)
        raise
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd python && python -m pytest agent/tests/test_runner.py -v
```

Expected: all 5 PASS

- [ ] **Step 5: Commit**

```bash
git add python/agent/runner.py python/agent/tests/test_runner.py
git commit -m "feat: add async Claude tool-use runner"
```

---

## Task 6: FastAPI server routes

**Files:**
- Create: `python/agent/server.py`
- Create: `python/agent/tests/test_server.py`

- [ ] **Step 1: Write failing tests**

Create `python/agent/tests/test_server.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd python && python -m pytest agent/tests/test_server.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create server.py**

```python
# python/agent/server.py
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .models import HardwareContext, SessionState, TestSession
from .runner import run_session
from .config import SCOPE_BITSTREAM, SCOPE_URL

router = APIRouter(prefix="/agent")

_sessions: dict[str, TestSession] = {}


class StartSessionRequest(BaseModel):
    schematic: dict[str, Any]
    config: dict[str, Any] = {}


@router.post("/sessions")
async def start_session(req: StartSessionRequest):
    from drivers.analog_discovery.driver import AnalogDiscovery

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
            "probe_point_label": p.probe_point_label,
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
    return {"status": "resuming"}


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
    return s
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd python && python -m pytest agent/tests/test_server.py -v
```

Expected: all 9 PASS

- [ ] **Step 5: Commit**

```bash
git add python/agent/server.py python/agent/tests/test_server.py
git commit -m "feat: add agent FastAPI router with session CRUD"
```

---

## Task 7: Tier-2 Claude evaluator

**Files:**
- Modify: `python/agent/evaluator.py` — add `evaluate_tier2`
- Modify: `python/agent/tests/test_evaluator.py` — add Tier-2 tests

- [ ] **Step 1: Write failing tests**

Append to `python/agent/tests/test_evaluator.py`:

```python
# --- evaluate_tier2 ---

@pytest.mark.asyncio
async def test_evaluate_tier2_returns_pass():
    from agent.evaluator import evaluate_tier2
    with patch("agent.evaluator.anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        resp = MagicMock()
        resp.content = [MagicMock(text='{"verdict": "PASS", "reasoning": "Acceptable ripple."}')]
        mock_client.messages.create.return_value = resp

        verdict, reasoning = await evaluate_tier2(
            measurements={"v_mean": 3.20, "v_pp": 0.12},
            expected_range="3.3V ± 5%",
            probe_point={"label": "TP1", "net": "VCC_3V3"},
            board_understanding="LDO rail for MCU.",
        )
    assert verdict == "PASS"
    assert "ripple" in reasoning.lower()


@pytest.mark.asyncio
async def test_evaluate_tier2_returns_fail():
    from agent.evaluator import evaluate_tier2
    with patch("agent.evaluator.anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        resp = MagicMock()
        resp.content = [MagicMock(text='{"verdict": "FAIL", "reasoning": "Voltage sag indicates overload."}')]
        mock_client.messages.create.return_value = resp

        verdict, reasoning = await evaluate_tier2(
            measurements={"v_mean": 3.10, "v_pp": 0.30},
            expected_range="3.3V ± 5%",
            probe_point={"label": "TP1", "net": "VCC_3V3"},
            board_understanding="LDO rail for MCU.",
        )
    assert verdict == "FAIL"
```

Add imports at the top of `test_evaluator.py` (after existing imports):

```python
from unittest.mock import patch, MagicMock
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd python && python -m pytest agent/tests/test_evaluator.py::test_evaluate_tier2_returns_pass -v
```

Expected: `ImportError` (function not defined yet)

- [ ] **Step 3: Add evaluate_tier2 to evaluator.py**

Append to `python/agent/evaluator.py`:

```python
import json
from typing import Any
import anthropic


async def evaluate_tier2(
    measurements: dict[str, Any],
    expected_range: str,
    probe_point: dict[str, Any],
    board_understanding: str,
) -> tuple[str, str]:
    """Call Claude to interpret a marginal measurement.

    Returns (verdict, reasoning) where verdict is 'PASS' or 'FAIL'.
    Only called when tier-1 returns MARGINAL.
    """
    from .config import AGENT_MODEL

    client = anthropic.Anthropic()
    prompt = (
        "You are evaluating a hardware measurement. Respond with ONLY valid JSON, no markdown.\n\n"
        f"Probe point: {json.dumps(probe_point)}\n"
        f"Expected range: {expected_range}\n"
        f"Measurements: {json.dumps(measurements)}\n"
        f"Board context: {board_understanding}\n\n"
        'Respond with exactly: {"verdict": "PASS" or "FAIL", "reasoning": "one sentence"}'
    )
    response = client.messages.create(
        model=AGENT_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    result = json.loads(response.content[0].text)
    return result["verdict"], result["reasoning"]
```

Also add these imports near the top of `evaluator.py` (after the existing imports):

```python
import json
from typing import Any
import anthropic
```

- [ ] **Step 4: Run all evaluator tests to verify they pass**

```bash
cd python && python -m pytest agent/tests/test_evaluator.py -v
```

Expected: all 18 PASS

- [ ] **Step 5: Commit**

```bash
git add python/agent/evaluator.py python/agent/tests/test_evaluator.py
git commit -m "feat: add tier-2 Claude evaluator for marginal measurements"
```

---

## Task 8: Mount on existing API and full test run

**Files:**
- Modify: `python/api/server.py` — include agent router

- [ ] **Step 1: Read current server.py top-of-file to know insertion point**

Open `python/api/server.py` and locate the `app = FastAPI(...)` line (currently line 37).

- [ ] **Step 2: Add agent router import and mount**

In `python/api/server.py`, after the `app = FastAPI(...)` line add:

```python
from agent.server import router as _agent_router
app.include_router(_agent_router)
```

- [ ] **Step 3: Run full test suite**

```bash
cd python && python -m pytest agent/tests/ -v
```

Expected: all tests PASS (minimum 43 across 5 test files)

- [ ] **Step 4: Verify server starts without error**

```bash
cd python && python -m api.main &
sleep 2
curl -s http://localhost:8000/docs | grep -q "agent" && echo "agent routes visible" || echo "MISSING"
kill %1
```

Expected: `agent routes visible`

- [ ] **Step 5: Commit**

```bash
git add python/api/server.py
git commit -m "feat: mount agent router on main FastAPI server"
```

---

## Verification

**Run all agent tests:**
```bash
cd python && python -m pytest agent/tests/ -v --tb=short
```
Expected: 43+ tests PASS, 0 FAIL

**Confirm REST surface is live (server must be running):**
```bash
# Start server
cd python && python -m api.main &
sleep 2

# Start a session (no real hardware — will fail at connect, which is expected without AD plugged in)
curl -s -X POST http://localhost:8000/agent/sessions \
  -H "Content-Type: application/json" \
  -d '{"schematic": {"board_name": "Test", "probe_points": []}}' | python3 -m json.tool

kill %1
```

**With real hardware connected:**
1. Set `SCOPE_BITSTREAM` to your `.bit` file path
2. Set `AGENT_MODEL` and `ANTHROPIC_API_KEY` env vars
3. POST a real `SchematicSummary` (serialized to JSON) to `/agent/sessions`
4. Poll `GET /agent/sessions/{id}` until `probe_required`
5. Place probe per instructions, then POST to `/agent/sessions/{id}/resume`
6. Repeat until `complete`
7. GET `/agent/sessions/{id}/report` for full results
