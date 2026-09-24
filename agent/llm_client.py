"""The ONLY module that calls OpenRouter.

Every `chat()` call: budget check before each attempt, `temperature=0`, cost from `usage.cost`
(fallback: token estimate), retries on 429/5xx/transport errors, raw request/response saved under
results/raw/, and exactly one `kind: "llm"` line in the run log. Keys never reach logs or disk.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from agent.budget import BudgetGuard, estimate_cost
from agent.config import ConfigError, Settings, secret_values
from agent.observability import RunLogger, redact, save_raw

BASE_URL = "https://openrouter.ai/api/v1"
FORMAT_LEVELS = ("json_schema", "json_object", "none")
_sleep = asyncio.sleep  # patched in tests
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMError(Exception):
    """kind: "http" (non-retryable HTTP error), "transient" (retries exhausted), "empty_content" (no text)."""

    def __init__(self, message: str, status: int | None = None, kind: str = "http"):
        super().__init__(message)
        self.status = status
        self.kind = kind


class _Retryable(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class LLMResponse:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    cost_estimated: bool
    latency_ms: int
    retries: int
    raw_path: str


def parse_json_loose(text: str) -> dict:
    """Parse a JSON object from model output that may carry fences or chatter."""
    t = _FENCE.sub("", (text or "").strip())
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object found in model output")
    try:
        obj = json.loads(t[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e.msg}") from None
    if not isinstance(obj, dict):
        raise ValueError("model output is not a JSON object")
    return obj


def _is_format_error(resp: httpx.Response) -> bool:
    body = resp.text.lower()
    return any(k in body for k in ("response_format", "json_schema", "structured", "json mode"))


def _error_message(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err)[:300]
    except (ValueError, AttributeError):
        pass
    return resp.text[:300]


class LLMClient:
    def __init__(self, settings: Settings, logger: RunLogger, budget: BudgetGuard, *, run_id: str,
                 transport: httpx.AsyncBaseTransport | None = None, raw_dir: Path | None = None):
        if settings.openrouter_api_key is None:
            raise ConfigError("OPENROUTER_API_KEY", "not set")
        self.logger = logger
        self.budget = budget
        self.run_id = run_id
        self.raw_dir = raw_dir
        self._secrets = secret_values(settings)
        self._format_level: dict[str, int] = {}
        self._counter = itertools.count(1)
        self._client = httpx.AsyncClient(
            base_url=BASE_URL, timeout=httpx.Timeout(180.0, connect=20.0), transport=transport,
            headers={"Authorization": f"Bearer {settings.openrouter_api_key.get_secret_value()}",
                     "X-Title": "toolkit-buildability-research"})

    async def aclose(self) -> None:
        await self._client.aclose()

    def _body(self, messages: list[dict], model: str, max_tokens: int, json_schema: dict | None, level: int,
              reasoning: dict | None = None) -> dict:
        body: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens,
                                "usage": {"include": True}}
        if reasoning is not None:
            body["reasoning"] = reasoning  # OpenRouter unified reasoning control, e.g. {"effort": "low"}
        if json_schema is not None and level == 0:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "extraction", "strict": False, "schema": json_schema}}
        elif json_schema is not None and level == 1:
            body["response_format"] = {"type": "json_object"}
        return body

    async def chat(self, *, messages: list[dict], model: str, stage: str, app_id: int | None, prompt_version: str,
                   json_schema: dict | None = None, max_tokens: int = 4000,
                   reasoning: dict | None = None) -> LLMResponse:
        approx_in = len(json.dumps(messages, ensure_ascii=False)) // 4
        estimate = estimate_cost(model, approx_in, max_tokens)
        notes: list[str] = []
        attempts = 0
        body: dict = {}
        data: dict | None = None
        started = time.monotonic()
        call_no = next(self._counter)
        raw_name = f"llm_{stage}_{app_id}_{call_no}.json"

        def log(status: str, error: str | None, **extra) -> None:
            self.logger.append({
                "kind": "llm", "run_id": self.run_id, "app_id": app_id, "stage": stage, "model": model,
                "prompt_version": prompt_version, "latency_ms": int((time.monotonic() - started) * 1000),
                "retries": max(attempts - 1, 0), "status": status,
                "error": "; ".join([*notes, error] if error else notes) or None, **extra})

        try:
            async for attempt in AsyncRetrying(stop=stop_after_attempt(4), sleep=_sleep, reraise=True,
                                               wait=wait_exponential(multiplier=1, min=1, max=8),
                                               retry=retry_if_exception_type(_Retryable)):
                with attempt:
                    attempts += 1
                    self.budget.check(estimate)
                    level = self._format_level.get(model, 0)
                    while True:
                        body = self._body(messages, model, max_tokens, json_schema, level, reasoning)
                        try:
                            resp = await self._client.post("/chat/completions", json=body)
                        except httpx.TransportError as e:
                            raise _Retryable(f"transport: {type(e).__name__}") from None
                        if resp.status_code == 400 and json_schema is not None and level < 2 and _is_format_error(resp):
                            level += 1
                            self._format_level[model] = level
                            notes.append(f"format_downgraded:{FORMAT_LEVELS[level]}")
                            continue
                        break
                    if resp.status_code == 429 or resp.status_code >= 500:
                        raise _Retryable(f"http_{resp.status_code}: {_error_message(resp)}", resp.status_code)
                    if resp.status_code >= 400:
                        raise LLMError(f"http_{resp.status_code}: {_error_message(resp)}", resp.status_code)
                    try:
                        data = resp.json()
                    except ValueError:
                        raise _Retryable("non-JSON response body") from None
                    if isinstance(data, dict) and data.get("error"):
                        code = data["error"].get("code") if isinstance(data["error"], dict) else None
                        raise _Retryable(f"provider error: {str(data['error'])[:300]}",
                                         code if isinstance(code, int) else None)
        except _Retryable as e:
            log("error", redact(str(e), self._secrets))
            raise LLMError(str(e), e.status, kind="transient") from None
        except LLMError as e:
            log("error", redact(str(e), self._secrets))
            raise

        usage = (data or {}).get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens") or 0)
        tokens_out = int(usage.get("completion_tokens") or 0)
        cost = usage.get("cost")
        cost_estimated = cost is None
        cost = estimate_cost(model, tokens_in, tokens_out) if cost is None else float(cost)
        self.budget.record(cost)
        raw_path = save_raw(self.run_id, raw_name, {"request": body, "response": data}, self.raw_dir, self._secrets)
        choices = (data or {}).get("choices") or [{}]
        text = ((choices[0] or {}).get("message") or {}).get("content")
        common = {"tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost,
                  "cost_estimated": cost_estimated, "raw_path": raw_path}
        if not isinstance(text, str) or not text.strip():
            log("error", "empty_content", **common)
            raise LLMError("empty_content", kind="empty_content")
        log("ok", None, **common)
        return LLMResponse(text=text, model=model, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost,
                           cost_estimated=cost_estimated, latency_ms=int((time.monotonic() - started) * 1000),
                           retries=attempts - 1, raw_path=raw_path)

    async def key_status(self) -> dict:
        """GET /key: numeric/boolean limit fields only (never labels or key material)."""
        resp = await self._client.get("/key")
        if resp.status_code >= 400:
            raise LLMError(f"http_{resp.status_code}: {_error_message(resp)}", resp.status_code)
        data = resp.json().get("data") or {}
        return {k: v for k, v in data.items() if isinstance(v, (int, float, bool)) or v is None}
