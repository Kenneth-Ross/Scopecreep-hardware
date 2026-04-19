# python/planner/models.py
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, ConfigDict


class PsuSetting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: Literal["main"] = "main"
    voltage: float = Field(ge=0.0)
    current_limit: float = Field(gt=0.0)
    rail_name: str


class ProbeStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    net: str
    location_hint: str
    probe_type: Literal["physical_tp", "component_pin", "net_trace"]
    scope_channel: Literal["CH1", "CH2"]
    user_instructions: str


class ExpectedRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["pct", "abs_mv", "gt", "lt", "range"]
    nominal: float | None = None
    tolerance: float | None = None
    lower: float | None = None
    upper: float | None = None


class TestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    rationale: str
    psu: PsuSetting
    probe: ProbeStep
    expected: ExpectedRange
    measurement: Literal["v_mean", "v_pp", "v_rms"]


class TestPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    board_name: str
    summary: str
    test_cases: list[TestCase]


def expected_range_to_legacy(e: ExpectedRange) -> str:
    """Format an ExpectedRange as the legacy string the tier-1 evaluator parses."""
    if e.kind == "pct":
        return f"{e.nominal}V ± {e.tolerance}%"
    if e.kind == "abs_mv":
        return f"{e.nominal}V ± {e.tolerance}mV"
    if e.kind == "gt":
        return f"> {e.lower}V"
    if e.kind == "lt":
        return f"< {e.upper}V"
    if e.kind == "range":
        return f"{e.lower}-{e.upper}V"
    raise ValueError(f"unknown kind: {e.kind}")
