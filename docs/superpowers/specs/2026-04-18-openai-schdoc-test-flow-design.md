# OpenAI-Driven Schematic→Test-Plan→Probe-Step CLI

**Status:** approved
**Date:** 2026-04-18
**Owner:** @hernantech
**Related:** `2026-04-18-schdoc-parser-design.md`, `2026-04-18-agent-orchestration-design.md`

## Goal

Give a user on the bench (macOS, DPS-150 PSU + Analog Discovery oscilloscope) a Python CLI that: parses a schematic, asks OpenAI for a structured test plan, prints the plan for approval, then walks the user through every test case — setting the PSU automatically and prompting the user per probe placement — and writes a pass/fail report.

Secondary goal: replace all existing Anthropic/Claude API calls in the repo with OpenAI, so the project has a single LLM provider.

## Non-goals

- Plugin (Kotlin) integration. That comes next; this spec is deliberately CLI-only.
- Hardware mocking as default. The executor has a test seam (dependency-injected fake driver classes) but the user-facing flow requires real hardware.
- Tier-2 (LLM-judged) pass/fail. Tier-1 numeric verdict only; the executor never calls the LLM.
- Mid-run adaptive re-planning. The plan is fixed at approval time.

## User flow

```
$ python -m cli.run_test Main.SchDoc
[parser] 7 connectors, 4 rails, 4 physical test points
[planner] calling openai (gpt-5-mini)...

=== Test Plan: Main ===
Summary: <3-5 sentence board overview>

TC-01  3V3 rail nominal
  PSU: 12V @ 0.5A on 12V rail
  Probe: CH1 on TP3 (C12 left pad, near U2)
  Expect: 3.3V ± 5%  (v_mean)
  Why: U2 is a 3V3 LDO fed from 12V; verify rail is in spec.

TC-02  12V input sag under nominal load
  ...

Approve and run? [y/N] y

── TC-01: 3V3 rail nominal ──
PSU set to 12V @ 0.5A. Press Enter to energize.
[psu] output on
Place CH1 tip on TP3, GND clip to nearest GND via. Press Enter to capture.
[scope] v_mean=3.312V v_pp=0.038V
→ 3.312V  [PASS]

...

=== Report ===
TC-01  3V3 rail nominal          PASS   3.312V  (expected 3.3V ± 5%)
TC-02  12V input sag             PASS   11.98V  (expected > 11.5V)
Overall: PASS (2/2)
Written: reports/2026-04-18T14-32-run.json
```

## Architecture

```
.env                             # OPENAI_API_KEY, OPENAI_MODEL, MAX_VOLTAGE, MAX_CURRENT
.env.example                     # committed template

python/
├── cli/
│   └── run_test.py              # entry point
├── planner/
│   ├── __init__.py
│   ├── models.py                # TestPlan / TestCase / PsuSetting / ProbeStep / ExpectedRange
│   └── openai_planner.py        # generate_plan(SchematicSummary) -> TestPlan
├── executor/
│   ├── __init__.py
│   ├── runner.py                # run(TestPlan) -> Report
│   └── prompts.py               # interactive stdin prompts
├── schdoc/…                     # existing; parser unchanged
├── drivers/…                    # existing; PSU + AD scope
└── agent/…                      # existing Anthropic REST path; migrated to OpenAI in the same PR
```

**Boundary rules (enforced by imports):**

- `planner/` may not import `drivers/` or `executor/`.
- `executor/` may not import `openai` or `planner/openai_planner.py`.
- Both `planner/` and `executor/` depend only on `schdoc/models.py` and `planner/models.py`.

The planner and executor are separable so the planner can move to the Kotlin plugin later without touching the executor.

## Data model (`python/planner/models.py`)

