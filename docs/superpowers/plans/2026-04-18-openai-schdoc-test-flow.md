# OpenAI Schdoc Test-Flow CLI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Python CLI that parses a `.SchDoc`, asks OpenAI (structured output) for a TestPlan, prints it for user approval, then steps the user through each test case — driving the DPS-150 PSU and Analog Discovery scope automatically and prompting the user per probe placement. Migrate all remaining Anthropic calls in the repo to OpenAI in the same PR.

**Architecture:** Two separable Python packages — `planner/` (OpenAI, no hardware) and `executor/` (hardware, no OpenAI) — joined by a `TestPlan` Pydantic model. A `cli/run_test.py` wires them together and handles the plan-then-approve gate. Existing `agent/`, `schdoc/llm.py`, and bundled `Scopecreep/` copies are migrated off Anthropic.

**Tech Stack:** Python 3.11, `openai>=1.40`, `pydantic>=2.6`, `python-dotenv>=1.0`, `pyserial` (DPS-150), `pydwf`/`pyftdi` (Analog Discovery), `pytest`.

**Spec:** `docs/superpowers/specs/2026-04-18-openai-schdoc-test-flow-design.md`

---

## File map

**Create**
- `/home/alex/jbhack/.env.example`
- `/home/alex/jbhack/.gitignore` (add `.env`, `reports/` if missing)
- `/home/alex/jbhack/python/evaluator/__init__.py`
- `/home/alex/jbhack/python/evaluator/tier1.py`
- `/home/alex/jbhack/python/evaluator/tier2.py`
- `/home/alex/jbhack/python/evaluator/tests/__init__.py`
- `/home/alex/jbhack/python/evaluator/tests/test_tier1.py`
- `/home/alex/jbhack/python/planner/__init__.py`
- `/home/alex/jbhack/python/planner/models.py`
- `/home/alex/jbhack/python/planner/openai_planner.py`
- `/home/alex/jbhack/python/planner/tests/__init__.py`
- `/home/alex/jbhack/python/planner/tests/test_models.py`
- `/home/alex/jbhack/python/planner/tests/test_openai_planner.py`
- `/home/alex/jbhack/python/executor/__init__.py`
- `/home/alex/jbhack/python/executor/drivers.py` — thin protocol + adapters for DPS-150 and AD scope
- `/home/alex/jbhack/python/executor/runner.py`
- `/home/alex/jbhack/python/executor/prompts.py`
- `/home/alex/jbhack/python/executor/report.py`
- `/home/alex/jbhack/python/executor/tests/__init__.py`
- `/home/alex/jbhack/python/executor/tests/test_runner.py`
- `/home/alex/jbhack/python/cli/__init__.py`
- `/home/alex/jbhack/python/cli/run_test.py`
- `/home/alex/jbhack/python/cli/tests/__init__.py`
- `/home/alex/jbhack/python/cli/tests/test_run_test.py`

**Modify**
- `/home/alex/jbhack/python/requirements.txt` — drop `anthropic`, add `openai`, `python-dotenv`, `pyserial`
- `/home/alex/jbhack/python/schdoc/llm.py` — Anthropic → OpenAI
- `/home/alex/jbhack/python/schdoc/tests/test_llm.py` — retarget to OpenAI mocks
- `/home/alex/jbhack/python/agent/config.py` — `AGENT_MODEL` default → `gpt-5-mini`
- `/home/alex/jbhack/python/agent/runner.py` — Claude tool-use → OpenAI function calling
- `/home/alex/jbhack/python/agent/evaluator.py` — delete tier-2 body; re-export from new `python/evaluator/`
- `/home/alex/jbhack/python/agent/tools.py` — update imports
- `/home/alex/jbhack/python/agent/tests/test_runner.py` — OpenAI mocks
- `/home/alex/jbhack/python/agent/README.md` — env vars + provider
- `/home/alex/jbhack/Scopecreep/src/main/resources/schdoc/llm.py` — mirror of `python/schdoc/llm.py`
- `/home/alex/jbhack/Scopecreep/src/main/resources/schdoc/requirements.txt` — mirror deps

**Delete**
- None. (Old Anthropic code is replaced in place.)

---

## Dependency / task order

1. Scaffolding & deps (Task 1)
2. Shared evaluator extraction (Task 2) — unblocks everything that imports `evaluate_tier1`
3. Planner models (Task 3) → Planner OpenAI call (Task 4)
4. Executor driver protocol + fakes (Task 5) → Executor runner (Task 6)
5. CLI wiring (Task 7)
6. Anthropic → OpenAI migration in `schdoc/llm.py` (Task 8), `agent/evaluator.py` tier-2 (Task 9), `agent/runner.py` (Task 10)
7. `Scopecreep/` mirror sync (Task 11)
8. Docs + acceptance (Task 12)

Tasks 3+4, 5+6, and 8–10 can be parallelized across agents after Task 2.

---

## Task 1: Scaffolding, `.env`, dependencies

**Files:**
- Create: `.env.example`
- Modify: `.gitignore`, `python/requirements.txt`

- [ ] **Step 1: Create `.env.example`**

Create `/home/alex/jbhack/.env.example`:

```
# OpenAI
OPENAI_API_KEY=sk-REPLACE_ME
OPENAI_MODEL=gpt-5-mini

# Hardware safety limits — executor refuses to drive past these
MAX_VOLTAGE=5.0
MAX_CURRENT=1.0

# DPS-150 serial port; leave blank to auto-detect
PSU_PORT=
```

- [ ] **Step 2: Update `.gitignore`**

Append to `/home/alex/jbhack/.gitignore` (create if missing):

```
.env
reports/
```

If the file already contains one of those lines, leave it alone.

- [ ] **Step 3: Update `python/requirements.txt`**

Replace contents of `/home/alex/jbhack/python/requirements.txt` with:

```
pyftdi>=0.55.0
pydwf>=1.1
numpy>=1.26
fastapi>=0.110
uvicorn>=0.29
olefile>=0.47
openai>=1.40
python-dotenv>=1.0
pyserial>=3.5
pytest-asyncio>=0.23
```

- [ ] **Step 4: Install**

Run: `pip install -r python/requirements.txt`
Expected: installs cleanly; `anthropic` is no longer required but may remain cached.

- [ ] **Step 5: Commit**

```bash
git add .env.example .gitignore python/requirements.txt
git commit -m "chore: bootstrap .env + swap anthropic->openai in requirements"
```

---

## Task 2: Extract tier-1 evaluator to shared module

Pull the verdict logic out of `python/agent/evaluator.py` so both `executor/` and `agent/` can import it without a cross-package dependency.

**Files:**
- Create: `python/evaluator/__init__.py`, `python/evaluator/tier1.py`, `python/evaluator/tests/test_tier1.py`
- Modify: `python/agent/evaluator.py`, `python/agent/tools.py`, `python/agent/tests/test_runner.py`

- [ ] **Step 1: Move the failing tests first — copy existing tier-1 tests**

Find the tier-1 tests. Run: `grep -rn "evaluate_tier1\|parse_expected_range" python/agent/tests/`. Copy any test that only exercises `evaluate_tier1` or `parse_expected_range` into `/home/alex/jbhack/python/evaluator/tests/test_tier1.py` verbatim, changing the import to `from evaluator.tier1 import evaluate_tier1, parse_expected_range`.

If no such tests exist, create this minimal suite:

```python
# python/evaluator/tests/test_tier1.py
import pytest
from evaluator.tier1 import evaluate_tier1, parse_expected_range

def test_pass_within_pct():
    assert evaluate_tier1({"v_mean": 3.31}, "3.3V ± 5%") == "PASS"

def test_fail_far_outside():
    assert evaluate_tier1({"v_mean": 2.5}, "3.3V ± 5%") == "FAIL"

def test_marginal_just_outside():
    assert evaluate_tier1({"v_mean": 3.5}, "3.3V ± 5%") == "MARGINAL"

def test_gt_lower():
    assert evaluate_tier1({"v_mean": 3.0}, "> 2.5V") == "PASS"

def test_range_hyphen():
    assert evaluate_tier1({"v_mean": 0.3}, "0-0.5V") == "PASS"

def test_missing_v_mean_raises():
    with pytest.raises(ValueError):
        evaluate_tier1({}, "3.3V ± 5%")

def test_unparseable_raises():
    with pytest.raises(ValueError):
        parse_expected_range("not a range")
```

