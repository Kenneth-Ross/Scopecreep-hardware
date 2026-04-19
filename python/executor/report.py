# python/executor/report.py
from __future__ import annotations

from pydantic import BaseModel


class TestResult(BaseModel):
    test_case_id: str
    title: str
    verdict: str
    measurement_key: str
    measurement_value: float | None
    measurements: dict[str, float]
    expected_range_str: str
    error: str | None = None


class Report(BaseModel):
    board_name: str
    results: list[TestResult]

    @property
    def overall(self) -> str:
        verdicts = {r.verdict for r in self.results}
        if not verdicts:
            return "EMPTY"
        if verdicts == {"PASS"}:
            return "PASS"
        if "FAIL" in verdicts or "ERROR" in verdicts:
            return "FAIL"
        return "PARTIAL"
