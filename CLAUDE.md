# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Agentic hardware testbench with a JetBrains IDE plugin frontend. The system lets users drive hardware tests through an IDE, with an AI agent (OpenAI/Codex) interpreting schematics, images, and test results. Hardware control (power supply, oscilloscope) runs on a local Python backend; the IDE plugin communicates with that backend and with the OpenAI API.

**Monorepo layout:**
- Python — hardware control, instrument drivers, agent orchestration backend
- Kotlin — JetBrains IDE plugin, OpenAI API integration, UI

## Architecture

```mermaid
graph TD
    subgraph IDE ["JetBrains IDE (Kotlin Plugin)"]
        UI[Test UI / Chat Panel]
        Agent[Agent Orchestrator]
        OpenAI[OpenAI / Codex API]
    end

    subgraph Server ["Dev Server (Python Backend, via Tailscale)"]
        API[Backend API]
        PSU[Power Supply Driver]
        OSC[Oscilloscope Driver]
        Files[Schematic / Image Handler]
    end

    subgraph HW ["Physical Hardware"]
        PowerSupply[Serial Power Supply]
        Oscilloscope[USB Oscilloscope]
    end

    UI --> Agent
    Agent --> OpenAI
    Agent --> API
    API --> PSU --> PowerSupply
    API --> OSC --> Oscilloscope
    API --> Files
```

The plugin owns the AI conversation and sends structured commands to the Python backend, which executes them against real hardware. There is no hardware mocking — all testing requires physical instruments connected to the dev server.

### Test Session Data Flow

```mermaid
sequenceDiagram
    participant User
    participant Plugin as JetBrains Plugin
    participant OpenAI as OpenAI API
    participant Backend as Python Backend
    participant HW as Hardware

    User->>Plugin: Upload schematic / describe test
    Plugin->>OpenAI: Send context + image
    OpenAI-->>Plugin: Agent response (test plan / commands)
    Plugin->>Backend: Execute hardware commands
    Backend->>HW: Control PSU / read oscilloscope
    HW-->>Backend: Measurements
    Backend-->>Plugin: Results
    Plugin->>OpenAI: Feed results back to agent
    OpenAI-->>Plugin: Analysis / next step
    Plugin-->>User: Display results
```

## Dev Environment

Development happens on a remote server accessed via **Tailscale + SSH tunnel**. When running or testing anything, assume the developer is already SSH'd into the server — do not suggest local execution or browser-based UIs that require a local display. Prefer CLI output and terminal-friendly workflows.

## Git Workflow

- **Feature branches** off `main`: `feat/<short-description>`, `fix/<short-description>`
- PRs merged into `main` following standard review practices
- Commit messages: imperative mood, concise subject line

## Python Side

- Hardware interfaces are real — no simulators or mocks
- Instrument drivers (power supply, oscilloscope) should be kept modular and swappable
- Agent orchestration flow is user-defined per test session; don't hardcode sequences

## Kotlin / JetBrains Plugin

- Plugin communicates with the local Python backend (not cloud) for all hardware operations
- OpenAI/Codex API calls originate from the plugin side
- Keep IDE plugin logic decoupled from specific hardware details — hardware knowledge lives in Python
