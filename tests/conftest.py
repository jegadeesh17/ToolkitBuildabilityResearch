import socket

import pytest


@pytest.fixture(autouse=True)
def _offline(monkeypatch, request, tmp_path):
    """No keys, no real .env, no sockets: tests can never make a paid call."""
    for var in ("OPENROUTER_API_KEY", "COMPOSIO_API_KEY", "PASS1_MODEL", "VERIFY_MODEL",
                "JUDGE_MODEL", "BUDGET_CAP_USD", "MODEL_PRESET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TBR_DOTENV_PATH", str(tmp_path / "no.env"))
    if "browser" not in request.keywords:
        real_connect = socket.socket.connect

        def _guarded(sock, address, *args, **kwargs):
            # Loopback is allowed: the Windows asyncio event loop uses a local socketpair internally.
            host = address[0] if isinstance(address, tuple) else address
            if host in ("127.0.0.1", "::1", "localhost"):
                return real_connect(sock, address, *args, **kwargs)
            raise RuntimeError("network access attempted in an offline test")
        monkeypatch.setattr(socket.socket, "connect", _guarded)
    # Run log and raw payloads never land in the repo during tests.
    import agent.config as config
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "runs" / "run_log.jsonl")
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")


META = {"pass": 1, "run_id": "r", "model": "m", "prompt_version": "p1-abc", "updated_at": "t"}
EV = [{"url": "https://ex.com/docs", "quote": "The API uses OAuth 2.0 tokens"}]


@pytest.fixture
def make_row():
    from agent.schema import SCORED_FIELDS, AppResult

    def _make(**over) -> AppResult:
        d = dict(id=1, app="X", category="C", description="d", auth_methods=["oauth2"],
                 access_model="self_serve_free", api_type=["rest"], api_breadth="broad", existing_mcp="official",
                 verdict="buildable_now", blocker="none", api_pricing_tier="free", confidence=0.8,
                 evidence={f: EV for f in SCORED_FIELDS}, unknown_reason={}, flags=[], meta=META)
        d.update(over)
        return AppResult.model_validate(d)
    return _make


@pytest.fixture
def make_bundle():
    import hashlib

    from agent.schema import EvidenceBundle, Page

    def _make(pages) -> EvidenceBundle:
        out = []
        for p in pages:
            url, text = p[0], p[1]
            final = p[2] if len(p) > 2 else None
            out.append(Page(url=url, final_url=final, title="", text=text, http_status=200, fetched_at="t",
                            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(), error=None, thin=False))
        return EvidenceBundle(app_id=1, app="X", run_id="r", created_at="t", queries=[], pages=out)
    return _make


TEST_ENV = {"OPENROUTER_API_KEY": "sk-or-v1-testkey", "COMPOSIO_API_KEY": "ck_test", "PASS1_MODEL": "a/b",
            "VERIFY_MODEL": "c/d", "JUDGE_MODEL": "e/f"}


@pytest.fixture
def run_logger(tmp_path):
    from agent.observability import RunLogger
    return RunLogger(tmp_path / "l.jsonl")


@pytest.fixture
def tool_client(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from agent.config import load_settings
    from agent.observability import RunLogger
    from agent.tools import ToolClient
    monkeypatch.setattr("agent.tools._sleep", AsyncMock())
    settings = load_settings(env=TEST_ENV)

    def _make(fn):
        calls = []

        def executor(slug, args):
            calls.append((slug, args))
            return fn(slug, args)
        return ToolClient(settings, RunLogger(tmp_path / "tools.jsonl"), run_id="r", executor=executor,
                          min_interval_s=0), calls
    return _make


class FakeLLM:
    """Stands in for LLMClient.chat: scripted replies, optional budget accounting like the real client."""

    def __init__(self, responses=(), default=None, cost=0.001, budget=None):
        self.responses, self.default, self.cost, self.budget, self.calls = list(responses), default, cost, budget, 0

    async def chat(self, **kw):
        from agent.llm_client import LLMResponse
        if self.budget is not None:
            self.budget.check(self.cost)
        self.calls += 1
        text = self.responses.pop(0) if self.responses else self.default
        if text is None:
            raise AssertionError("FakeLLM ran out of scripted responses")
        if self.budget is not None:
            self.budget.record(self.cost)
        return LLMResponse(text, kw["model"], 10, 10, self.cost, False, 5, 0, "raw.json")


class FakeTools:
    """Stands in for ToolClient: `pages` maps url -> text; `hits` is returned for every search."""

    def __init__(self, pages=None, hits=()):
        self.pages, self.hits, self.calls = dict(pages or {}), list(hits), 0

    async def search(self, query, **kw):
        from agent.tools import SearchHit
        self.calls += 1
        return [SearchHit(u) for u in self.hits]

    async def fetch(self, url, **kw):
        import hashlib

        from agent.schema import Page
        from agent.tools import is_thin
        self.calls += 1
        if url not in self.pages:
            return Page(url=url, fetched_at="t", error="fetch_failed")
        text = self.pages[url]
        return Page(url=url, text=text, http_status=200, fetched_at="t", thin=is_thin(text),
                    sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())


@pytest.fixture
def fake_llm():
    return FakeLLM


@pytest.fixture
def fake_tools():
    return FakeTools
