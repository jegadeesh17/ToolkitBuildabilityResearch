"""Settings from `.env` / the environment. The only module that reads secrets.

Key values are held as `SecretStr` and never printed or logged; errors name the variable only.
Paths are module attributes read at call time (`config.RUN_LOG`), so tests can redirect them.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"
RAW_DIR = RESULTS_DIR / "raw"
RUN_LOG = RESULTS_DIR / "runs" / "run_log.jsonl"
VERIFICATION_DIR = ROOT / "verification"

DEFAULT_BUDGET_CAP_USD = 4.00
_PLACEHOLDER = "REPLACE_ME"


class ConfigError(Exception):
    def __init__(self, var: str, problem: str = "missing or invalid"):
        super().__init__(f"{var}: {problem}")
        self.var = var
        self.problem = problem


def config_error_message(err: ConfigError) -> str:
    return (f"Missing or invalid {err.var} ({err.problem}). "
            "Copy .env.example to .env and set it (values are never printed).")


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: SecretStr | None
    composio_api_key: SecretStr | None
    pass1_model: str
    verify_model: str
    judge_model: str
    budget_cap_usd: float
    model_preset: str


def secret_values(settings: Settings) -> list[str]:
    """Raw secret strings, for redaction only."""
    return [s.get_secret_value() for s in (settings.openrouter_api_key, settings.composio_api_key) if s]


def _source(env: Mapping[str, str] | None) -> dict[str, str]:
    if env is not None:
        return dict(env)
    path = Path(os.environ.get("TBR_DOTENV_PATH") or ROOT / ".env")
    values = {k: v for k, v in dotenv_values(path).items() if v is not None} if path.exists() else {}
    values.update({k: v for k, v in os.environ.items() if v})
    return values


def _required(values: Mapping[str, str], var: str) -> str:
    v = (values.get(var) or "").strip()
    if not v:
        raise ConfigError(var, "not set")
    if _PLACEHOLDER in v:
        raise ConfigError(var, "still the .env.example placeholder")
    return v


def load_settings(*, need_llm: bool = True, need_tools: bool = True, env: Mapping[str, str] | None = None) -> Settings:
    values = _source(env)
    or_key = SecretStr(_required(values, "OPENROUTER_API_KEY")) if need_llm else None
    cp_key = SecretStr(_required(values, "COMPOSIO_API_KEY")) if need_tools else None
    pass1 = _required(values, "PASS1_MODEL") if need_llm else (values.get("PASS1_MODEL") or "")
    raw_cap = (values.get("BUDGET_CAP_USD") or "").strip()
    try:
        cap = float(raw_cap) if raw_cap else DEFAULT_BUDGET_CAP_USD
    except ValueError:
        raise ConfigError("BUDGET_CAP_USD", "not a number") from None
    if cap < 0:
        raise ConfigError("BUDGET_CAP_USD", "negative")
    return Settings(
        openrouter_api_key=or_key,
        composio_api_key=cp_key,
        pass1_model=pass1,
        verify_model=(values.get("VERIFY_MODEL") or "").strip(),
        judge_model=(values.get("JUDGE_MODEL") or "").strip(),
        budget_cap_usd=cap,
        model_preset=(values.get("MODEL_PRESET") or "paid").strip(),
    )
