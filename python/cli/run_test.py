# python/cli/run_test.py
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from executor.drivers import Dps150Adapter, AnalogDiscoveryScopeAdapter
from executor.runner import run, SafetyError
from executor.prompts import confirm
from planner.openai_planner import generate_plan, PlannerError
from planner.models import TestPlan


def _print_plan(plan: TestPlan) -> None:
    print(f"\n=== Test Plan: {plan.board_name} ===")
    print(f"Summary: {plan.summary}\n")
    for tc in plan.test_cases:
        print(f"{tc.id}  {tc.title}")
        print(f"  PSU:   {tc.psu.voltage}V @ {tc.psu.current_limit}A on {tc.psu.rail_name}")
        print(f"  Probe: {tc.probe.scope_channel} on {tc.probe.label} ({tc.probe.location_hint})")
        print(f"  Expect: kind={tc.expected.kind} nominal={tc.expected.nominal} tol={tc.expected.tolerance}")
        print(f"  Why:    {tc.rationale}")
        print()


def _print_summary(report) -> None:
    print("\n=== Report ===")
    for r in report.results:
        val = "n/a" if r.measurement_value is None else f"{r.measurement_value:.4f}V"
        print(f"{r.test_case_id}  {r.title:30s}  {r.verdict:8s}  {val}  (expected {r.expected_range_str})")
    passed = sum(1 for r in report.results if r.verdict == "PASS")
    print(f"Overall: {report.overall} ({passed}/{len(report.results)})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_test")
    parser.add_argument("schdoc", help="Path to .SchDoc file")
    parser.add_argument("--report-dir", default="reports", help="Where to write JSON reports")
    parser.add_argument("--yes", action="store_true", help="Skip approval prompt")
    args = parser.parse_args(argv)

    load_dotenv()

    # Ensure python/ is on path for the parser import below when run as a module.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from schdoc.parser import parse

    try:
        summary = parse(args.schdoc)
    except Exception as exc:
        print(f"error: failed to parse {args.schdoc}: {exc}", file=sys.stderr)
        return 2

    try:
        plan = generate_plan(summary)
    except PlannerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    _print_plan(plan)

    if not args.yes:
        if not confirm("Approve and run?"):
            print("Aborted.")
            return 1

    psu = Dps150Adapter()
    scope = AnalogDiscoveryScopeAdapter()
    try:
        report = run(plan, psu=psu, scope=scope)
    except SafetyError as exc:
        print(f"SAFETY ABORT: {exc}", file=sys.stderr)
        return 4

    _print_summary(report)

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    out = report_dir / f"{ts}-run.json"
    out.write_text(report.model_dump_json(indent=2))
    print(f"Written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
