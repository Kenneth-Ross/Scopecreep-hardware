# Integration status — plugin ↔ agent ↔ firmware

Snapshot: 2026-04-19. Branch `feat/plugin-agent-integration` in both repos
(`/home/alex/jbhack` and `/home/alex/jbhack/Scopecreep`). Working tree is
uncommitted.

This doc is the handoff contract between three concurrent efforts:

| Effort | Owner | Status |
|---|---|---|
| Python backend: LLM ↔ hardware test loop + schdoc parsing | other agent | in progress |
| JetBrains plugin UI + sidecar bundling + UI surfaces | this branch | implemented, unverified end-to-end |
| LangGraph firmware pipeline | sibling repo `~/benchy/pipeline/` | lives out-of-repo; touched via Supabase only |

When the agent-side work lands, the plugin is the thin layer that wires the
existing HTTP surface into IDE UI. No further plugin changes should be needed
unless the backend API shape changes.

---

## What the plugin already provides

### Tool window tabs (Scopecreep)

Registered in `Scopecreep/src/main/kotlin/com/scopecreep/ScopecreepToolWindowFactory.kt`:

1. **Ping** — existing, unchanged. Hits `GET /health` on the memory worker.
2. **Profiles** — existing, unchanged. Memory/flywheel profile browser.
3. **Schematic** (new) — picks a `.SchDoc` → `POST /schematic/parse` on port
   8000 → renders the Markdown summary via `MarkdownRenderer`. A "Use in
   Agent" button hands the markdown to the Agent tab as a hint.
4. **Agent** (new) — start/poll/resume/cancel a test session. Pastes or loads
   the structured schematic JSON that `POST /agent/sessions` expects. Renders
   the probe prompt during `PROBE_REQUIRED`, and a Markdown report on
   `COMPLETE`/`FAILED`.
5. **Waveform** (new) — receives the raw report JSON from the Agent tab and
   renders a per-probe measurement table (`v_min/max/mean/pp/rms`) with a
   small hand-rolled Swing sparkline cell.
6. **Chat** (new) — shells out to `codex exec <prompt>` and streams stdout.
   Respects `NEBIUS_API_KEY` / `OPENAI_API_KEY` from settings.
7. **Firmware** (new) — dashboard for `firmware_jobs` in Supabase. Insert
   (Generate), list, poll (every 2s), and PATCH `status='flash_requested'`
   (Flash button). No LangGraph logic — that lives in the sibling repo.

### HTTP client layer

`Scopecreep/src/main/kotlin/com/scopecreep/service/`:

- `AgentClient.kt` — typed calls for `/health`, `/schematic/parse`,
  `/schematic/parse.json`, `POST /agent/sessions`, `GET /agent/sessions/{id}`,
  `POST /agent/sessions/{id}/resume`, `GET /agent/sessions/{id}/report`,
  `DELETE /agent/sessions/{id}`. Uses a separate URL from `RunnerClient`
  (`agentUrl` built from `runnerHost:agentPort`, default `:8000`).
- `SessionPoller.kt` — 1 Hz poll loop on a pooled thread, deduplicates
  snapshots, dispatches updates on the EDT. Terminal states are `COMPLETE`,
  `FAILED`, `ERROR`.
- `FirmwareClient.kt` — Supabase PostgREST for `firmware_jobs`
  (create/get/list/PATCH-flash).
- `JsonFields.kt` — minimal regex-based JSON field extractor (string/int +
  object/array scoping). Good enough for the small shapes the backend
  returns; swap in Jackson if payloads grow.
- `RunnerClient.kt` — existing memory-layer client, unchanged.

### Sidecar (two processes, one venv)

`Scopecreep/src/main/kotlin/com/scopecreep/sidecar/SidecarManager.kt`:

- Extracts `/sidecar/worker.py` + its deps (memory worker) into
  `~/.scopecreep/sidecar/`.
- Extracts the bundled agent backend (vendored from `python/{api,agent,
  drivers,schdoc}` via the Gradle `bundleBenchyBackend` task) into
  `~/.scopecreep/sidecar/benchy/`, driven by `benchy-manifest.txt`.
- Creates `~/.scopecreep/venv/` and installs
  `resources/sidecar/requirements.txt` (unified superset).