```python
class PsuSetting(BaseModel):
    channel: Literal["main"] = "main"
    voltage: float            # volts; executor re-validates against MAX_VOLTAGE
    current_limit: float      # amps; executor re-validates against MAX_CURRENT
    rail_name: str            # human-facing: "12V"

class ProbeStep(BaseModel):
    label: str                # "TP3" or synthetic e.g. "3V3_at_U2"
    net: str                  # "VCC_3V3"
    location_hint: str        # "C12 left pad, near U2"
    probe_type: Literal["physical_tp", "component_pin", "net_trace"]
    scope_channel: Literal["CH1", "CH2"]
    user_instructions: str    # full probe-placement sentence shown to user

class ExpectedRange(BaseModel):
    kind: Literal["pct", "abs_mv", "gt", "lt", "range"]
    nominal: float | None = None
    tolerance: float | None = None     # 5 (%) or 165 (mV), depending on kind
    lower: float | None = None
    upper: float | None = None

class TestCase(BaseModel):
    id: str                   # "TC-01"
    title: str
    rationale: str            # 1-2 sentences from the LLM
    psu: PsuSetting
    probe: ProbeStep
    expected: ExpectedRange
    measurement: Literal["v_mean", "v_pp", "v_rms"]

class TestPlan(BaseModel):
    board_name: str
    summary: str              # 3-5 sentence overview
    test_cases: list[TestCase]
```

The JSON schema derived from `TestPlan.model_json_schema()` is sent as
`response_format={"type":"json_schema","json_schema":{...,"strict":True}}`.

## Planner (`python/planner/openai_planner.py`)

System prompt:
> You are a hardware test engineer. Given a parsed schematic summary, produce a JSON test plan that verifies each power rail and any explicit test points on the board. For each test case: pick a PSU voltage and current limit justified by the schematic's nominal rails; never exceed {MAX_VOLTAGE} volts on any channel; specify a physical probe location a human can find (reference designator + pin or pad, plus a nearby landmark); give an expected range grounded in the schematic (nominal ± tolerance). Prefer probe points the parser already extracted; you MAY add rail-level tests (ripple, load-step) only if the schematic supports it. Be concrete. Do not invent components not in the summary.

Context shape (JSON, same fields as existing `schdoc/llm.py::_build_context`): `board_name`, `components`, `power_rails`, `zones`, `probe_points`, plus an added `max_voltage` top-level field.

Single OpenAI call (no tool use, no streaming). Model from `OPENAI_MODEL` env, default `gpt-5-mini`.

**Error handling — all fatal, no fallback:**
- Missing `OPENAI_API_KEY` → exit 2 with pointer to `.env.example`.
- API error (rate limit, 5xx, network) → exit 3 with the raw error.
- Schema-validation failure → exit 4 with Pydantic error + raw response body.

No silent fallback test plan; running stubbed tests on real hardware is unsafe.

## CLI (`python/cli/run_test.py`)

```
python -m cli.run_test <path-to-.SchDoc> [--report-dir reports/] [--yes]
```

Steps:
1. `load_dotenv()` from project root.
2. `summary = schdoc.parser.parse(path)`.
3. `plan = planner.openai_planner.generate_plan(summary)`.
4. `print_plan(plan)` — numbered test cases, PSU voltage highlighted red if > any rail nominal in `summary`.
5. Confirm `Approve and run? [y/N]`; `--yes` skips.
6. `report = executor.runner.run(plan)`.
7. Write JSON report to `reports/<ISO8601>-run.json`, print summary table.

## Executor (`python/executor/runner.py`)

```python
def run(plan: TestPlan, psu: PsuDriver, scope: ScopeDriver,
        stdin: TextIO = sys.stdin) -> Report:
    results: list[TestResult] = []
    try:
        for tc in plan.test_cases:
            _safety_check(tc.psu)             # MAX_VOLTAGE, MAX_CURRENT
            _print_header(tc)

            psu.set(tc.psu.voltage, tc.psu.current_limit)
            _wait_enter(f"PSU set to {tc.psu.voltage}V @ {tc.psu.current_limit}A "
                        f"on {tc.psu.rail_name}. Press Enter to energize.", stdin)
            psu.output_on()

            _wait_enter(tc.probe.user_instructions + "  Press Enter to capture.", stdin)
            meas = scope.capture(channel=tc.probe.scope_channel)
            value = meas[tc.measurement]

            verdict = evaluate(tc.expected, value)   # reuse agent/evaluator tier-1
            _print_result(tc, value, verdict)
            results.append(TestResult(tc, meas, verdict))
    finally:
        psu.output_off()
        psu.close()
        scope.close()
    return Report(plan=plan, results=results)
```

