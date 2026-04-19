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

    Returns (verdict, reasoning). Kept `async` to match the existing agent
    runner contract; the underlying OpenAI call is synchronous.
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

    try:
        from _openai_usage import log_usage
        log_usage(resp, label="evaluator.tier2")
    except Exception:
        pass

    raw = resp.choices[0].message.content
    if not raw:
        raise Tier2Error("empty response")
    data = json.loads(raw)
    verdict = data.get("verdict", "FAIL")
    reasoning = data.get("reasoning", "")
    if verdict not in ("PASS", "FAIL"):
        raise Tier2Error(f"bad verdict: {verdict}")
    return verdict, reasoning
