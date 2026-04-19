# python/agent/evaluator.py
# Tier-1 logic lives in python/evaluator/tier1.py so executor/ can share it.
# Tier-2 is a placeholder until Task 9 ports it to OpenAI.
from __future__ import annotations

from evaluator.tier1 import evaluate_tier1, parse_expected_range
from .evaluator_tier2 import evaluate_tier2

__all__ = ["evaluate_tier1", "parse_expected_range", "evaluate_tier2"]
