import pytest
from evaluator.tier1 import evaluate_tier1, parse_expected_range


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
    b = parse_expected_range("0\u20130.5V")
    assert abs(b.lower - 0.0) < 1e-9
    assert abs(b.upper - 0.5) < 1e-9


def test_parse_unknown_raises():
    with pytest.raises(ValueError, match="Cannot parse"):
        parse_expected_range("something invalid")


# --- evaluate_tier1 ---

def test_pass_within_pct():
    assert evaluate_tier1({"v_mean": 3.31}, "3.3V ± 5%") == "PASS"


def test_fail_far_outside():
    assert evaluate_tier1({"v_mean": 2.5}, "3.3V ± 5%") == "FAIL"


def test_marginal_just_outside():
    assert evaluate_tier1({"v_mean": 3.5}, "3.3V ± 5%") == "MARGINAL"


def test_tier1_pass_at_boundary():
    assert evaluate_tier1({"v_mean": 3.135}, "3.3V ± 5%") == "PASS"


def test_tier1_marginal_just_outside():
    # 3.10 is just outside the 5% band but within 10%
    assert evaluate_tier1({"v_mean": 3.10}, "3.3V ± 5%") == "MARGINAL"


def test_gt_lower():
    assert evaluate_tier1({"v_mean": 3.0}, "> 2.5V") == "PASS"


def test_tier1_fail_greater_than():
    assert evaluate_tier1({"v_mean": 1.0}, "> 2.5V") == "FAIL"


def test_tier1_marginal_greater_than():
    # Just below the lower bound but within 10% of it
    assert evaluate_tier1({"v_mean": 2.35}, "> 2.5V") == "MARGINAL"


def test_tier1_pass_less_than():
    assert evaluate_tier1({"v_mean": 0.1}, "< 0.5V") == "PASS"


def test_tier1_fail_less_than():
    assert evaluate_tier1({"v_mean": 1.0}, "< 0.5V") == "FAIL"


def test_range_hyphen():
    assert evaluate_tier1({"v_mean": 0.3}, "0-0.5V") == "PASS"


def test_tier1_fail_range():
    assert evaluate_tier1({"v_mean": 1.5}, "0-0.5V") == "FAIL"


def test_tier1_marginal_above_upper():
    # v_mean exceeds the upper bound but stays within 2x tolerance
    assert evaluate_tier1({"v_mean": 3.47}, "3.3V ± 5%") == "MARGINAL"


def test_tier1_marginal_range():
    # v_mean above the range but within 2x tolerance
    assert evaluate_tier1({"v_mean": 0.74}, "0-0.5V") == "MARGINAL"


def test_missing_v_mean_raises():
    with pytest.raises(ValueError):
        evaluate_tier1({}, "3.3V ± 5%")


def test_tier1_raises_on_none_v_mean():
    with pytest.raises(ValueError, match="v_mean"):
        evaluate_tier1({"v_mean": None}, "3.3V ± 5%")


def test_unparseable_raises():
    with pytest.raises(ValueError):
        parse_expected_range("not a range")
