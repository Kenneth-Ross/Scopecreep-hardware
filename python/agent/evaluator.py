from __future__ import annotations
import re
import json
import anthropic
from dataclasses import dataclass
from typing import Any
from . import config


@dataclass
class _Bounds:
    lower: float | None
    upper: float | None
    tolerance: float   # half-band size for MARGINAL detection


def parse_expected_range(s: str) -> _Bounds:
    """Parse an expected_range string into numeric bounds.

    Supported patterns:
        "3.3V ± 5%"      symmetric percentage
        "3.3V ± 165mV"   symmetric millivolt
        "3.3V ± 0.165V"  symmetric volt
        "> 2.5V"         one-sided lower bound
        "< 0.5V"         one-sided upper bound
        "0-0.5V"         absolute range (hyphen or en-dash)
    """
    s = s.strip()

    m = re.match(r'([\d.]+)\s*[Vv]\s*[±]\s*([\d.]+)\s*%', s)
    if m:
        center, pct = float(m.group(1)), float(m.group(2)) / 100
        tol = center * pct
        return _Bounds(center - tol, center + tol, tol)

    m = re.match(r'([\d.]+)\s*[Vv]\s*[±]\s*([\d.]+)\s*m[Vv]', s)
    if m:
        center, tol = float(m.group(1)), float(m.group(2)) / 1000
        return _Bounds(center - tol, center + tol, tol)

    m = re.match(r'([\d.]+)\s*[Vv]\s*[±]\s*([\d.]+)\s*[Vv]', s)
    if m:
        center, tol = float(m.group(1)), float(m.group(2))
        return _Bounds(center - tol, center + tol, tol)

    m = re.match(r'>\s*([\d.]+)\s*[Vv]', s)
    if m:
        lo = float(m.group(1))
        return _Bounds(lo, None, lo * 0.1)

    m = re.match(r'<\s*([\d.]+)\s*[Vv]', s)
    if m:
        hi = float(m.group(1))
        return _Bounds(None, hi, hi * 0.1)

    m = re.match(r'([\d.]+)\s*[-\u2013]\s*([\d.]+)\s*[Vv]', s)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return _Bounds(lo, hi, (hi - lo) / 2)

    raise ValueError(f"Cannot parse expected_range: {s!r}")


def evaluate_tier1(measurements: dict[str, Any], expected_range: str) -> str:
    """Return 'PASS', 'FAIL', or 'MARGINAL' based on v_mean vs expected_range.

    PASS     — v_mean is within the specified bounds.
    FAIL     — v_mean is more than 2x the tolerance outside the bounds.
    MARGINAL — v_mean is between 1x and 2x the tolerance outside the bounds.
    """
    raw = measurements.get("v_mean")
    if raw is None:
        raise ValueError("measurements must contain a numeric 'v_mean' key")
    v_mean = float(raw)
    b = parse_expected_range(expected_range)

    in_range = True
    if b.lower is not None and v_mean < b.lower:
        in_range = False
    if b.upper is not None and v_mean > b.upper:
        in_range = False

    if in_range:
        return "PASS"

    tol = b.tolerance
    if b.lower is not None and b.upper is not None:
        center = (b.lower + b.upper) / 2
        if abs(v_mean - center) <= 2 * tol:
            return "MARGINAL"
    elif b.lower is not None:
        if v_mean >= b.lower - tol:
            return "MARGINAL"
    elif b.upper is not None:
        if v_mean <= b.upper + tol:
            return "MARGINAL"

    return "FAIL"


async def evaluate_tier2(
    measurements: dict[str, Any],
    expected_range: str,
    probe_point: dict[str, Any],
    board_understanding: str,
) -> tuple[str, str]:
    """Call Claude to interpret a marginal measurement.

    Returns (verdict, reasoning) where verdict is 'PASS' or 'FAIL'.
    Only called when tier-1 returns MARGINAL.
    """
    prompt = (
        "You are evaluating a hardware measurement. Respond with ONLY valid JSON, no markdown.\n\n"
        f"Probe point: {json.dumps(probe_point)}\n"
        f"Expected range: {expected_range}\n"
        f"Measurements: {json.dumps(measurements)}\n"
        f"Board context: {board_understanding}\n\n"
        'Respond with exactly: {"verdict": "PASS" or "FAIL", "reasoning": "one sentence"}'
    )
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=config.AGENT_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    result = json.loads(response.content[0].text)
    return result["verdict"], result["reasoning"]