- Launches **two** uvicorn processes:
  - `uvicorn worker:app` on `runnerPort` (default 8420) — memory/profile.
  - `uvicorn agent_worker:app` on `agentPort` (default 8000) — agent +
    hardware. `agent_worker.py` is a generated shim:
    `from api.server import app`.
- Passes env vars from settings (`SCOPECREEP_SUPABASE_URL`,
  `SCOPECREEP_SUPABASE_ANON_KEY`, `SCOPECREEP_NEBIUS_API_KEY` to the memory
  worker; `ANTHROPIC_API_KEY` to the agent worker).
- SIGTERM + 2s wait → SIGKILL on project close.

### Gradle bundling

`Scopecreep/build.gradle.kts`:

- `bundleBenchyBackend` task copies `python/{api,agent,drivers,schdoc}` into
  `build/generated/benchy-resources/sidecar/benchy/`, skipping `__pycache__`
  and `tests/` subtrees. Writes `benchy-manifest.txt` + `agent_worker.py`.
- The generated dir is added as a resource srcDir so its contents end up at
  `/sidecar/benchy/**` inside the plugin JAR.
- Source path is `../python` by default, overridable via
  `-Pbenchy.backend.path=/abs/to/python`.
- Task degrades gracefully when the source path is missing (emits an empty
  manifest + placeholder shim) so CI builds without jbhack still succeed.

### Settings

`Scopecreep/src/main/kotlin/com/scopecreep/settings/ScopecreepSettings.kt`
adds `agentPort: Int = 8000` and `anthropicApiKey: String`. The configurable
UI (`ScopecreepSettingsConfigurable.kt`) exposes both in the Sidecar group.
Round-trip test in `ScopecreepSettingsTest.kt`.

### Supabase schema

`Scopecreep/supabase/migrations/002_firmware_jobs.sql` defines the
`firmware_jobs` table:

```
id, goal, target, status (enum), architecture_spec jsonb,
files jsonb, logs jsonb, compile_output, flash_output, error,
author_id, created_at, updated_at
```

Status values: `queued | architecting | generating | stitching | compiling
| flash_requested | flashing | done | failed`.

RLS mirrors the author-centric pattern used by `profiles`.

---

## What the plugin depends on from the other agent

The plugin is already wired against these HTTP endpoints. The other agent's
job is to keep these shapes (or tell us what changed):

### `POST /schematic/parse` (existing)

Multipart upload of `.SchDoc`. Returns `text/plain` Markdown. Used by the
Schematic tab.

### `POST /schematic/parse.json` (added this branch, `python/api/server.py`)

Same input, returns the structured shape the agent session expects:

```json
{
  "board_name": "string",
  "understanding": "string",
  "probe_points": [
    {"label": "...", "net": "...", "expected_range": "...",
     "probe_type": "...", "designator": "...",
     "pin_name": "...", "pin_number": "..."}
  ]
}
```

Used by the Agent tab's "Load .SchDoc → JSON" button.

### `POST /agent/sessions`

Body:
```json
{"schematic": { board_name, understanding, probe_points, ... },
 "config":    { "scope_backend": "...", ... }}
```
Returns `{"session_id": "...", "status": "..."}`.

### `GET /agent/sessions/{id}`

Returns:
```json
{
  "session_id": "...",
  "status": "PLANNING|PROBE_REQUIRED|CAPTURING|EVALUATING|COMPLETE|FAILED",
  "progress": {"completed": int, "total": int},
  "current_probe": {
    "label","net","location_hint","probe_type","instructions"
  },
  "error": "string | absent"
}
```

The `current_probe` object is only present in `PROBE_REQUIRED`. Plugin polls
at 1 Hz.

### `POST /agent/sessions/{id}/resume`

No body. Plugin calls this after the human places the probe.

### `GET /agent/sessions/{id}/report`

Returns `202` while running. On terminal state:
```json
{
  "session_id","board_name","overall": "PASS|FAIL|PARTIAL",
  "results": [
    {"probe_point","net","verdict","expected_range",
     "measurements": {"v_min","v_max","v_mean","v_pp","v_rms"},
     "reasoning","tier"}
  ]
}
```

Plugin renders `overall` + the per-probe table in the Agent tab and the
Waveform tab.

### `DELETE /agent/sessions/{id}`

Cancel. Plugin calls this on user cancel or tab close.

### Tool schemas (informational — plugin doesn't touch these)

