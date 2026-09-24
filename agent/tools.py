"""The ONLY module that calls Composio: web search and URL fetch via the no-auth search toolkit.

Slugs outside ALLOWED_SLUGS are refused before any network call. Every call is rate-limited,
retried with 1 s / 2 s / 4 s backoff on transient errors, and logged as one `kind: "tool"` line.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent.config import ConfigError, Settings, secret_values
from agent.grounding import canonical_url
from agent.observability import RunLogger, redact, utc_now
from agent.schema import Page

# Verified against the live toolkit listing on 2026-09-24 (docs/reference/README.md).
SEARCH_SLUG = "COMPOSIO_SEARCH_WEB"
SEARCH_FALLBACK_SLUG = "COMPOSIO_SEARCH_DUCK_DUCK_GO"
FETCH_SLUG = "COMPOSIO_SEARCH_FETCH_URL_CONTENT"
ALLOWED_SLUGS = frozenset({SEARCH_SLUG, SEARCH_FALLBACK_SLUG, FETCH_SLUG})
TOOLKIT_VERSION = "20260903_00"
BACKOFF_S = (1, 2, 4)
MAX_CHARS = 20_000

_sleep = asyncio.sleep  # patched in tests
_RETRYABLE = re.compile(r"429|rate.?limit|too many|timeout|timed out|\b5\d\d\b|temporar|connection|unavailable|reset",
                        re.IGNORECASE)
_JS_ONLY = re.compile(r"enable javascript|javascript is required|requires javascript", re.IGNORECASE)
_TEXT_KEYS = ("text", "content", "markdown", "body", "page_content", "raw_content")


class DisallowedToolError(Exception):
    pass


class ToolConfigError(ConfigError):
    """The Composio key is rejected (auth/permission): fix configuration, don't retry."""


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""


def is_thin(text: str) -> bool:
    t = (text or "").strip()
    return len(t) < 500 or bool(_JS_ONLY.search(t[:2000]))


def _walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def parse_search_hits(data: Any) -> list[SearchHit]:
    """Find result dicts carrying a url/link anywhere in the payload; keep order, drop duplicates."""
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for d in _walk(data):
        url = d.get("url") or d.get("link") or d.get("href")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        key = canonical_url(url)
        if key in seen:
            continue
        seen.add(key)
        title = d.get("title") or d.get("name") or ""
        snippet = d.get("snippet") or d.get("description") or d.get("body") or ""
        hits.append(SearchHit(url=url, title=str(title)[:300], snippet=str(snippet)[:500]))
    return hits


def _longest_text(d: dict) -> str:
    best = ""
    for k in _TEXT_KEYS:
        v = d.get(k)
        if isinstance(v, str) and len(v) > len(best):
            best = v
    return best


def parse_fetch_text(data: Any, url: str | None = None) -> tuple[str, str | None, int | None]:
    """(text, title, http_status) for `url` from a fetch payload; falls back to the longest text found."""
    want = canonical_url(url) if url else None
    best: tuple[str, str | None, int | None] = ("", None, None)
    for d in _walk(data):
        text = _longest_text(d)
        if not text:
            continue
        status = d.get("status_code") or d.get("http_status") or d.get("statusCode")
        status = status if isinstance(status, int) else None
        title = d.get("title") if isinstance(d.get("title"), str) else None
        candidate = (text, title, status)
        u = d.get("url") or d.get("id")
        if want and isinstance(u, str) and canonical_url(u) == want:
            return candidate
        if len(text) > len(best[0]):
            best = candidate
    if not best[0] and isinstance(data, str):
        return data, None, None
    return best


def _to_dict(resp: Any) -> dict:
    if hasattr(resp, "model_dump"):
        return resp.model_dump()
    if isinstance(resp, dict):
        return resp
    return {"successful": False, "error": f"unexpected response type {type(resp).__name__}"}


