"""Lightweight OpenAI usage + cost tracking.

Call `log_usage(response, label=...)` after any OpenAI response. Records tokens
to `reports/usage.jsonl` (gitignored) and prints a one-line summary.

Cost estimate uses env-var rates (per 1M tokens):
    OPENAI_INPUT_COST_PER_1M   (default: 0.25   — gpt-5-mini input)
    OPENAI_OUTPUT_COST_PER_1M  (default: 2.00   — gpt-5-mini output)

Rates are approximate; override in .env to match OpenAI's current pricing.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _rates() -> tuple[float, float]:
    inp = float(os.environ.get("OPENAI_INPUT_COST_PER_1M", "0.25"))
    out = float(os.environ.get("OPENAI_OUTPUT_COST_PER_1M", "2.00"))
    return inp, out


def _usage_file() -> Path:
    root = Path(os.environ.get("REPORTS_DIR", "reports"))
    root.mkdir(parents=True, exist_ok=True)
    return root / "usage.jsonl"


def log_usage(response: Any, label: str = "openai") -> dict:
    """Extract usage from an OpenAI response, log and print, return dict."""
    try:
        u = response.usage
        prompt = int(getattr(u, "prompt_tokens", 0) or 0)
        completion = int(getattr(u, "completion_tokens", 0) or 0)
        total = int(getattr(u, "total_tokens", prompt + completion) or 0)
    except Exception:
        prompt = completion = total = 0

    inp_rate, out_rate = _rates()
    cost = (prompt / 1_000_000) * inp_rate + (completion / 1_000_000) * out_rate

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "model": os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "est_cost_usd": round(cost, 6),
    }

    try:
        with _usage_file().open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass

    sys.stderr.write(
        f"[usage] {label}: {prompt} in + {completion} out = {total} tok  "
        f"~${cost:.4f}\n"
    )
    sys.stderr.flush()
    return record


def session_totals() -> dict:
    """Sum all usage in the current reports/usage.jsonl. Returns totals + cost."""
    path = _usage_file()
    if not path.exists():
        return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                "total_tokens": 0, "est_cost_usd": 0.0}

    tp = tc = tt = 0
    cost = 0.0
    calls = 0
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            calls += 1
            tp += r.get("prompt_tokens", 0)
            tc += r.get("completion_tokens", 0)
            tt += r.get("total_tokens", 0)
            cost += r.get("est_cost_usd", 0.0)
    return {
        "calls": calls,
        "prompt_tokens": tp,
        "completion_tokens": tc,
        "total_tokens": tt,
        "est_cost_usd": round(cost, 4),
    }