- [ ] **Step 2: Run — tests fail (module missing)**

Run: `cd python && pytest evaluator/tests/test_tier1.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evaluator'`.

- [ ] **Step 3: Create `python/evaluator/__init__.py`**

```python
# python/evaluator/__init__.py
from .tier1 import evaluate_tier1, parse_expected_range

__all__ = ["evaluate_tier1", "parse_expected_range"]
```

- [ ] **Step 4: Create `python/evaluator/tier1.py`**

Copy the `from __future__ import annotations` line, `_Bounds`, `parse_expected_range`, and `evaluate_tier1` from `python/agent/evaluator.py` (lines 1–97) verbatim into `/home/alex/jbhack/python/evaluator/tier1.py`. Do not include `evaluate_tier2` — that moves in Task 9.

- [ ] **Step 5: Run — tier-1 tests pass**

Run: `cd python && pytest evaluator/tests/test_tier1.py -v`
Expected: all pass.

- [ ] **Step 6: Rewrite `python/agent/evaluator.py` to re-export + keep tier-2 stub**

Replace contents of `/home/alex/jbhack/python/agent/evaluator.py` with:

```python
# python/agent/evaluator.py
# Tier-1 logic lives in python/evaluator/tier1.py so executor/ can share it.
# Tier-2 (LLM-judged marginal re-evaluation) is ported to OpenAI in task 9.
from __future__ import annotations

from evaluator.tier1 import evaluate_tier1, parse_expected_range
from .evaluator_tier2 import evaluate_tier2  # created in task 9; placeholder today

__all__ = ["evaluate_tier1", "parse_expected_range", "evaluate_tier2"]
```

Create a temporary placeholder so imports don't break before task 9 runs. Create `/home/alex/jbhack/python/agent/evaluator_tier2.py`:

```python
# python/agent/evaluator_tier2.py — placeholder; real impl in task 9
from __future__ import annotations
from typing import Any


async def evaluate_tier2(
    measurements: dict[str, Any],
    expected_range: str,
    probe_point: dict[str, Any],
    board_understanding: str,
) -> tuple[str, str]:
    raise NotImplementedError("tier2 OpenAI evaluator not implemented yet (task 9)")
```

- [ ] **Step 7: Run all existing agent tests — still green**

Run: `cd python && pytest agent/tests -v -k "not tier2"`
Expected: pass (tier-2 tests, if any, will be re-done in task 9).

If any test failed because it imported from `python/agent/evaluator.py` at module load and the placeholder raises, those tests are expected to fail in task 9 and should be marked `@pytest.mark.skip(reason="tier2 reworked in task 9")` now with a follow-up note — do NOT delete them.

- [ ] **Step 8: Commit**

```bash
git add python/evaluator python/agent/evaluator.py python/agent/evaluator_tier2.py
git add python/agent/tests  # if any tests were skipped
git commit -m "refactor: extract tier-1 evaluator to python/evaluator"
```

---

## Task 3: Planner Pydantic models

**Files:**
- Create: `python/planner/__init__.py`, `python/planner/models.py`, `python/planner/tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Create `/home/alex/jbhack/python/planner/tests/__init__.py` (empty).

Create `/home/alex/jbhack/python/planner/tests/test_models.py`:

```python
import pytest
from pydantic import ValidationError
from planner.models import TestPlan, TestCase, PsuSetting, ProbeStep, ExpectedRange


def _valid_plan() -> dict:
    return {
        "board_name": "Main",
        "summary": "Test board with 12V input and 3V3 LDO.",
        "test_cases": [{
            "id": "TC-01",
            "title": "3V3 rail nominal",
            "rationale": "Verify U2 LDO output is in spec.",
            "psu": {"channel": "main", "voltage": 12.0, "current_limit": 0.5, "rail_name": "12V"},
            "probe": {
                "label": "TP3", "net": "VCC_3V3",
                "location_hint": "C12 left pad",
                "probe_type": "physical_tp",
                "scope_channel": "CH1",
                "user_instructions": "Place CH1 tip on TP3, GND clip to via."
            },
            "expected": {"kind": "pct", "nominal": 3.3, "tolerance": 5.0},
            "measurement": "v_mean",
        }],
    }


def test_accepts_valid_plan():
    plan = TestPlan.model_validate(_valid_plan())
    assert plan.board_name == "Main"
    assert plan.test_cases[0].psu.voltage == 12.0


def test_rejects_missing_board_name():
    bad = _valid_plan()
    del bad["board_name"]
    with pytest.raises(ValidationError):
        TestPlan.model_validate(bad)


def test_rejects_bad_probe_type():
    bad = _valid_plan()
    bad["test_cases"][0]["probe"]["probe_type"] = "bogus"
    with pytest.raises(ValidationError):
        TestPlan.model_validate(bad)


def test_rejects_negative_voltage():
    bad = _valid_plan()
    bad["test_cases"][0]["psu"]["voltage"] = -1.0
    with pytest.raises(ValidationError):
        TestPlan.model_validate(bad)
```

- [ ] **Step 2: Run — fails (module missing)**

Run: `cd python && pytest planner/tests/test_models.py -v`
Expected: `ModuleNotFoundError: No module named 'planner'`.

- [ ] **Step 3: Create `python/planner/__init__.py`**

```python
# python/planner/__init__.py
from .models import TestPlan, TestCase, PsuSetting, ProbeStep, ExpectedRange

__all__ = ["TestPlan", "TestCase", "PsuSetting", "ProbeStep", "ExpectedRange"]
```

- [ ] **Step 4: Create `python/planner/models.py`**

```python
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
```

- [ ] **Step 5: Run — tests pass**

Run: `cd python && pytest planner/tests/test_models.py -v`
Expected: 4 passed.

- [ ] **Step 6: Expose a helper to translate `ExpectedRange` to the legacy string format**

Append to `/home/alex/jbhack/python/planner/models.py`:

```python
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
```

- [ ] **Step 7: Add legacy-format test**

Append to `/home/alex/jbhack/python/planner/tests/test_models.py`:

```python
from planner.models import expected_range_to_legacy

def test_pct_legacy():
    e = ExpectedRange(kind="pct", nominal=3.3, tolerance=5.0)
    assert expected_range_to_legacy(e) == "3.3V ± 5.0%"

def test_range_legacy():
    e = ExpectedRange(kind="range", lower=0.0, upper=0.5)
    assert expected_range_to_legacy(e) == "0.0-0.5V"
```

- [ ] **Step 8: Run + commit**

Run: `cd python && pytest planner/tests/test_models.py -v`
Expected: 6 passed.

```bash
git add python/planner
git commit -m "feat(planner): pydantic TestPlan models"
```

---

## Task 4: Planner — OpenAI structured-output call

**Files:**
- Create: `python/planner/openai_planner.py`, `python/planner/tests/test_openai_planner.py`

- [ ] **Step 1: Write a test that stubs the OpenAI client**

Create `/home/alex/jbhack/python/planner/tests/test_openai_planner.py`:

```python
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from planner.models import TestPlan
from planner.openai_planner import generate_plan, PlannerError


class _Summary:
    board_name = "Main"
    power_rails = []
    zones = []
    connectors = []
    probe_points = []


def _fake_response(plan_dict):
    msg = MagicMock()
    msg.content = json.dumps(plan_dict)
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


VALID_PLAN = {
    "board_name": "Main",
    "summary": "ok",
    "test_cases": [{
        "id": "TC-01",
        "title": "3V3 rail nominal",
        "rationale": "why",
        "psu": {"channel": "main", "voltage": 12.0, "current_limit": 0.5, "rail_name": "12V"},
        "probe": {
            "label": "TP3", "net": "VCC_3V3", "location_hint": "C12",
            "probe_type": "physical_tp", "scope_channel": "CH1",
            "user_instructions": "Place probe."
        },
        "expected": {"kind": "pct", "nominal": 3.3, "tolerance": 5.0},
        "measurement": "v_mean",
    }],
}


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(PlannerError, match="OPENAI_API_KEY"):
        generate_plan(_Summary())


