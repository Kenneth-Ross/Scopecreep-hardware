import pytest
from agent.evaluator import parse_expected_range, evaluate_tier1
from unittest.mock import patch, AsyncMock


# --- parse_expected_range ---

def test_parse_pct_tolerance():
    b = parse_expected_range("3.3V ± 5%")
    assert abs(b.lower - 3.135) < 1e-9
    assert abs(b.upper - 3.465) < 1e-9


def test_parse_mv_tolerance():
    b = parse_expected_range("3.3V ± 165mV")
    assert abs(b.lower - 3.135) < 1e-9
    assert abs(b.upper - 3.465) < 1e-9


def test_parse_volt_tolerance():
    b = parse_expected_range("3.3V ± 0.165V")
    assert abs(b.lower - 3.135) < 1e-9
    assert abs(b.upper - 3.465) < 1e-9


def test_parse_greater_than():
    b = parse_expected_range("> 2.5V")
    assert abs(b.lower - 2.5) < 1e-9
    assert b.upper is None


def test_parse_less_than():
    b = parse_expected_range("< 0.5V")
    assert b.lower is None
    assert abs(b.upper - 0.5) < 1e-9


def test_parse_range_hyphen():
    b = parse_expected_range("0-0.5V")
    assert abs(b.lower - 0.0) < 1e-9
    assert abs(b.upper - 0.5) < 1e-9


def test_parse_range_endash():
    b = parse_expected_range("0–0.5V")
    assert abs(b.lower - 0.0) < 1e-9
    assert abs(b.upper - 0.5) < 1e-9


def test_parse_unknown_raises():
    with pytest.raises(ValueError, match="Cannot parse"):
        parse_expected_range("something invalid")


# --- evaluate_tier1 ---

def test_tier1_pass_symmetric():
    verdict = evaluate_tier1({"v_mean": 3.31}, "3.3V ± 5%")
    assert verdict == "PASS"


def test_tier1_pass_at_boundary():
    verdict = evaluate_tier1({"v_mean": 3.135}, "3.3V ± 5%")
    assert verdict == "PASS"


def test_tier1_marginal_just_outside():
    # 3.10 is just outside the 5% band but within 10%
    verdict = evaluate_tier1({"v_mean": 3.10}, "3.3V ± 5%")
    assert verdict == "MARGINAL"


def test_tier1_fail_far_out():
    # Way outside even 2x tolerance
    verdict = evaluate_tier1({"v_mean": 2.5}, "3.3V ± 5%")
    assert verdict == "FAIL"


def test_tier1_pass_greater_than():
    verdict = evaluate_tier1({"v_mean": 3.0}, "> 2.5V")
    assert verdict == "PASS"


def test_tier1_fail_greater_than():
    verdict = evaluate_tier1({"v_mean": 1.0}, "> 2.5V")
    assert verdict == "FAIL"


def test_tier1_marginal_greater_than():
    # Just below the lower bound but within 10% of it
    verdict = evaluate_tier1({"v_mean": 2.35}, "> 2.5V")
    assert verdict == "MARGINAL"


def test_tier1_pass_less_than():
    verdict = evaluate_tier1({"v_mean": 0.1}, "< 0.5V")
    assert verdict == "PASS"


def test_tier1_fail_less_than():
    verdict = evaluate_tier1({"v_mean": 1.0}, "< 0.5V")
    assert verdict == "FAIL"


def test_tier1_pass_range():
    verdict = evaluate_tier1({"v_mean": 0.25}, "0-0.5V")
    assert verdict == "PASS"


def test_tier1_fail_range():
    verdict = evaluate_tier1({"v_mean": 1.5}, "0-0.5V")
    assert verdict == "FAIL"


def test_tier1_marginal_above_upper():
    # v_mean exceeds the upper bound but stays within 2x tolerance
    verdict = evaluate_tier1({"v_mean": 3.47}, "3.3V ± 5%")
    assert verdict == "MARGINAL"


def test_tier1_marginal_range():
    # v_mean above the range but within 2x tolerance
    verdict = evaluate_tier1({"v_mean": 0.74}, "0-0.5V")
    assert verdict == "MARGINAL"


def test_tier1_raises_on_missing_v_mean():
    with pytest.raises(ValueError, match="v_mean"):
        evaluate_tier1({}, "3.3V ± 5%")


def test_tier1_raises_on_none_v_mean():
    with pytest.raises(ValueError, match="v_mean"):
        evaluate_tier1({"v_mean": None}, "3.3V ± 5%")


# --- evaluate_tier2 ---

def _mock_proc(json_response: str):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(json_response.encode(), b""))
    return proc


@pytest.mark.asyncio
async def test_evaluate_tier2_returns_pass():
    from agent.evaluator import evaluate_tier2
    with patch("asyncio.create_subprocess_exec", return_value=_mock_proc('{"verdict": "PASS", "reasoning": "Acceptable ripple."}')):
        verdict, reasoning = await evaluate_tier2(
            measurements={"v_mean": 3.20, "v_pp": 0.12},
            expected_range="3.3V ± 5%",
            probe_point={"label": "TP1", "net": "VCC_3V3"},
            board_understanding="LDO rail for MCU.",
        )
    assert verdict == "PASS"
    assert "ripple" in reasoning.lower()


@pytest.mark.asyncio
async def test_evaluate_tier2_returns_fail():
    from agent.evaluator import evaluate_tier2
    with patch("asyncio.create_subprocess_exec", return_value=_mock_proc('{"verdict": "FAIL", "reasoning": "Voltage sag indicates overload."}')):
        verdict, reasoning = await evaluate_tier2(
            measurements={"v_mean": 3.10, "v_pp": 0.30},
            expected_range="3.3V ± 5%",
            probe_point={"label": "TP1", "net": "VCC_3V3"},
            board_understanding="LDO rail for MCU.",
        )
    assert verdict == "FAIL"