class ToolClient:
    def __init__(self, settings: Settings, logger: RunLogger, *, run_id: str,
                 executor: Callable[[str, dict], Any] | None = None, min_interval_s: float = 0.6):
        self.logger = logger
        self.run_id = run_id
        self._secrets = secret_values(settings)
        self._executor = executor or self._default_executor(settings)
        self._min_interval = min_interval_s
        self._last = 0.0
        self._lock = asyncio.Lock()

    @staticmethod
    def _default_executor(settings: Settings) -> Callable[[str, dict], Any]:
        if settings.composio_api_key is None:
            raise ConfigError("COMPOSIO_API_KEY", "not set")
        key = settings.composio_api_key.get_secret_value()
        state: dict[str, Any] = {}

        def run(slug: str, args: dict) -> Any:
            if "client" not in state:
                from composio import Composio  # lazy: tests never load the SDK
                for name in ("composio", "composio_client", "httpx"):
                    logging.getLogger(name).setLevel(logging.WARNING)
                state["client"] = Composio(api_key=key)
            return state["client"].tools.execute(slug, args, user_id="default", version=TOOLKIT_VERSION)
        return run

    async def _throttle(self) -> None:
        async with self._lock:
            wait = self._last + self._min_interval - time.monotonic()
            if wait > 0:
                await _sleep(wait)
            self._last = time.monotonic()

    async def _execute(self, slug: str, args: dict, *, app_id: int | None, stage: str,
                       url: str | None = None) -> dict:
        if slug not in ALLOWED_SLUGS:
            raise DisallowedToolError(f"tool slug not allowed: {slug}")
        started = time.monotonic()
        retries = 0
        error: str | None = None
        result: dict = {}
        for attempt in range(len(BACKOFF_S) + 1):
            await self._throttle()
            try:
                result = _to_dict(await asyncio.to_thread(self._executor, slug, args))
                error = None if result.get("successful", True) else str(result.get("error") or "unsuccessful")
            except Exception as e:  # SDK raises typed HTTP errors; classify, never leak the key
                name = type(e).__name__
                msg = redact(f"{name}: {e}", self._secrets)[:300]
                if "PermissionDenied" in name or "Authentication" in name or re.search(r"\b40[13]\b", msg):
                    self._log(slug, app_id, stage, url, started, retries, "error", msg, None)
                    raise ToolConfigError("COMPOSIO_API_KEY", f"rejected by Composio ({name})") from None
                result, error = {}, msg
            if error is None:
                break
            if attempt < len(BACKOFF_S) and _RETRYABLE.search(error):
                retries += 1
                await _sleep(BACKOFF_S[attempt])
                continue
            break
        self._log(slug, app_id, stage, url, started, retries, "ok" if error is None else "error", error,
                  200 if error is None else None)
        return result if error is None else {"successful": False, "error": error}

    def _log(self, slug, app_id, stage, url, started, retries, status, error, http_status) -> None:
        self.logger.append({"kind": "tool", "run_id": self.run_id, "app_id": app_id, "stage": stage, "tool": slug,
                            "url": url, "http_status": http_status, "retries": retries, "status": status,
                            "error": redact(error, self._secrets) if error else None, "cost_usd": 0.0,
                            "latency_ms": int((time.monotonic() - started) * 1000)})

    async def search(self, query: str, *, app_id: int | None, stage: str, k: int = 3,
                     fallback: bool = True) -> list[SearchHit]:
        for slug in (SEARCH_SLUG, SEARCH_FALLBACK_SLUG) if fallback else (SEARCH_SLUG,):
            res = await self._execute(slug, {"query": query}, app_id=app_id, stage=stage, url=query)
            hits = parse_search_hits(res.get("data")) if res.get("successful", True) else []
            if hits:
                return hits[:k]
        return []

    async def fetch(self, url: str, *, app_id: int | None, stage: str, max_chars: int = MAX_CHARS) -> Page:
        res = await self._execute(FETCH_SLUG, {"urls": [url], "text": True, "max_characters": max_chars},
                                  app_id=app_id, stage=stage, url=url)
        if not res.get("successful", True):
            return Page(url=url, fetched_at=utc_now(), error="fetch_failed")
        text, title, status = parse_fetch_text(res.get("data"), url)
        text = text[:max_chars]
        if not text.strip():
            return Page(url=url, fetched_at=utc_now(), error="fetch_failed", http_status=status)
        return Page(url=url, title=title or "", text=text, http_status=status or 200, fetched_at=utc_now(),
                    sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(), thin=is_thin(text))
