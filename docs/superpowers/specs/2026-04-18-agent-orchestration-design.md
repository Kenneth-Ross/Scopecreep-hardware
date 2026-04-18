# Agent Orchestration Layer — Design Spec

**Date:** 2026-04-18  
**Branch:** feat/agent-orchestration  
**Status:** Approved for implementation

---

## 1. Goal

Build an end-to-end orchestration layer that:
1. Accepts a parsed schematic (`SchematicSummary`) as input
2. Uses Claude (Anthropic SDK) in an autonomous tool-calling loop to generate and execute hardware test cases
3. Controls the PSU and oscilloscope via existing Python drivers
4. Pauses when the user must physically move a probe, resumes on request
5. Determines pass/fail per probe point via tier-1 numeric thresholds, escalating to Claude for marginal results
6. Exposes a REST API so any plugin (JetBrains, CLI, etc.) can drive sessions

Plugin integration is deferred. The REST surface is the plugin seam — build it right, integration is a later concern.

---

## 2. Architecture

```
Plugin / CLI
     │  REST (JSON)
     ▼
FastAPI  /agent/*
     │
     ▼
TestSession (asyncio task)
     │
     ├── Claude tool-use loop (Anthropic SDK)
     │       tools: psu_configure, scope_capture, require_probe, record_result
     │
     ├── Hardware drivers
     │       PSU  → backend/drivers/dps150.py  OR  python/drivers/analog_discovery/power.py
     │       Scope → python/drivers/analog_discovery/oscilloscope.py
     │
     └── Evaluator
             Tier 1: numeric threshold check (parse expected_range string)
             Tier 2: Claude interpretation (marginal cases only)
```

---

## 3. Session State Machine

```
planning → probe_required → capturing → evaluating ─┬→ probe_required (next point)
                                                     ├→ complete
                                                     └→ failed
```

| State | Meaning |
|---|---|
| `planning` | Claude reading schematic, ordering test sequence |
| `probe_required` | Agent paused; plugin must show probe instructions to user and call `/resume` |
| `capturing` | PSU/scope commands executing against hardware |
| `evaluating` | Tier-1 numeric check; Claude called if result is marginal |
| `complete` | All probe points tested; report available |
| `failed` | Unrecoverable error (hardware disconnect, agent error) |

Sessions live in-memory. TTL: 1 hour. No database.

---

## 4. REST API

### Start a session
```
POST /agent/sessions
Body: {
  "schematic": <SchematicSummary as JSON>,
  "config": {                          // all optional
    "psu_port": "/dev/ttyACM0",        // overrides env default
    "scope_bitstream": "/path/to.bit"  // overrides env default
  }
}
Response: { "session_id": "uuid4", "status": "planning" }
```

### Poll session status
```
GET /agent/sessions/{id}
Response: {
  "session_id": "...",
  "status": "probe_required" | "capturing" | "evaluating" | "complete" | "failed",
  "current_probe": {          // present when status == "probe_required"
    "label": "TP3",
    "net": "VCC_3V3",
    "location_hint": "C12 drain, left pad",
    "probe_type": "physical_tp",
    "instructions": "Place CH1 probe tip on TP3. Ensure GND clip is on GND plane."
  },
  "progress": { "completed": 3, "total": 8 },
  "error": "..."              // present when status == "failed"
}
```

### Resume after probe placed
```
POST /agent/sessions/{id}/resume
Response: { "status": "capturing" }
```

### Get final report
```
GET /agent/sessions/{id}/report
Response: {
  "session_id": "...",
  "board_name": "...",
  "overall": "PASS" | "FAIL" | "PARTIAL",
  "results": [
    {
      "probe_point": "TP3",
      "net": "VCC_3V3",
      "verdict": "PASS" | "FAIL" | "MARGINAL",
      "expected_range": "3.3V ± 5%",
      "measurements": { "v_mean": 3.31, "v_pp": 0.04, "v_rms": 3.31 },
      "reasoning": "...",      // Claude's interpretation when tier-2 was invoked
      "tier": 1 | 2            // which tier reached the verdict
    }
  ]
}
```

### Cancel session
```
DELETE /agent/sessions/{id}
Response: { "status": "cancelled" }
```

---

## 5. Agent Tools

Claude is given four tools. All hardware execution happens in Python; Claude only decides *what* to call and *when*.

### `psu_configure`
```json
{
  "name": "psu_configure",
  "description": "Set PSU voltage/current and enable/disable output. Call before any scope capture that requires the board to be powered.",
  "input_schema": {
    "channel": "int (0 or 1)",
    "voltage": "float (volts)",
    "current_limit": "float (amps)",
    "enabled": "bool"
  }
}
```
Hardcoded safety clamps in Python (not enforced by Claude): `MAX_VOLTAGE = 30.0`, `MAX_CURRENT = 5.0`. Exceeding → 400 error, session → `failed`.

### `scope_capture`
```json
{
  "name": "scope_capture",
  "description": "Configure and trigger a scope capture on one channel. Returns waveform statistics. Call after require_probe has been acknowledged.",
  "input_schema": {
    "channel": "int (0 or 1)",
    "voltage_range": "float (V peak-to-peak, must be in [0.5,1,2,5,10,25,50])",
    "sample_rate": "float (Hz, default 1e6)",
    "num_samples": "int (default 1024)",
    "trigger_source": "str ('none'|'ch0'|'ch1'|'ext', default 'none')",
    "trigger_level": "float (volts, default 0.0)",
    "trigger_edge": "str ('rising'|'falling', default 'rising')"
  },
  "returns": {
    "v_min": "float", "v_max": "float", "v_mean": "float",
    "v_pp": "float", "v_rms": "float",
    "sample_rate": "float", "num_samples": "int"
  }
}
```

