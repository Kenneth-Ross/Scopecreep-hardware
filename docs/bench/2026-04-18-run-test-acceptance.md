# `run_test` Bench Acceptance

On a macOS machine with DPS-150 + Analog Discovery connected:

1. `git pull origin feat/openai-test-flow`
2. `python -m venv .venv && source .venv/bin/activate`
3. `pip install -r python/requirements.txt`
4. `cp .env.example .env` and paste `OPENAI_API_KEY` (and adjust `OPENAI_MODEL` / `MAX_VOLTAGE` if needed).
5. Plug in DPS-150 (USB) and Analog Discovery (USB). Confirm port:
   - macOS: `ls /dev/cu.usbmodem*`
   - Linux: `ls /dev/ttyACM*`
   If auto-detect picks the wrong device, set `PSU_PORT=/dev/...` in `.env`.
6. Connect oscilloscope ground clip to a known GND on the DUT.
7. From the repo root: `cd python && python -m cli.run_test ../Main.SchDoc`
8. Review the printed plan. Approve with `y`.
9. For each test case, follow the on-screen instructions and press Enter at each prompt.
10. Confirm a report JSON appears in `python/reports/` and the overall verdict prints to stdout.

## Pass criteria

- No unhandled exceptions.
- PSU output is OFF at exit regardless of outcome. Verify: DPS-150 front panel reads 0 V / 0 A.
- `ps aux | grep python` shows nothing lingering from the run.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `OPENAI_API_KEY is not set` | Edit `.env`, ensure the key is not the `sk-REPLACE_ME` placeholder |
| `DPS-150 serial port not found` | Explicitly set `PSU_PORT` in `.env` |
| `SAFETY ABORT: ... exceeds MAX_VOLTAGE` | The LLM proposed a voltage above your `MAX_VOLTAGE` limit. Raise the limit in `.env` OR regenerate the plan. |
| Scope returns ~0 V on every probe | Check PSU output is actually on; scope GND is connected; probe is on the right net |
| `OpenAI API error: ...` | Network issue or invalid key. Re-run — planner is single-shot, no retries. |