def test_happy_path(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with patch("planner.openai_planner.OpenAI") as m:
        m.return_value.chat.completions.create.return_value = _fake_response(VALID_PLAN)
        plan = generate_plan(_Summary())
    assert isinstance(plan, TestPlan)
    assert plan.test_cases[0].id == "TC-01"


def test_bad_schema_raises(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    bad = {"board_name": "x"}  # missing required fields
    with patch("planner.openai_planner.OpenAI") as m:
        m.return_value.chat.completions.create.return_value = _fake_response(bad)
        with pytest.raises(PlannerError, match="schema"):
            generate_plan(_Summary())
```

- [ ] **Step 2: Run — fails (module missing)**

Run: `cd python && pytest planner/tests/test_openai_planner.py -v`
Expected: `ModuleNotFoundError: No module named 'planner.openai_planner'`.

- [ ] **Step 3: Implement the planner**

Create `/home/alex/jbhack/python/planner/openai_planner.py`:

```python
# python/planner/openai_planner.py
from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI, OpenAIError
from pydantic import ValidationError

from .models import TestPlan

SYSTEM_PROMPT = (
    "You are a hardware test engineer. Given a parsed schematic summary, "
    "produce a JSON test plan that verifies each power rail and any explicit "
    "test points on the board. For each test case: pick a PSU voltage and "
    "current limit justified by the schematic's nominal rails; never exceed "
    "{max_voltage} volts on any channel; specify a physical probe location a "
    "human can find (reference designator + pin or pad, plus a nearby "
    "landmark); give an expected range grounded in the schematic "
    "(nominal \u00b1 tolerance). Prefer probe points the parser already "
    "extracted; you MAY add rail-level tests (ripple, load-step) only if the "
    "schematic supports it. Be concrete. Do not invent components not in the "
    "summary."
)


class PlannerError(RuntimeError):
    """Fatal planner error — surfaced to the CLI with a non-zero exit."""


def _build_context(summary: Any, max_voltage: float) -> str:
    """Extract a JSON context from a SchematicSummary (duck-typed)."""
    all_components = []
    seen: set[str] = set()
    for z in getattr(summary, "zones", []):
        for c in getattr(z, "components", []):
            if c.designator not in seen:
                seen.add(c.designator)
                all_components.append(c)
    for c in getattr(summary, "connectors", []):
        if c.designator not in seen:
            seen.add(c.designator)
            all_components.append(c)

    return json.dumps({
        "max_voltage": max_voltage,
        "board_name": getattr(summary, "board_name", "Unknown"),
        "components": [
            {"designator": c.designator, "value": c.value, "description": c.description}
            for c in all_components
        ],
        "power_rails": [
            {
                "name": r.name,
                "nominal_voltage": r.nominal_voltage,
                "source": r.source_designator,
                "loads": r.loads,
            }
            for r in getattr(summary, "power_rails", [])
        ],
        "zones": [
            {"name": z.name, "components": [c.designator for c in z.components]}
            for z in getattr(summary, "zones", [])
        ],
        "probe_points": [
            {"label": p.label, "net": p.net, "type": p.probe_type, "expected": p.expected_range}
            for p in getattr(summary, "probe_points", [])
        ],
    }, indent=2)


def generate_plan(summary: Any) -> TestPlan:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise PlannerError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in."
        )

    model = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
    max_voltage = float(os.environ.get("MAX_VOLTAGE", "5.0"))
    system = SYSTEM_PROMPT.replace("{max_voltage}", str(max_voltage))
    context = _build_context(summary, max_voltage)

    client = OpenAI(api_key=api_key)

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": context},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "TestPlan",
                    "schema": TestPlan.model_json_schema(),
                    "strict": True,
                },
            },
        )
    except OpenAIError as exc:
        raise PlannerError(f"OpenAI API error: {exc}") from exc

    raw = resp.choices[0].message.content
    if not raw:
        raise PlannerError("OpenAI returned an empty message.")

    try:
        return TestPlan.model_validate_json(raw)
    except ValidationError as exc:
        raise PlannerError(f"Plan failed schema validation:\n{exc}\nRaw:\n{raw}") from exc
```

- [ ] **Step 4: Run — unit tests pass**

Run: `cd python && pytest planner/tests -v`
Expected: all tests pass.

- [ ] **Step 5: Handle OpenAI `strict: true` schema restrictions**

OpenAI's `strict: true` JSON-schema mode rejects some Pydantic-emitted schemas: it requires `additionalProperties: false` on every object, does not support `$defs` references in some SDK versions, and requires every property to be listed in `required` (nullable instead of optional).

Test empirically — run the integration test from Step 6. If OpenAI rejects the schema, adjust `TestPlan.model_json_schema()` before sending:

Add this helper to `python/planner/openai_planner.py`:

```python
def _strictify(schema: dict) -> dict:
    """Transform a Pydantic JSON schema into OpenAI strict-mode-compatible form."""
    if not isinstance(schema, dict):
        return schema
    if schema.get("type") == "object":
        schema["additionalProperties"] = False
        props = schema.get("properties", {})
        schema["required"] = list(props.keys())
        for v in props.values():
            _strictify(v)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            schema[key] = [_strictify(s) for s in schema[key]]
    if "items" in schema:
        schema["items"] = _strictify(schema["items"])
    defs = schema.get("$defs", {}) or schema.get("definitions", {})
    for v in defs.values():
        _strictify(v)
    return schema
```

Update the `response_format` call to use `_strictify(TestPlan.model_json_schema())`. All fields in `ExpectedRange` are already `Optional`, which maps to `anyOf [type, null]` — that is OpenAI-strict-compatible.

- [ ] **Step 6: Integration test (gated on real key)**

Append to `/home/alex/jbhack/python/planner/tests/test_openai_planner.py`:

```python
@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="no OPENAI_API_KEY")
def test_integration_real_openai():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from schdoc.parser import parse_schdoc

    summary = parse_schdoc(str(Path(__file__).resolve().parents[3] / "Main.SchDoc"))
    plan = generate_plan(summary)

    assert plan.board_name
    assert len(plan.test_cases) >= 1
    max_v = float(os.environ.get("MAX_VOLTAGE", "5.0"))
    for tc in plan.test_cases:
        assert 0 <= tc.psu.voltage <= max_v, (tc.id, tc.psu.voltage)
        assert tc.psu.current_limit > 0
```

(If `schdoc.parser` exposes a different entry-point name, adjust the import — run `grep -n "^def parse" python/schdoc/parser.py` to confirm.)

- [ ] **Step 7: Run integration test (if key present)**

Run: `cd python && OPENAI_API_KEY=$OPENAI_API_KEY pytest planner/tests/test_openai_planner.py::test_integration_real_openai -v`
Expected: pass. If it fails on schema strictness, iterate on `_strictify` until the API accepts it.

- [ ] **Step 8: Commit**

```bash
git add python/planner/openai_planner.py python/planner/tests/test_openai_planner.py
git commit -m "feat(planner): openai structured-output test plan generator"
```

---

## Task 5: Executor driver protocol + fakes

The executor should accept anything matching small Protocols — that's how we inject fakes in tests without `unittest.mock`.

**Files:**
- Create: `python/executor/__init__.py`, `python/executor/drivers.py`

- [ ] **Step 1: Create `python/executor/__init__.py`** (empty placeholder for now)

```python
# python/executor/__init__.py
```

- [ ] **Step 2: Define driver protocols and hardware adapters**

Create `/home/alex/jbhack/python/executor/drivers.py`:

```python
# python/executor/drivers.py
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class PsuDriver(Protocol):
    def set(self, voltage: float, current_limit: float) -> None: ...
    def output_on(self) -> None: ...
    def output_off(self) -> None: ...
    def close(self) -> None: ...


