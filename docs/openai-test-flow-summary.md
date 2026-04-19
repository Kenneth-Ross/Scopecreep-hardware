# OpenAI Schdoc Test-Flow — Delivered Work

**Branch:** `feat/openai-test-flow` (13 commits ahead of `feat/agent-orchestration`)
**Spec:** `docs/superpowers/specs/2026-04-18-openai-schdoc-test-flow-design.md`
**Plan:** `docs/superpowers/plans/2026-04-18-openai-schdoc-test-flow.md`
**Bench procedure:** `docs/bench/2026-04-18-run-test-acceptance.md`

## What ships

1. **Python CLI** — `python -m cli.run_test <path/to/.SchDoc>` parses a schematic, asks OpenAI for a structured test plan, prints the plan for user approval, then walks the user through every test case — driving the DPS-150 PSU and Analog Discovery scope automatically and prompting per probe placement. Writes a JSON report to `reports/`.
2. **OpenAI-only LLM stack** — every Anthropic call in the repo migrated to OpenAI. `.env` with `OPENAI_API_KEY` is the sole config.
3. **Token / cost tracking** — every OpenAI call appends a record to `reports/usage.jsonl`; the CLI prints a session total. Default rates target gpt-5-mini (`$0.25/1M` in, `$2/1M` out), overridable via `OPENAI_INPUT_COST_PER_1M` / `OPENAI_OUTPUT_COST_PER_1M`.

## Package layout added

| Package | Responsibility | LLM? | Hardware? |
|---|---|---|---|
| `python/planner/` | Pydantic `TestPlan`, OpenAI structured-output generator | yes | no |
| `python/executor/` | Deterministic runner; DPS-150 + AD scope adapters; safety caps | no | yes |
| `python/evaluator/` | Tier-1 numeric verdict (shared); tier-2 OpenAI judge (marginals) | partial | no |
| `python/cli/` | `run_test.py` — wires everything; parses args; writes report | no | no |
| `python/_openai_usage.py` | `log_usage(response, label)` + `session_totals()` | — | — |

## Usage

```bash
# One-time setup
cp .env.example .env   # then paste OPENAI_API_KEY

# Run
cd python
python -m cli.run_test ../Main.SchDoc
# → prints plan
# → "Approve and run? [y/N]"  (y to execute; n to abort cleanly)
# → steps through PSU set + probe placement per test case
# → writes reports/<ISO8601>-run.json
```

Exit codes: `0` success, `1` user aborted, `2` parse error, `3` planner error, `4` safety violation (voltage/current cap exceeded).

## Hard safety boundaries

- Executor rejects any test case with `voltage > MAX_VOLTAGE` or `current_limit > MAX_CURRENT` before touching hardware.
- PSU `output_off()` runs in a `finally` block — including on `KeyboardInterrupt` and any exception inside the test loop.
- Planner is **single-shot, fails loud**: no retry, no fallback plan. Running a stubbed plan on real hardware would be unsafe.
- `ExpectedRange` has a Pydantic validator enforcing the `kind`↔fields contract (e.g. `kind=range` requires both `lower` and `upper`). A malformed plan is rejected before it can run.

## Evidence (live run, 2026-04-18)

One real OpenAI call against `Main.SchDoc` (8 CAN-testpoint + power-rail + GND test cases):

```
[usage] planner: 3750 in + 5543 out = 9293 tok  ~$0.012
```

Plan correctly:
- identified TPS62932 (U1) as the 3V3 regulator; chose 5V input feed
- refused to drive 12V from the bench PSU (respected `MAX_VOLTAGE=5.0`)
- located all four CAN test points (J3.1 / J4.1 / J5.1 / J6.1)
- picked appropriate `ExpectedRange.kind` per test:
  `pct` for the 3V3 nominal, `abs_mv` for ripple, `range` for CAN recessive window, `lt` for "12V should not be present"

## Budget math

- First planner call: ~$0.012 (9,293 tokens).
- At that size: ~4,000 runs on a $50 budget before exhaustion.
- Tier-2 evaluator calls only fire on MARGINAL verdicts (none in the deterministic CLI path today).

## What's untested

Runs on the bench are needed to exercise:
1. DPS-150 serial adapter (`backend/drivers/dps150.py` loaded via `importlib`).
2. Analog Discovery scope `capture()` on real hardware.
3. End-to-end probe step-through + JSON report writing.

Procedure is in `docs/bench/2026-04-18-run-test-acceptance.md`.

## Known follow-ups (non-blocking)

- `cli/run_test.py` has no `--plan-only` flag. Adding one would make dev iteration on the prompt cheaper (approve-n currently works but is slightly awkward).
- Tier-2 evaluator is wired up but unused by the CLI; only reachable via the REST `agent/` path.
- Kotlin plugin still hits the Python sidecar for hardware; moving the LLM loop into Kotlin is a separate milestone.