- `psu` and `scope` are injected so tests pass fakes.
- `evaluate()` is lifted from `python/agent/evaluator.py` (tier-1 only) into a shared location (`python/evaluator/tier1.py`) during this work; the `agent/` path imports it from there too.
- `KeyboardInterrupt` is not caught; the `finally` block still runs, guaranteeing PSU off. Partial report is written by the CLI via `try/except` around `executor.run`.
- A test case that raises `SafetyError` aborts the whole run with PSU off.

## Anthropic → OpenAI migration (same PR)

Files changed:
| File | Change |
|---|---|
| `python/schdoc/llm.py` | Replace `anthropic.Anthropic` with `openai.OpenAI`; model from `OPENAI_MODEL`; keep existing fallback-to-programmatic-summary behavior (this is prose, not a test plan — safe to fall back). |
| `python/agent/runner.py` | Replace Claude tool-use loop with OpenAI function-calling; tool schemas translated. Kept as Anthropic path is removed. |
| `python/agent/evaluator.py` | Tier-2 `claude -p` shell-out replaced with an OpenAI call (same prompt, structured JSON response); or deleted if tier-2 is descoped — decided at implementation time, default is replace. |
| `python/agent/README.md` | Update env vars and examples. |
| `python/requirements.txt` | Remove `anthropic`, add `openai`, add `python-dotenv`. |
| `Scopecreep/src/main/resources/schdoc/llm.py` | Same change as `python/schdoc/llm.py` (it's a bundled copy). |
| `Scopecreep/src/main/resources/schdoc/requirements.txt` | Mirror requirement changes. |
| Tests referring to Anthropic | Updated or skipped behind `OPENAI_API_KEY` markers. |

Env var migration: `ANTHROPIC_API_KEY` → `OPENAI_API_KEY`; `AGENT_MODEL` → `OPENAI_MODEL`. Old names not supported (clean break; pre-MVP).

## Configuration

`.env` at repo root (gitignored), loaded via `python-dotenv`:

```
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5-mini
MAX_VOLTAGE=5.0
MAX_CURRENT=1.0
```

`.env.example` is committed with placeholder values.

## Testing

- `python/planner/tests/test_models.py` — Pydantic accepts a valid fixture plan and rejects one with missing required fields. Pure unit test.
- `python/planner/tests/test_openai_planner.py` — integration test, `@pytest.mark.skipif(not OPENAI_API_KEY)`; runs against the real `Main.SchDoc`; asserts every test case has `voltage ≤ MAX_VOLTAGE` and references a component designator that exists in the summary.
- `python/executor/tests/test_runner.py` — canned `TestPlan` fed to `run()` with `FakePsu` + `FakeScope` (not MagicMock); asserts (a) results match canned scope readings, (b) `psu.output_off()` is called when scope raises, (c) safety violation aborts before `output_on()`.
- `python/evaluator/tests/test_tier1.py` — moved from `agent/tests/` unchanged.
- No end-to-end hardware test in CI. README documents the bench acceptance run.

## Acceptance

On a macOS machine with hardware connected, after `git pull && pip install -r python/requirements.txt`:

```
cp .env.example .env && $EDITOR .env     # paste OPENAI_API_KEY
python -m cli.run_test Main.SchDoc
```

…produces a plan, user approves, steps through all probes, and a report file is written. Overall verdict prints to stdout. No unhandled exceptions; PSU output is off at exit regardless of outcome.

## Open questions

None at spec time. Implementation-time decisions explicitly deferred:
- Whether to keep tier-2 evaluator as OpenAI-backed or drop it. Default is keep-and-port.
- Exact JSON schema shape for `response_format` given that `strict: true` has OpenAI-specific restrictions (e.g., no `oneOf`); may require flattening `ExpectedRange.kind` into separate optional fields.