@runtime_checkable
class ScopeDriver(Protocol):
    def capture(self, channel: str) -> dict[str, float]: ...
    def close(self) -> None: ...


# ---------- DPS-150 adapter ----------

def _load_dps150_class():
    """Load backend/drivers/dps150.py without clashing with python/drivers/."""
    repo_root = Path(__file__).resolve().parents[2]
    dps_path = repo_root / "backend" / "drivers" / "dps150.py"
    base_path = repo_root / "backend" / "drivers" / "base.py"

    base_spec = importlib.util.spec_from_file_location("_bench_base", base_path)
    base_mod = importlib.util.module_from_spec(base_spec)
    base_spec.loader.exec_module(base_mod)

    import sys
    sys.modules["backend"] = type(sys)("backend")
    sys.modules["backend.drivers"] = type(sys)("backend.drivers")
    sys.modules["backend.drivers.base"] = base_mod

    dps_spec = importlib.util.spec_from_file_location("_bench_dps150", dps_path)
    dps_mod = importlib.util.module_from_spec(dps_spec)
    dps_spec.loader.exec_module(dps_mod)
    return dps_mod.DPS150


def _auto_detect_dps_port() -> str:
    import glob
    candidates = (
        glob.glob("/dev/cu.usbmodem*")
        + glob.glob("/dev/tty.usbmodem*")
        + glob.glob("/dev/ttyACM*")
    )
    if not candidates:
        raise RuntimeError("DPS-150 serial port not found. Set PSU_PORT in .env.")
    return candidates[0]


class Dps150Adapter:
    """PsuDriver implementation wrapping the FNIRSI DPS-150."""

    def __init__(self, port: str | None = None):
        DPS150 = _load_dps150_class()
        self._dev = DPS150(port or os.environ.get("PSU_PORT") or _auto_detect_dps_port())
        self._dev.connect()

    def set(self, voltage: float, current_limit: float) -> None:
        self._dev.set_current(current_limit)
        self._dev.set_voltage(voltage)

    def output_on(self) -> None:
        self._dev.enable_output(True)

    def output_off(self) -> None:
        try:
            self._dev.enable_output(False)
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._dev.disconnect()
        except Exception:
            pass


# ---------- Analog Discovery adapter ----------

class AnalogDiscoveryScopeAdapter:
    """ScopeDriver implementation wrapping the WaveForms driver."""

    def __init__(self, sample_rate: float = 1_000_000.0, num_samples: int = 1024,
                 voltage_range: float = 10.0):
        from drivers.analog_discovery.waveforms_driver import WaveFormsAnalogDiscovery
        self._hw = WaveFormsAnalogDiscovery()
        self._hw.connect()
        self._sample_rate = sample_rate
        self._num_samples = num_samples
        self._voltage_range = voltage_range

    def capture(self, channel: str) -> dict[str, float]:
        ch = 0 if channel.upper() == "CH1" else 1
        scope = self._hw.scope
        scope.configure_channel(
            channel=ch,
            voltage_range=self._voltage_range,
            sample_rate=self._sample_rate,
            num_samples=self._num_samples,
        )
        scope.arm_trigger(source="none", level=0.0, edge="rising")
        samples = np.asarray(scope.read_samples(ch))
        return {
            "v_min":  float(samples.min()),
            "v_max":  float(samples.max()),
            "v_mean": float(samples.mean()),
            "v_pp":   float(samples.max() - samples.min()),
            "v_rms":  float(np.sqrt(np.mean(samples ** 2))),
        }

    def close(self) -> None:
        try:
            self._hw.disconnect()
        except Exception:
            pass
```

- [ ] **Step 3: Smoke-import the module**

Run: `cd python && python -c "from executor.drivers import PsuDriver, ScopeDriver, Dps150Adapter, AnalogDiscoveryScopeAdapter; print('ok')"`
Expected: prints `ok`. (No hardware connects — adapters only connect in `__init__`, which we don't run.)

- [ ] **Step 4: Commit**

```bash
git add python/executor/__init__.py python/executor/drivers.py
git commit -m "feat(executor): driver protocols + DPS-150/AD adapters"
```

---

## Task 6: Executor runner — TDD with fake drivers

**Files:**
- Create: `python/executor/runner.py`, `python/executor/prompts.py`, `python/executor/report.py`, `python/executor/tests/test_runner.py`

- [ ] **Step 1: Create prompts module**

Create `/home/alex/jbhack/python/executor/prompts.py`:

```python
# python/executor/prompts.py
from __future__ import annotations

import sys
from typing import TextIO


def wait_enter(msg: str, stdin: TextIO = None) -> None:
    stdin = stdin or sys.stdin
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()
    stdin.readline()


def confirm(msg: str, stdin: TextIO = None, default_yes: bool = False) -> bool:
    stdin = stdin or sys.stdin
    suffix = " [Y/n] " if default_yes else " [y/N] "
    sys.stdout.write(msg + suffix)
    sys.stdout.flush()
    line = stdin.readline().strip().lower()
    if not line:
        return default_yes
    return line in ("y", "yes")
```

- [ ] **Step 2: Create report module**

Create `/home/alex/jbhack/python/executor/report.py`:

```python
# python/executor/report.py
from __future__ import annotations

from pydantic import BaseModel
from planner.models import TestCase, TestPlan


class TestResult(BaseModel):
    test_case_id: str
    title: str
    verdict: str             # "PASS" | "FAIL" | "MARGINAL" | "ERROR"
    measurement_key: str
    measurement_value: float | None
    measurements: dict[str, float]
    expected_range_str: str
    error: str | None = None


class Report(BaseModel):
    board_name: str
    results: list[TestResult]

    @property
    def overall(self) -> str:
        verdicts = {r.verdict for r in self.results}
        if verdicts == {"PASS"}:
            return "PASS"
        if "FAIL" in verdicts or "ERROR" in verdicts:
            return "FAIL"
        return "PARTIAL"
```

- [ ] **Step 3: Write the failing executor tests**

Create `/home/alex/jbhack/python/executor/tests/__init__.py` (empty).

Create `/home/alex/jbhack/python/executor/tests/test_runner.py`:

```python
import io
import pytest

from planner.models import (
    TestPlan, TestCase, PsuSetting, ProbeStep, ExpectedRange,
)
from executor.runner import run, SafetyError


class FakePsu:
    def __init__(self):
        self.log: list[tuple] = []
        self._output_on = False
    def set(self, v, i):
        self.log.append(("set", v, i))
    def output_on(self):
        self.log.append(("on",)); self._output_on = True
    def output_off(self):
        self.log.append(("off",)); self._output_on = False
    def close(self):
        self.log.append(("close",))


class FakeScope:
    def __init__(self, v_mean=3.31):
        self.v_mean = v_mean
        self.closed = False
    def capture(self, channel):
        return {"v_min": self.v_mean - 0.02, "v_max": self.v_mean + 0.02,
                "v_mean": self.v_mean, "v_pp": 0.04, "v_rms": self.v_mean}
    def close(self):
        self.closed = True


class FakeScopeRaises(FakeScope):
    def capture(self, channel):
        raise RuntimeError("scope broke")


def _single_case_plan(voltage=12.0, nominal=3.3, v_mean=3.31):
    tc = TestCase(
        id="TC-01", title="3V3 rail", rationale="why",
        psu=PsuSetting(voltage=voltage, current_limit=0.5, rail_name="12V"),
        probe=ProbeStep(label="TP3", net="VCC_3V3", location_hint="C12",
                        probe_type="physical_tp", scope_channel="CH1",
                        user_instructions="Place probe."),
        expected=ExpectedRange(kind="pct", nominal=nominal, tolerance=5.0),
        measurement="v_mean",
    )
    return TestPlan(board_name="Main", summary="s", test_cases=[tc])


def _stdin(lines):
    return io.StringIO("\n".join(lines) + "\n")


def test_happy_path_pass(monkeypatch):
    monkeypatch.setenv("MAX_VOLTAGE", "15.0")
    monkeypatch.setenv("MAX_CURRENT", "2.0")
    psu, scope = FakePsu(), FakeScope(v_mean=3.31)
    report = run(_single_case_plan(), psu=psu, scope=scope, stdin=_stdin(["", ""]))
    assert report.overall == "PASS"
    assert report.results[0].verdict == "PASS"
    assert ("on",) in psu.log
    assert ("off",) in psu.log
    assert scope.closed is True