### `require_probe`
```json
{
  "name": "require_probe",
  "description": "Pause the session and instruct the user to place the oscilloscope probe. The agent loop blocks until /resume is called.",
  "input_schema": {
    "probe_point_label": "str",
    "net": "str",
    "location_hint": "str  (human-readable, e.g. 'C12 drain, left pad')",
    "probe_type": "str ('physical_tp'|'power_rail'|'signal')",
    "instructions": "str  (full sentence instruction for the user)"
  }
}
```

### `record_result`
```json
{
  "name": "record_result",
  "description": "Record a pass/fail verdict for the current probe point. Call after evaluating measurements.",
  "input_schema": {
    "probe_point_label": "str",
    "verdict": "str ('PASS'|'FAIL'|'MARGINAL')",
    "reasoning": "str",
    "measurements": "object"
  }
}
```

---

## 6. Evaluator (Tier-1 / Tier-2)

### Tier-1: Numeric threshold

Parses `ProbePoint.expected_range` strings into a numeric bound:

| Pattern | Example | Rule |
|---|---|---|
| `X V ± Y%` | `3.3V ± 5%` | mean in `[X*(1-Y/100), X*(1+Y/100)]` |
| `X V ± Y mV` | `3.3V ± 165mV` | mean within absolute tolerance |
| `> X V` / `< X V` | `> 2.5V` | one-sided bound on mean |
| `X–Y V` | `0–0.5V` | mean in `[X, Y]` |

Verdict:
- **PASS** — within bounds
- **FAIL** — outside 2× the tolerance band
- **MARGINAL** — between 1× and 2× the band (escalate to tier-2)

### Tier-2: Claude interpretation

Called only for MARGINAL results. Prompt includes:
- ProbePoint metadata (net, designator, probe_type, expected_range)
- Full waveform stats (v_min, v_max, v_mean, v_pp, v_rms)
- Board understanding from `SchematicSummary.understanding`
- Surrounding power rail and zone context

Claude returns a structured verdict (`PASS` | `FAIL`) with a `reasoning` string that goes into the report.

---

## 7. System Prompt

Claude receives a system prompt at session start containing:

1. Role: "You are a hardware test agent. You control a power supply and oscilloscope to validate a PCB against its schematic."
2. Full `SchematicSummary` serialized as JSON (board name, understanding, power rails, probe points with expected ranges)
3. Rules:
   - Always call `require_probe` before `scope_capture`
   - Always call `record_result` after evaluating each probe point
   - Test power rails before signal nets
   - If PSU measurement shows no current draw after enabling, call `record_result` with FAIL and stop
   - Do not invent probe points not in the schematic summary
4. User confirmation gate: "Before energizing the board, state what voltage you will apply and wait for the `resume` signal."

---

## 8. File Map

| Path | Responsibility |
|---|---|
| `python/agent/__init__.py` | Package root |
| `python/agent/models.py` | `TestSession`, `SessionState`, `ProbeInstruction`, `TestResult` dataclasses |
| `python/agent/tools.py` | Tool definitions (JSON schema dicts) + Python handler functions |
| `python/agent/evaluator.py` | Tier-1 `expected_range` parser + numeric verdict; Tier-2 Claude call |
| `python/agent/runner.py` | Async Claude tool-use loop; session lifecycle management |
| `python/agent/server.py` | FastAPI router: `/agent/sessions` CRUD + resume endpoint |
| `python/agent/config.py` | `AGENT_MODEL`, `AGENT_MAX_TOKENS`, `PSU_PORT`, `SCOPE_BITSTREAM` from env |
| `python/api/main.py` | Mount agent router alongside existing hardware routes |

---

## 9. Configuration

```
AGENT_MODEL=claude-sonnet-4-6          # Anthropic model ID
AGENT_MAX_TOKENS=4096
AGENT_MAX_TOOL_ROUNDS=30               # safety cap on agent loop iterations
PSU_PORT=/dev/ttyACM0
PSU_BAUD=115200
SCOPE_BITSTREAM=/path/to/bitstream.bit
MAX_VOLTAGE=30.0                       # hardware safety clamp
MAX_CURRENT=5.0
SESSION_TTL_SECONDS=3600
```

---

## 10. Key Design Decisions

1. **Claude IS the orchestrator.** No separate planning pass — Claude reads the schematic and decides test order, PSU settings, and scope parameters directly via tool calls.
2. **`require_probe` is the only user-blocking primitive.** All other tool calls are non-interactive. This gives the plugin one clean handshake to build UI around.
3. **Tier-1 thresholds are deterministic Python, not LLM.** Saves tokens and latency on clear pass/fail cases.
4. **Safety clamps in Python, not in Claude's instructions.** The LLM can be prompted to ignore instructions; the Python handler cannot.
5. **In-memory sessions, no DB.** Runs are short-lived (minutes). Persistence is a future concern.
6. **Single FastAPI router mounted on existing server.** No new process — the agent shares the hardware driver handles opened at lifespan.
7. **Model is env-configurable.** Default `claude-sonnet-4-6` for testing; swap to any Anthropic or OpenAI-compatible model without code changes.
