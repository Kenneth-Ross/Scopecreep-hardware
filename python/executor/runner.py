# python/executor/runner.py
from __future__ import annotations

import os
import sys
from typing import TextIO

from planner.models import TestCase, TestPlan, expected_range_to_legacy
from evaluator.tier1 import evaluate_tier1

from .drivers import PsuDriver, ScopeDriver
from .prompts import wait_enter
from .report import Report, TestResult


class SafetyError(RuntimeError):
    """Raised when a test case violates MAX_VOLTAGE / MAX_CURRENT."""


def _safety_check(tc: TestCase) -> None:
    max_v = float(os.environ.get("MAX_VOLTAGE", "5.0"))
    max_i = float(os.environ.get("MAX_CURRENT", "1.0"))
    if tc.psu.voltage > max_v:
        raise SafetyError(
            f"{tc.id}: PSU voltage {tc.psu.voltage}V exceeds MAX_VOLTAGE {max_v}V"
        )
    if tc.psu.current_limit > max_i:
        raise SafetyError(
            f"{tc.id}: current limit {tc.psu.current_limit}A exceeds MAX_CURRENT {max_i}A"
        )


def _print_header(tc: TestCase) -> None:
    sys.stdout.write(f"\n── {tc.id}: {tc.title} ──\n{tc.rationale}\n")
    sys.stdout.flush()


def _print_result(tc: TestCase, value: float | None, verdict: str) -> None:
    if value is None:
        sys.stdout.write(f"→ (no reading)  [{verdict}]\n")
    else:
        sys.stdout.write(f"→ {value:.4f}V  [{verdict}]\n")
    sys.stdout.flush()


def run(
    plan: TestPlan,
    psu: PsuDriver,
    scope: ScopeDriver,
    stdin: TextIO | None = None,
) -> Report:
    results: list[TestResult] = []
    try:
        for tc in plan.test_cases:
            _safety_check(tc)
            _print_header(tc)

            psu.set(tc.psu.voltage, tc.psu.current_limit)
            wait_enter(
                f"PSU set to {tc.psu.voltage}V @ {tc.psu.current_limit}A on "
                f"{tc.psu.rail_name}. Press Enter to energize.",
                stdin=stdin,
            )
            psu.output_on()

            wait_enter(tc.probe.user_instructions + "  Press Enter to capture.", stdin=stdin)

            expected_str = expected_range_to_legacy(tc.expected)
            try:
                meas = scope.capture(channel=tc.probe.scope_channel)
                value = float(meas[tc.measurement])
                # tier1 only reads v_mean; wrap the selected measurement so
                # v_pp / v_rms test cases reuse the same evaluator.
                verdict = evaluate_tier1({"v_mean": value}, expected_str)
                err = None
            except Exception as exc:
                meas = {}
                value = None
                verdict = "ERROR"
                err = str(exc)

            _print_result(tc, value, verdict)
            results.append(TestResult(
                test_case_id=tc.id,
                title=tc.title,
                verdict=verdict,
                measurement_key=tc.measurement,
                measurement_value=value,
                measurements=meas,
                expected_range_str=expected_str,
                error=err,
            ))
    finally:
        try:
            psu.output_off()
        finally:
            try:
                psu.close()
            finally:
                scope.close()

    return Report(board_name=plan.board_name, results=results)
