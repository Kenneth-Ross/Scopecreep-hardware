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
    PLAN_READY = "plan_ready"           # plan published, awaiting user approval
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
    # Proposed test plan produced by publish_test_plan, awaiting user review.
    proposed_plan: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        # Not a dataclass field — excluded from __repr__, asdict(), and equality
        # checks. Each instance gets its own independent Event, never shared.
        self._resume_event: asyncio.Event = asyncio.Event()
