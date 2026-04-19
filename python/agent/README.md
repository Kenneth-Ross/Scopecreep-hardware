# Agent Orchestration Layer

Drives an autonomous hardware test session using OpenAI. Reads a parsed schematic, controls the PSU and oscilloscope via the Analog Discovery driver, and determines pass/fail per probe point.

No plugin integration required — the REST API is the seam.

---

## How it works

```
POST /agent/sessions   ← start with SchematicSummary JSON
        │
        ▼
OpenAI function-call loop
        ├── psu_configure   → Analog Discovery PSU
        ├── require_probe   → pauses; plugin shows probe instruction to user
        ├── scope_capture   → Analog Discovery oscilloscope
        └── record_result   → tier-1 numeric verdict; openai for MARGINAL
        │
        ▼
GET /agent/sessions/{id}/report   ← PASS / FAIL / PARTIAL + per-point results
```

**Probe pause/resume:** When OpenAI calls `require_probe`, the session enters `probe_required` state and blocks. The plugin calls `POST /agent/sessions/{id}/resume` once the user has placed the probe, and the loop continues.

---

## REST API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/agent/sessions` | Start a session |
| `GET` | `/agent/sessions/{id}` | Poll status and current probe instruction |
| `POST` | `/agent/sessions/{id}/resume` | Unblock after probe placed |
| `GET` | `/agent/sessions/{id}/report` | Final results (202 while running) |
| `DELETE` | `/agent/sessions/{id}` | Cancel |

### Start a session

```bash
curl -X POST http://localhost:8000/agent/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "schematic": { ...SchematicSummary as JSON... },
    "config": {
      "scope_bitstream": "/path/to/bitstream.bit",
      "scope_url": "ftdi://0x0403:0x6014/1"
    }
  }'
# → {"session_id": "uuid", "status": "planning"}
```

### Poll until probe required

```bash
curl http://localhost:8000/agent/sessions/{id}
# → {
#     "status": "probe_required",
#     "current_probe": {
#       "label": "TP3",
#       "net": "VCC_3V3",
#       "location_hint": "C12 drain, left pad",
#       "probe_type": "physical_tp",
#       "instructions": "Place CH1 probe tip on TP3. GND clip to GND plane."
#     },
#     "progress": {"completed": 2, "total": 8}
#   }
```

### Resume after placing probe

```bash
curl -X POST http://localhost:8000/agent/sessions/{id}/resume
# → {"status": "capturing"}
```

### Get report

```bash
curl http://localhost:8000/agent/sessions/{id}/report
# → {
#     "overall": "PASS",
#     "results": [
#       {
#         "probe_point": "TP3",
#         "net": "VCC_3V3",
#         "verdict": "PASS",
#         "expected_range": "3.3V ± 5%",
#         "measurements": {"v_mean": 3.31, "v_pp": 0.04, "v_rms": 3.31},
#         "reasoning": "...",
#         "tier": 1
#       }
#     ]
#   }
```

---

## Pass/fail verdict

**Tier 1 — deterministic (always runs)**

Parses `expected_range` strings and checks `v_mean` against numeric bounds:

| Pattern | Example | Rule |
|---------|---------|------|
| `X V ± Y%` | `3.3V ± 5%` | mean within `[X·(1−Y/100), X·(1+Y/100)]` |
| `X V ± Y mV` | `3.3V ± 165mV` | mean within absolute tolerance |
| `> X V` | `> 2.5V` | mean above lower bound |
| `< X V` | `< 0.5V` | mean below upper bound |
| `X–Y V` | `0–0.5V` | mean within range |

Verdict: **PASS** (within bounds) / **FAIL** (>2× outside) / **MARGINAL** (1–2× outside).

**Tier 2 — OpenAI judgment (MARGINAL only)**

Calls the OpenAI API with probe point metadata, waveform stats, and board context. Returns `PASS` or `FAIL` with a reasoning string. Requires `OPENAI_API_KEY` in the environment (or `.env`).

---

## Configuration

All via environment variables:

```bash
OPENAI_MODEL=gpt-5-mini             # model for runner loop (needs OPENAI_API_KEY)
AGENT_MAX_TOKENS=4096
AGENT_MAX_TOOL_ROUNDS=30            # safety cap on loop iterations
SCOPE_BACKEND=waveforms             # "waveforms" (pydwf, stock firmware) | "pti" (custom bitstream)
SCOPE_BITSTREAM=/path/to/file.bit   # required only when SCOPE_BACKEND=pti
SCOPE_URL=ftdi://0x0403:0x6014/1    # required only when SCOPE_BACKEND=pti
MAX_VOLTAGE=5.0                     # Analog Discovery V+/V− hardware limit
SESSION_TTL_SECONDS=3600
```

---

## Files

| File | Responsibility |
|------|---------------|
| `config.py` | Env-based configuration |
| `models.py` | `TestSession`, `SessionState`, `ProbeInstruction`, `TestResult` |
| `evaluator.py` | Tier-1 range parser + verdict; tier-2 OpenAI call |
| `tools.py` | Tool schemas (JSON) + Python handlers; hardware safety clamps |
| `runner.py` | Async OpenAI function-call loop; session lifecycle |
| `server.py` | FastAPI router mounted at `/agent/*` |

---

## Running the demo

No hardware needed (requires `OPENAI_API_KEY`):

```bash
cd python/
python demo_agent.py
```

Parses `Main.SchDoc`, walks a full session through every state with stub hardware, hits all REST endpoints, and prints each step.