The agent's Claude tool loop exposes `psu_configure`, `scope_capture`,
`require_probe`, `record_result` (see `python/agent/tools.py`). The plugin
observes the effects indirectly through `/agent/sessions/{id}`.

---

## What remains open

### Plugin-side

- End-to-end smoke with real hardware (DPS-150 + Analog Discovery on the dev
  server). Needs the instruments connected to the machine running the plugin
  sidecar — the branch chose local-sidecar mode, so the plugin must run where
  the hardware is.
- Cold-start timing: the venv install is single-digit minutes on a fresh
  machine. We may want a progress indicator; currently users just see the
  log.
- No SSE — all session updates are poll-based. Good enough; revisit if
  the UI feels laggy under load.
- No streaming chat. `codex exec` is a one-shot subprocess per user turn;
  re-implement with a persistent `codex` REPL if conversation history is
  needed.
- No image upload (schematic images, waveform captures). Not blocking the
  MVP.

### Backend-side (expected from the other agent)

- Keep `/schematic/parse` and `/schematic/parse.json` stable.
- `POST /agent/sessions` must accept the schematic JSON shape defined above.
  If you add fields, plugin will ignore them — safe.
- If new session states appear, add them to the snapshot parser's
  `TERMINAL_STATES` set (`Scopecreep/.../SessionPoller.kt:61`). Non-terminal
  states just display by name.
- Tool-use events are currently invisible to the plugin. If you want per-
  measurement streaming (instead of only the final report), expose either:
    (a) `GET /agent/sessions/{id}/events` SSE, or
    (b) append a running `results` array to the snapshot payload.
  The plugin currently only pulls the report at terminal time.
- If the backend adds auth, it must be a static header the plugin can set
  via settings; no OAuth flow is plumbed.

### Firmware pipeline (LangGraph, sibling repo)

- Needs to be pointed at the same Supabase project the plugin is configured
  for, with the `002_firmware_jobs.sql` migration applied.
- Must write progress into the row the plugin inserts (keyed by
  `firmware_jobs.id`): update `status`, append to `logs`, populate `files`
  and `compile_output`.
- On `status='flash_requested'`, the Pi flash worker picks up the row and
  drives the ESP32. That integration is out of scope for this branch.

---

## Verification checklist (once the backend work lands)

1. From `jbhack`, bring up the backend directly to verify shape:
   ```
   cd python && python -m api.main
   curl -fsS http://127.0.0.1:8000/health
   curl -F file=@../Main.SchDoc http://127.0.0.1:8000/schematic/parse.json | jq .
   ```
2. From `Scopecreep`, build + run the plugin against bundled backend:
   ```
   JAVA_HOME=... ./gradlew buildPlugin verifyPlugin test
   JAVA_HOME=... ./gradlew runIde
   ```
3. In the sandbox IDE, run the sequence:
   Schematic → Parse → Use in Agent → Agent: Load .SchDoc → JSON →
   Start session → respond to probe prompt → Resume → inspect report +
   Waveform tab.
4. Firmware tab: requires Supabase migration applied + `supabaseAnonKey`
   set in Settings. Inserting a job should succeed; row stays `queued`
   until the out-of-repo pipeline advances it.

---

## Pointers

Plugin code of interest:
- `Scopecreep/src/main/kotlin/com/scopecreep/service/AgentClient.kt`
- `Scopecreep/src/main/kotlin/com/scopecreep/service/SessionPoller.kt`
- `Scopecreep/src/main/kotlin/com/scopecreep/ui/AgentSessionPanel.kt`
- `Scopecreep/src/main/kotlin/com/scopecreep/ui/WaveformPanel.kt`
- `Scopecreep/src/main/kotlin/com/scopecreep/sidecar/SidecarManager.kt`
- `Scopecreep/build.gradle.kts` (`bundleBenchyBackend`)
- `Scopecreep/supabase/migrations/002_firmware_jobs.sql`

Backend entry points:
- `python/api/server.py` (`/schematic/parse`, `/schematic/parse.json`,
  hardware endpoints, mounts the agent router)
- `python/agent/server.py` (`/agent/sessions/*`)
- `python/agent/runner.py`, `python/agent/tools.py` — the Claude tool loop
  the other agent is iterating on
- `python/schdoc/` — schematic parser

Plan file (for context):
- `/home/alex/.claude/plans/while-orchrestration-for-llm-quirky-mist.md`