def test_psu_off_after_scope_error(monkeypatch):
    monkeypatch.setenv("MAX_VOLTAGE", "15.0")
    monkeypatch.setenv("MAX_CURRENT", "2.0")
    psu, scope = FakePsu(), FakeScopeRaises()
    report = run(_single_case_plan(), psu=psu, scope=scope, stdin=_stdin(["", ""]))
    assert ("off",) in psu.log
    assert report.results[0].verdict == "ERROR"


def test_safety_violation_aborts_before_output_on(monkeypatch):
    monkeypatch.setenv("MAX_VOLTAGE", "5.0")
    monkeypatch.setenv("MAX_CURRENT", "2.0")
    psu, scope = FakePsu(), FakeScope()
    with pytest.raises(SafetyError):
        run(_single_case_plan(voltage=12.0), psu=psu, scope=scope, stdin=_stdin(["", ""]))
    assert ("on",) not in psu.log
    assert ("off",) in psu.log  # finally still runs


def test_current_limit_safety(monkeypatch):
    monkeypatch.setenv("MAX_VOLTAGE", "15.0")
    monkeypatch.setenv("MAX_CURRENT", "0.1")
    psu, scope = FakePsu(), FakeScope()
    with pytest.raises(SafetyError):
        run(_single_case_plan(), psu=psu, scope=scope, stdin=_stdin(["", ""]))


def test_marginal_verdict(monkeypatch):
    monkeypatch.setenv("MAX_VOLTAGE", "15.0")
    monkeypatch.setenv("MAX_CURRENT", "2.0")
    psu, scope = FakePsu(), FakeScope(v_mean=3.5)  # ~6% high, tol 5%
    report = run(_single_case_plan(), psu=psu, scope=scope, stdin=_stdin(["", ""]))
    assert report.results[0].verdict == "MARGINAL"
```

- [ ] **Step 4: Run — fails**

Run: `cd python && pytest executor/tests/test_runner.py -v`
Expected: `ModuleNotFoundError: No module named 'executor.runner'`.

- [ ] **Step 5: Implement the runner**

Create `/home/alex/jbhack/python/executor/runner.py`:

```python
# python/executor/runner.py
from __future__ import annotations

import os
import sys
from typing import TextIO

from planner.models import TestCase, TestPlan, expected_range_to_legacy
from evaluator.tier1 import evaluate_tier1

from .drivers import PsuDriver, ScopeDriver
from .prompts import wait_enter
from .report import Report, TestResult


class SafetyError(RuntimeError):
    """Raised when a test case violates MAX_VOLTAGE / MAX_CURRENT."""


def _safety_check(tc: TestCase) -> None:
    max_v = float(os.environ.get("MAX_VOLTAGE", "5.0"))
    max_i = float(os.environ.get("MAX_CURRENT", "1.0"))
    if tc.psu.voltage > max_v:
        raise SafetyError(
            f"{tc.id}: PSU voltage {tc.psu.voltage}V exceeds MAX_VOLTAGE {max_v}V"
        )
    if tc.psu.current_limit > max_i:
        raise SafetyError(
            f"{tc.id}: current limit {tc.psu.current_limit}A exceeds MAX_CURRENT {max_i}A"
        )


def _print_header(tc: TestCase) -> None:
    sys.stdout.write(f"\n── {tc.id}: {tc.title} ──\n{tc.rationale}\n")
    sys.stdout.flush()


def _print_result(tc: TestCase, value: float | None, verdict: str) -> None:
    if value is None:
        sys.stdout.write(f"→ (no reading)  [{verdict}]\n")
    else:
        sys.stdout.write(f"→ {value:.4f}V  [{verdict}]\n")
    sys.stdout.flush()


def run(
    plan: TestPlan,
    psu: PsuDriver,
    scope: ScopeDriver,
    stdin: TextIO | None = None,
) -> Report:
    results: list[TestResult] = []
    try:
        for tc in plan.test_cases:
            _safety_check(tc)
            _print_header(tc)

            psu.set(tc.psu.voltage, tc.psu.current_limit)
            wait_enter(
                f"PSU set to {tc.psu.voltage}V @ {tc.psu.current_limit}A on "
                f"{tc.psu.rail_name}. Press Enter to energize.",
                stdin=stdin,
            )
            psu.output_on()

            wait_enter(tc.probe.user_instructions + "  Press Enter to capture.", stdin=stdin)

            expected_str = expected_range_to_legacy(tc.expected)
            try:
                meas = scope.capture(channel=tc.probe.scope_channel)
                value = float(meas[tc.measurement])
                verdict = evaluate_tier1({"v_mean": value}, expected_str)
                err = None
            except Exception as exc:
                meas = {}
                value = None
                verdict = "ERROR"
                err = str(exc)

            _print_result(tc, value, verdict)
            results.append(TestResult(
                test_case_id=tc.id,
                title=tc.title,
                verdict=verdict,
                measurement_key=tc.measurement,
                measurement_value=value,
                measurements=meas,
                expected_range_str=expected_str,
                error=err,
            ))
    finally:
        try:
            psu.output_off()
        finally:
            try:
                psu.close()
            finally:
                scope.close()

    return Report(board_name=plan.board_name, results=results)
```

- [ ] **Step 6: Run — tests pass**

Run: `cd python && pytest executor/tests/test_runner.py -v`
Expected: 5 tests pass.

Note: `evaluate_tier1` currently only checks `v_mean` against the legacy string. For `measurement != "v_mean"` (e.g. `v_pp`), the executor sends `{"v_mean": value}` where `value` is the measurement of interest — this keeps the evaluator reusable. Document this decision in a short comment above the `evaluate_tier1` call in runner.py if it isn't already obvious.

- [ ] **Step 7: Commit**

```bash
git add python/executor
git commit -m "feat(executor): deterministic runner with safety checks"
```

---

## Task 7: CLI entry point

**Files:**
- Create: `python/cli/__init__.py`, `python/cli/run_test.py`, `python/cli/tests/test_run_test.py`

- [ ] **Step 1: Empty `__init__.py`s**

Create `/home/alex/jbhack/python/cli/__init__.py` and `/home/alex/jbhack/python/cli/tests/__init__.py` (both empty).

- [ ] **Step 2: Write a CLI happy-path test**

Create `/home/alex/jbhack/python/cli/tests/test_run_test.py`:

```python
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from cli.run_test import main
from planner.models import TestPlan, TestCase, PsuSetting, ProbeStep, ExpectedRange


def _plan():
    return TestPlan(
        board_name="Main", summary="s",
        test_cases=[TestCase(
            id="TC-01", title="t", rationale="r",
            psu=PsuSetting(voltage=3.3, current_limit=0.2, rail_name="3V3"),
            probe=ProbeStep(label="TP3", net="VCC_3V3", location_hint="C12",
                            probe_type="physical_tp", scope_channel="CH1",
                            user_instructions="Probe."),
            expected=ExpectedRange(kind="pct", nominal=3.3, tolerance=5.0),
            measurement="v_mean",
        )],
    )


class FakePsu:
    def set(self, v, i): pass
    def output_on(self): pass
    def output_off(self): pass
    def close(self): pass


class FakeScope:
    def capture(self, channel): return {"v_mean": 3.31, "v_pp": 0.04, "v_rms": 3.31, "v_min": 3.29, "v_max": 3.33}
    def close(self): pass


def test_run_test_writes_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MAX_VOLTAGE", "15.0")
    monkeypatch.setenv("MAX_CURRENT", "2.0")
    schdoc = Path(__file__).resolve().parents[3] / "Main.SchDoc"
    report_dir = tmp_path / "reports"

    stdin_data = "y\n\n\n"   # approve, PSU enter, probe enter

    with patch("cli.run_test.generate_plan", return_value=_plan()), \
         patch("cli.run_test.Dps150Adapter", return_value=FakePsu()), \
         patch("cli.run_test.AnalogDiscoveryScopeAdapter", return_value=FakeScope()), \
         patch("sys.stdin", io.StringIO(stdin_data)):
        rc = main([str(schdoc), "--report-dir", str(report_dir)])

    assert rc == 0
    produced = list(report_dir.glob("*.json"))
    assert produced
    data = json.loads(produced[0].read_text())
    assert data["board_name"] == "Main"
    assert data["results"][0]["verdict"] == "PASS"


