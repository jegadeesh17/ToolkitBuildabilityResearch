"""Budget guard backed by the run log: refuse any LLM call that would start past BUDGET_CAP_USD."""
from __future__ import annotations

import threading
from pathlib import Path

from agent.observability import read_log

# $ per 1M tokens (input, output); OpenRouter catalogue 2026-09-24 (docs/reference/README.md).
PRICES_PER_M: dict[str, tuple[float, float]] = {
    "deepseek/deepseek-v4-flash": (0.09, 0.18),
    "openai/gpt-5.6-luna": (0.20, 1.20),
    "qwen/qwen3.7-plus": (0.32, 1.28),
    "deepseek/deepseek-v4-pro": (0.94, 1.88),
    "anthropic/claude-sonnet-5": (2.00, 10.00),
}
FALLBACK_PRICE = (3.0, 15.0)  # conservative for unknown paid models: errs toward stopping early


def price_for(model: str) -> tuple[float, float]:
    if model.endswith(":free") or model == "openrouter/free":
        return (0.0, 0.0)
    return PRICES_PER_M.get(model, FALLBACK_PRICE)


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    p_in, p_out = price_for(model)
    return (tokens_in * p_in + tokens_out * p_out) / 1_000_000


def spent_from_log(path: Path | None = None) -> float:
    return sum(float(r.get("cost_usd") or 0) for r in read_log(path) if r.get("kind") == "llm")


class BudgetExceeded(Exception):
    def __init__(self, spent: float, cap: float, estimate: float):
        super().__init__(f"budget cap ${cap:.2f} reached: spent ${spent:.4f}, next call est. ${estimate:.4f}")
        self.spent, self.cap, self.estimate = spent, cap, estimate


class BudgetGuard:
    def __init__(self, cap: float, log_path: Path | None = None):
        self.cap = cap
        self.spent = spent_from_log(log_path)
        self._lock = threading.Lock()

    def check(self, estimate: float = 0.0) -> None:
        with self._lock:
            if self.spent >= self.cap or self.spent + estimate > self.cap:
                raise BudgetExceeded(self.spent, self.cap, estimate)

    def record(self, cost: float) -> None:
        with self._lock:
            self.spent += cost or 0.0
