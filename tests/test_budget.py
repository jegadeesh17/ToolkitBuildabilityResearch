import pytest

from agent.budget import BudgetExceeded, BudgetGuard, estimate_cost, price_for, spent_from_log
from agent.observability import RunLogger


def seed(tmp_path, *costs):
    log = RunLogger(tmp_path / "l.jsonl")
    for c in costs:
        log.append({"kind": "llm", "cost_usd": c})
    log.append({"kind": "tool", "cost_usd": None})
    log.append({"kind": "llm_validation", "cost_usd": 99.0})  # not an LLM call; must be ignored
    return tmp_path / "l.jsonl"


def test_spent_sums_llm_lines_only(tmp_path):
    assert spent_from_log(seed(tmp_path, 0.1, 0.25)) == pytest.approx(0.35)


def test_missing_log_is_zero(tmp_path):
    assert spent_from_log(tmp_path / "none.jsonl") == 0


def test_call_that_would_exceed_is_refused(tmp_path):
    g = BudgetGuard(1.0, seed(tmp_path, 0.95))
    g.check(0.04)
    with pytest.raises(BudgetExceeded) as e:
        g.check(0.06)
    assert e.value.cap == 1.0 and e.value.spent == pytest.approx(0.95) and "1.00" in str(e.value)


def test_at_cap_refuses_even_free_calls(tmp_path):
    with pytest.raises(BudgetExceeded):
        BudgetGuard(1.0, seed(tmp_path, 1.0)).check(0.0)


def test_record_accumulates(tmp_path):
    g = BudgetGuard(1.0, seed(tmp_path))
    g.record(0.6)
    assert g.spent == pytest.approx(0.6)
    with pytest.raises(BudgetExceeded):
        g.check(0.5)


def test_free_models_cost_zero():
    assert price_for("qwen/qwen3.8-27b:free") == (0, 0) and price_for("openrouter/free") == (0, 0)
    assert estimate_cost("x/y:free", 10**6, 10**6) == 0


def test_known_model_price():
    assert estimate_cost("deepseek/deepseek-v4-flash", 1_000_000, 1_000_000) == pytest.approx(0.27)


def test_unknown_paid_model_uses_conservative_fallback():
    assert estimate_cost("new/model", 1_000_000, 0) == pytest.approx(3.0)