def test_run_test_aborts_when_not_approved(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_VOLTAGE", "15.0")
    monkeypatch.setenv("MAX_CURRENT", "2.0")
    schdoc = Path(__file__).resolve().parents[3] / "Main.SchDoc"
    report_dir = tmp_path / "reports"

    with patch("cli.run_test.generate_plan", return_value=_plan()), \
         patch("cli.run_test.Dps150Adapter") as psu_ctor, \
         patch("cli.run_test.AnalogDiscoveryScopeAdapter") as scope_ctor, \
         patch("sys.stdin", io.StringIO("n\n")):
        rc = main([str(schdoc), "--report-dir", str(report_dir)])

    assert rc == 1          # user-aborted
    psu_ctor.assert_not_called()
    scope_ctor.assert_not_called()
```

- [ ] **Step 3: Run — fails (module missing)**

Run: `cd python && pytest cli/tests/test_run_test.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Implement the CLI**

Create `/home/alex/jbhack/python/cli/run_test.py`:

```python
# python/cli/run_test.py
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from executor.drivers import Dps150Adapter, AnalogDiscoveryScopeAdapter
from executor.runner import run, SafetyError
from executor.prompts import confirm
from planner.openai_planner import generate_plan, PlannerError
from planner.models import TestPlan


def _print_plan(plan: TestPlan) -> None:
    print(f"\n=== Test Plan: {plan.board_name} ===")
    print(f"Summary: {plan.summary}\n")
    for tc in plan.test_cases:
        print(f"{tc.id}  {tc.title}")
        print(f"  PSU:   {tc.psu.voltage}V @ {tc.psu.current_limit}A on {tc.psu.rail_name}")
        print(f"  Probe: {tc.probe.scope_channel} on {tc.probe.label} ({tc.probe.location_hint})")
        print(f"  Expect: kind={tc.expected.kind} nominal={tc.expected.nominal} tol={tc.expected.tolerance}")
        print(f"  Why:    {tc.rationale}")
        print()


def _print_summary(report) -> None:
    print("\n=== Report ===")
    for r in report.results:
        val = "n/a" if r.measurement_value is None else f"{r.measurement_value:.4f}V"
        print(f"{r.test_case_id}  {r.title:30s}  {r.verdict:8s}  {val}  (expected {r.expected_range_str})")
    passed = sum(1 for r in report.results if r.verdict == "PASS")
    print(f"Overall: {report.overall} ({passed}/{len(report.results)})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_test")
    parser.add_argument("schdoc", help="Path to .SchDoc file")
    parser.add_argument("--report-dir", default="reports", help="Where to write JSON reports")
    parser.add_argument("--yes", action="store_true", help="Skip approval prompt")
    args = parser.parse_args(argv)

    load_dotenv()

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from schdoc.parser import parse_schdoc

    try:
        summary = parse_schdoc(args.schdoc)
    except Exception as exc:
        print(f"error: failed to parse {args.schdoc}: {exc}", file=sys.stderr)
        return 2

    try:
        plan = generate_plan(summary)
    except PlannerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    _print_plan(plan)

    if not args.yes:
        if not confirm("Approve and run?"):
            print("Aborted.")
            return 1

    psu = Dps150Adapter()
    scope = AnalogDiscoveryScopeAdapter()
    try:
        report = run(plan, psu=psu, scope=scope)
    except SafetyError as exc:
        print(f"SAFETY ABORT: {exc}", file=sys.stderr)
        return 4

    _print_summary(report)

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    out = report_dir / f"{ts}-run.json"
    out.write_text(report.model_dump_json(indent=2))
    print(f"Written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Note: the parse entry-point is `parse_schdoc` — verify by running `grep -n "^def parse" python/schdoc/parser.py`. If it's named differently (e.g. `parse`), update the import in `cli/run_test.py` AND `planner/tests/test_openai_planner.py`.

- [ ] **Step 5: Run — CLI tests pass**

Run: `cd python && pytest cli/tests/test_run_test.py -v`
Expected: 2 tests pass.

- [ ] **Step 6: Manual smoke (no hardware, with stubbed OpenAI)**

Skip if you don't have an API key. Otherwise: `cd python && python -m cli.run_test ../Main.SchDoc` — expect a real plan to print, hit `n` at the approval prompt, verify clean exit with rc=1.

- [ ] **Step 7: Commit**

```bash
git add python/cli
git commit -m "feat(cli): run_test entry point"
```

---

## Task 8: Migrate `schdoc/llm.py` to OpenAI

**Files:**
- Modify: `python/schdoc/llm.py`, `python/schdoc/tests/test_llm.py`

- [ ] **Step 1: Inspect current test file**

Run: `cat python/schdoc/tests/test_llm.py` — understand what's there before rewriting.

- [ ] **Step 2: Rewrite `python/schdoc/llm.py`**

Replace entire contents of `/home/alex/jbhack/python/schdoc/llm.py` with:

```python
# python/schdoc/llm.py
from __future__ import annotations

import json
import os

from openai import OpenAI, OpenAIError

from .models import SchematicSummary


_SYSTEM = (
    "You are a hardware engineer. Given structured schematic data, "
    "write a concise 3-5 sentence board overview a test engineer can use "
    "to understand the board before generating hardware tests. "
    "Be specific: name components, voltages, and signal types. "
    "Do not speculate beyond the data provided."
)


def _build_context(summary: SchematicSummary) -> str:
    all_components = [c for z in summary.zones for c in z.components] + summary.connectors
    seen: set[str] = set()
    unique_components = []
    for c in all_components:
        if c.designator not in seen:
            seen.add(c.designator)
            unique_components.append(c)

    return json.dumps({
        "board_name": summary.board_name,
        "components": [
            {"designator": c.designator, "value": c.value, "description": c.description}
            for c in unique_components
        ],
        "power_rails": [
            {"name": r.name, "nominal_voltage": r.nominal_voltage,
             "source": r.source_designator, "loads": r.loads}
            for r in summary.power_rails
        ],
        "zones": [
            {"name": z.name, "components": [c.designator for c in z.components]}
            for z in summary.zones
        ],
        "probe_points": [
            {"label": p.label, "net": p.net, "type": p.probe_type, "expected": p.expected_range}
            for p in summary.probe_points
        ],
    }, indent=2)


def _fallback_understanding(summary: SchematicSummary) -> str:
    parts = [f"Board: {summary.board_name}."]
    non_gnd = [r for r in summary.power_rails if r.name.upper() != "GND"]
    if non_gnd:
        rail_strs = [
            f"{r.name} ({r.nominal_voltage}V)" if r.nominal_voltage is not None else r.name
            for r in non_gnd
        ]
        parts.append(f"Power rails: {', '.join(rail_strs)}.")
    non_other = [z for z in summary.zones if z.name != "Other"]
    if non_other:
        parts.append(f"Functional blocks: {', '.join(z.name for z in non_other)}.")
    if summary.connectors:
        parts.append(
            f"Connectors: {', '.join(c.designator for c in summary.connectors)} "
            f"({len(summary.connectors)} total)."
        )
    physical_tps = [p for p in summary.probe_points if p.probe_type == "physical_tp"]
    if physical_tps:
        parts.append(f"Physical test points: {', '.join(p.label for p in physical_tps)}.")
    return " ".join(parts)


def generate_understanding(summary: SchematicSummary) -> str:
    """Call OpenAI to generate board-understanding prose. Falls back to programmatic summary."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return _fallback_understanding(summary)

    model = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _build_context(summary)},
            ],
            max_tokens=512,
        )
        return resp.choices[0].message.content or _fallback_understanding(summary)
    except OpenAIError:
        return _fallback_understanding(summary)
```

- [ ] **Step 3: Update the test file**

Replace the contents of `/home/alex/jbhack/python/schdoc/tests/test_llm.py` with:

```python
from unittest.mock import MagicMock, patch

from schdoc.models import SchematicSummary, PowerRail
from schdoc.llm import generate_understanding, _fallback_understanding


def _summary():
    return SchematicSummary(
        board_name="Main",
        power_rails=[PowerRail(name="3V3", nominal_voltage=3.3, source_designator="U2", loads=["U3"])],
        zones=[], connectors=[], probe_points=[],
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
        assert "3V3" in generate_understanding(_summary())


def test_openai_error_falls_back(monkeypatch):
    from openai import OpenAIError
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with patch("schdoc.llm.OpenAI") as m:
        m.return_value.chat.completions.create.side_effect = OpenAIError("boom")
        assert "3V3" in generate_understanding(_summary())
```

You may need to adjust the `SchematicSummary(...)` constructor call if its fields differ — run `grep -A 30 "class SchematicSummary" python/schdoc/models.py` and match its dataclass signature.

- [ ] **Step 4: Run**

Run: `cd python && pytest schdoc/tests/test_llm.py -v`
Expected: 3 pass.

- [ ] **Step 5: Commit**

```bash
git add python/schdoc/llm.py python/schdoc/tests/test_llm.py
git commit -m "refactor(schdoc): migrate llm understanding generator to openai"
```

---

## Task 9: Migrate `agent/evaluator.py` tier-2 to OpenAI

**Files:**
- Create: `python/evaluator/tier2.py`
- Modify: `python/agent/evaluator_tier2.py`, `python/agent/evaluator.py`
- Update tests that were skipped in Task 2

- [ ] **Step 1: Implement shared tier-2**

Create `/home/alex/jbhack/python/evaluator/tier2.py`:

```python
# python/evaluator/tier2.py
from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI, OpenAIError


class Tier2Error(RuntimeError):
    pass


async def evaluate_tier2(
    measurements: dict[str, Any],
    expected_range: str,
    probe_point: dict[str, Any],
    board_understanding: str,
) -> tuple[str, str]:
    """Ask OpenAI to resolve a MARGINAL tier-1 verdict into PASS or FAIL.

    Sync under the hood but kept `async` to preserve the existing agent/runner API.
    Returns (verdict, reasoning).
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise Tier2Error("OPENAI_API_KEY not set")

    model = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
    prompt = (
        "You are evaluating a hardware measurement.\n"
        f"Probe point: {json.dumps(probe_point)}\n"
        f"Expected range: {expected_range}\n"
        f"Measurements: {json.dumps(measurements)}\n"
        f"Board context: {board_understanding}\n\n"
        "Respond with JSON: "
        '{"verdict": "PASS" | "FAIL", "reasoning": "one sentence"}'
    )

    client = OpenAI(api_key=api_key)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
    except OpenAIError as exc:
        raise Tier2Error(str(exc)) from exc

    raw = resp.choices[0].message.content
    if not raw:
        raise Tier2Error("empty response")
    data = json.loads(raw)
    verdict = data.get("verdict", "FAIL")
    reasoning = data.get("reasoning", "")
    if verdict not in ("PASS", "FAIL"):
        raise Tier2Error(f"bad verdict: {verdict}")
    return verdict, reasoning
```

Add to `/home/alex/jbhack/python/evaluator/__init__.py`:

```python
from .tier2 import evaluate_tier2, Tier2Error

__all__ = ["evaluate_tier1", "parse_expected_range", "evaluate_tier2", "Tier2Error"]
```

- [ ] **Step 2: Replace the placeholder in `python/agent/evaluator_tier2.py`**

Replace entire contents with:

```python
# python/agent/evaluator_tier2.py
from evaluator.tier2 import evaluate_tier2

__all__ = ["evaluate_tier2"]
```

- [ ] **Step 3: Write a unit test**

Create `/home/alex/jbhack/python/evaluator/tests/test_tier2.py`:

```python
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
        v, r = asyncio.run(evaluate_tier2({"v_mean":3.45}, "3.3V ± 5%", {}, "ctx"))
    assert v == "PASS"


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
```

- [ ] **Step 4: Run**

Run: `cd python && pytest evaluator/tests -v`
Expected: tier-1 and tier-2 tests pass.

- [ ] **Step 5: Un-skip any agent tests skipped in Task 2**

Run: `grep -rn "tier2 reworked" python/agent/tests/` — for every match, remove the `@pytest.mark.skip` decorator and verify the test passes against the new OpenAI-backed tier-2 (mocking OpenAI as above if needed).

- [ ] **Step 6: Commit**

```bash
git add python/evaluator python/agent/evaluator_tier2.py python/agent/evaluator.py python/agent/tests
git commit -m "feat(evaluator): migrate tier-2 verdict to openai"
```

---

## Task 10: Migrate `agent/runner.py` to OpenAI function calling

This is the largest single port. Follow the same rules as the existing Claude loop: each tool-use turn triggers tool dispatch; `require_probe` pauses via `session._resume_event`.

**Files:**
- Modify: `python/agent/runner.py`, `python/agent/config.py`, `python/agent/tools.py`, `python/agent/README.md`, `python/agent/tests/test_runner.py`

- [ ] **Step 1: Update config env vars**

Replace `/home/alex/jbhack/python/agent/config.py` contents with:

```python
# python/agent/config.py
import os

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
AGENT_MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "4096"))
AGENT_MAX_TOOL_ROUNDS = int(os.getenv("AGENT_MAX_TOOL_ROUNDS", "30"))
SCOPE_BACKEND = os.getenv("SCOPE_BACKEND", "waveforms")
SCOPE_BITSTREAM = os.getenv("SCOPE_BITSTREAM", "")
SCOPE_URL = os.getenv("SCOPE_URL", "ftdi://0x0403:0x6014/1")
MAX_VOLTAGE = float(os.getenv("MAX_VOLTAGE", "5.0"))
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "3600"))

# Back-compat alias: some legacy callers import AGENT_MODEL.
AGENT_MODEL = OPENAI_MODEL
```

- [ ] **Step 2: Translate tool schemas to OpenAI format**

OpenAI function tools use `{"type":"function","function":{"name":..., "parameters":...}}` where `parameters` is JSON schema. Anthropic's `input_schema` maps 1:1 to OpenAI's `parameters`.

Add to `/home/alex/jbhack/python/agent/tools.py` (keep the existing `TOOL_SCHEMAS` for reference but add a second constant):

```python
# python/agent/tools.py — append to end of file

OPENAI_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": s["name"],
            "description": s["description"],
            "parameters": s["input_schema"],
        },
    }
    for s in TOOL_SCHEMAS
]
```

- [ ] **Step 3: Rewrite `python/agent/runner.py`**

Replace entire contents of `/home/alex/jbhack/python/agent/runner.py` with:

```python
# python/agent/runner.py
from __future__ import annotations

import asyncio
import json
from typing import Any

from openai import AsyncOpenAI, OpenAIError

from . import config
from .config import OPENAI_MODEL, AGENT_MAX_TOKENS
from .models import HardwareContext, SessionState, TestSession
from .tools import OPENAI_TOOL_SCHEMAS, dispatch_tool


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
    """Drive the OpenAI tool-use loop for one test session."""
    client = AsyncOpenAI()
    system = build_system_prompt(session.schematic)
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": "Please begin testing the board. Work through all probe points."},
    ]

    try:
        for _ in range(config.AGENT_MAX_TOOL_ROUNDS):
            if session.state in (SessionState.COMPLETE, SessionState.FAILED):
                break

            resp = await client.chat.completions.create(
                model=OPENAI_MODEL,
                max_tokens=AGENT_MAX_TOKENS,
                tools=OPENAI_TOOL_SCHEMAS,
                messages=messages,
            )
            choice = resp.choices[0]
            msg = choice.message

            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in (msg.tool_calls or [])
                ],
            })

            if choice.finish_reason == "stop" and not msg.tool_calls:
                session.state = SessionState.COMPLETE
                break

            if not msg.tool_calls:
                session.state = SessionState.FAILED
                session.error = f"Unexpected finish_reason: {choice.finish_reason}"
                break

            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError as exc:
                    result = {"error": f"bad tool arguments: {exc}"}
                else:
                    result = await dispatch_tool(tc.function.name, args, session, hw)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })

                if session.state == SessionState.PROBE_REQUIRED:
                    await session._resume_event.wait()
                    session._resume_event.clear()
                    if session.state != SessionState.PROBE_REQUIRED:
                        return
                    session.state = SessionState.CAPTURING
        else:
            session.state = SessionState.FAILED
            session.error = f"Agent exceeded {config.AGENT_MAX_TOOL_ROUNDS} tool rounds"

    except OpenAIError as exc:
        session.state = SessionState.FAILED
        session.error = f"OpenAI error: {exc}"
    except Exception as exc:
        session.state = SessionState.FAILED
        session.error = str(exc)
    finally:
        try:
            hw.analog_discovery.disconnect()
        except Exception:
            pass
```

- [ ] **Step 4: Update agent runner tests**

Open `/home/alex/jbhack/python/agent/tests/test_runner.py`. Every `patch("anthropic.AsyncAnthropic")` or similar becomes `patch("agent.runner.AsyncOpenAI")`. Every mock response needs to produce `choices[0].message.tool_calls` structure instead of Anthropic's `content` blocks.

If the file is long, refactor the mock factory to a helper:

```python
from unittest.mock import MagicMock

def _openai_tool_call_response(name: str, args: dict, call_id: str = "call_1"):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    msg = MagicMock()
    msg.content = None
    msg.tool_calls = [tc]
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "tool_calls"
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _openai_final_response(text: str = "done"):
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = []
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    return resp
```

Wire the mocks as `side_effect=[_openai_tool_call_response(...), ..., _openai_final_response()]`.

- [ ] **Step 5: Run**

Run: `cd python && pytest agent/tests -v`
Expected: all tests pass.

- [ ] **Step 6: Update `python/agent/README.md`**

Replace every mention of `ANTHROPIC_API_KEY` with `OPENAI_API_KEY`, `claude -p` with `openai`, `claude-sonnet-4-6` with `gpt-5-mini`, `Claude` with `OpenAI`, and the "No API key config needed — reuses Claude Code auth" line with "Requires `OPENAI_API_KEY` in the environment (or `.env`)."

- [ ] **Step 7: Commit**

```bash
git add python/agent
git commit -m "feat(agent): migrate tool-use loop to openai function calling"
```

---

## Task 11: Sync `Scopecreep/` bundled schdoc/llm.py

The plugin ships its own copy of `schdoc/llm.py` + `requirements.txt` — must stay in lockstep or the plugin runs stale Anthropic code.

**Files:**
- Modify: `Scopecreep/src/main/resources/schdoc/llm.py`, `Scopecreep/src/main/resources/schdoc/requirements.txt`

- [ ] **Step 1: Diff-check upstream vs bundled**

Run: `diff /home/alex/jbhack/python/schdoc/llm.py /home/alex/jbhack/Scopecreep/src/main/resources/schdoc/llm.py`

- [ ] **Step 2: Copy updated file**

Run: `cp /home/alex/jbhack/python/schdoc/llm.py /home/alex/jbhack/Scopecreep/src/main/resources/schdoc/llm.py`

- [ ] **Step 3: Update bundled requirements**

Open `/home/alex/jbhack/Scopecreep/src/main/resources/schdoc/requirements.txt` and replace `anthropic>=*` with `openai>=1.40`. Keep all other lines.

- [ ] **Step 4: If `SchdocParserRunner.kt` references the env var, update it**

Run: `grep -n "ANTHROPIC_API_KEY\|anthropic" Scopecreep/src/main/kotlin/com/scopecreep/service/SchdocParserRunner.kt`

Replace any `ANTHROPIC_API_KEY` usage with `OPENAI_API_KEY`. Do not build the full plugin unless you're on Kotlin; just keep the Kotlin reference correct.

- [ ] **Step 5: Commit**

```bash
git add Scopecreep/src
git commit -m "chore(scopecreep): sync bundled schdoc/llm.py to openai"
```

---

## Task 12: Documentation + acceptance script

**Files:**
- Modify: `python/agent/README.md` (already touched in Task 10), add a top-level testing-procedure doc.
- Create: `docs/bench/2026-04-18-run-test-acceptance.md`

- [ ] **Step 1: Create the bench acceptance doc**

Create `/home/alex/jbhack/docs/bench/2026-04-18-run-test-acceptance.md`:

```markdown
# `run_test` Bench Acceptance

On a macOS machine with DPS-150 + Analog Discovery connected:

1. `git pull`
2. `python -m venv .venv && source .venv/bin/activate`
3. `pip install -r python/requirements.txt`
4. `cp .env.example .env` and paste `OPENAI_API_KEY`.
5. Plug in DPS-150 (USB) and Analog Discovery (USB). Confirm port with:
   - macOS: `ls /dev/cu.usbmodem*`
   - Linux: `ls /dev/ttyACM*`
   If auto-detect picks the wrong device, set `PSU_PORT=` in `.env`.
6. Connect probe ground clip to a known GND on the DUT.
7. Run: `python -m cli.run_test Main.SchDoc`
8. Review printed plan. Approve with `y`.
9. For each test case: follow the on-screen instructions, press Enter.
10. Confirm a report JSON appears in `reports/` and the overall verdict prints.

Pass criteria: no unhandled exceptions; PSU output is OFF at exit regardless of
outcome (verify with `ps`: `ps aux | grep python` shows nothing lingering, and
the DPS-150 front panel reads 0V/0A).
```

- [ ] **Step 2: Commit**

```bash
git add docs/bench
git commit -m "docs: run_test bench acceptance procedure"
```

- [ ] **Step 3: Full test-suite run**

Run: `cd python && pytest -v`
Expected: all tests pass. If any fail due to Anthropic stragglers, fix and commit.

- [ ] **Step 4: Run the full acceptance flow with hardware connected**

Follow the steps in `docs/bench/2026-04-18-run-test-acceptance.md`. If steps fail, file issues — do NOT silently patch over hardware bugs.

- [ ] **Step 5: Final cleanup commit (if needed)**

If any files were left dirty (formatting, imports), run `ruff check python/ --fix && ruff format python/` (if ruff is configured) and commit.

```bash
git add -A && git diff --staged
git commit -m "chore: post-acceptance cleanup"
```

---

## Self-review check

Spec coverage:
- `.env` + `.env.example` + `python-dotenv` loading — Task 1 + Task 7 (`load_dotenv()` in CLI). ✓
- OpenAI-first (no Anthropic anywhere) — Tasks 8, 9, 10, 11. ✓
- `schdoc` parsing unchanged; used by CLI — Task 7. ✓
- LLM generates `TestPlan` (option A) — Tasks 3 + 4. ✓
- Plan-then-approve gate — Task 7 `confirm()`. ✓
- Real DPS-150 + AD scope, cross-platform auto-detect — Task 5 adapters. ✓
- Tier-1 evaluator reused — Task 2 extraction + Task 6 use. ✓
- PSU off in `finally` — Task 6 + test. ✓
- Step-through UX per PSU and per probe — Task 6 `wait_enter` calls. ✓
- Report written to disk — Task 7. ✓
- Tier-2 fallback via OpenAI (decision: keep) — Task 9. ✓
- `Scopecreep/` bundled mirror updated — Task 11. ✓

No placeholders found. All test code is inline. Type names are consistent (`TestPlan`, `TestCase`, `PsuSetting`, `ProbeStep`, `ExpectedRange`, `Report`, `TestResult`) from Task 3 through Task 7.

Open implementation-time decisions (not blockers):
- Whether OpenAI's `strict: true` accepts the schema as emitted — Task 4 Step 5 has a `_strictify` helper ready.
- `parse_schdoc` vs `parse` as the entry-point name in `schdoc/parser.py` — confirm before writing Task 7 code.
