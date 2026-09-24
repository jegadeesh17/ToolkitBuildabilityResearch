import json
from unittest.mock import AsyncMock

import httpx
import pytest

from agent.budget import BudgetExceeded, BudgetGuard
from agent.config import load_settings
from agent.llm_client import LLMClient, LLMError, parse_json_loose
from agent.observability import RunLogger, read_log

ENV = {"OPENROUTER_API_KEY": "sk-or-v1-SECRETSECRET", "COMPOSIO_API_KEY": "ck", "PASS1_MODEL": "a/b",
       "VERIFY_MODEL": "c/d", "JUDGE_MODEL": "e/f"}
OK = {"choices": [{"message": {"content": '{"x": 1}'}}],
      "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.0012}}


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    monkeypatch.setattr("agent.llm_client._sleep", AsyncMock())


def client(tmp_path, handler, cap=4.0):
    s = load_settings(env=ENV)
    log = RunLogger(tmp_path / "l.jsonl", secrets=["sk-or-v1-SECRETSECRET"])
    c = LLMClient(s, log, BudgetGuard(cap, tmp_path / "l.jsonl"), run_id="r1",
                  transport=httpx.MockTransport(handler), raw_dir=tmp_path / "raw")
    return c, tmp_path / "l.jsonl"


async def ask(c, **kw):
    kw.setdefault("model", "a/b")
    return await c.chat(messages=[{"role": "user", "content": "hi"}], stage="pass1", app_id=3,
                        prompt_version="p1-x", **kw)


async def test_success_logs_cost_and_never_the_key(tmp_path):
    seen = {}

    def h(req):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=OK)

    c, log = client(tmp_path, h)
    r = await ask(c)
    assert r.text == '{"x": 1}' and r.cost_usd == 0.0012 and not r.cost_estimated and r.retries == 0
    assert (r.tokens_in, r.tokens_out) == (100, 20)
    assert seen["auth"] == "Bearer sk-or-v1-SECRETSECRET"
    assert seen["body"]["temperature"] == 0 and seen["body"]["usage"] == {"include": True}
    assert seen["body"]["model"] == "a/b" and "response_format" not in seen["body"]
    [line] = [x for x in read_log(log) if x["kind"] == "llm"]
    assert line["cost_usd"] == 0.0012 and line["status"] == "ok" and line["app_id"] == 3
    assert line["stage"] == "pass1" and line["prompt_version"] == "p1-x" and line["raw_path"]
    files = [log, *(tmp_path / "raw").rglob("*.json")]
    assert len(files) == 2
    for f in files:
        assert "SECRETSECRET" not in f.read_text(encoding="utf-8")


async def test_retries_429_then_succeeds(tmp_path):
    calls = iter([httpx.Response(429), httpx.Response(200, json=OK)])
    c, log = client(tmp_path, lambda req: next(calls))
    assert (await ask(c)).retries == 1
    assert read_log(log)[-1]["retries"] == 1


async def test_retries_exhausted_raises(tmp_path):
    c, log = client(tmp_path, lambda req: httpx.Response(503))
    with pytest.raises(LLMError):
        await ask(c)
    line = read_log(log)[-1]
    assert line["status"] == "error" and line["retries"] == 3


async def test_non_retryable_401_raises_and_logs(tmp_path):
    n = {"calls": 0}

    def h(req):
        n["calls"] += 1
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    c, log = client(tmp_path, h)
    with pytest.raises(LLMError) as e:
        await ask(c)
    assert n["calls"] == 1 and e.value.status == 401
    assert read_log(log)[-1]["status"] == "error"


async def test_missing_usage_cost_falls_back_to_estimate(tmp_path):
    body = {**OK, "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0}}
    c, log = client(tmp_path, lambda req: httpx.Response(200, json=body))
    r = await ask(c, model="unlisted/model")
    assert r.cost_estimated and r.cost_usd == pytest.approx(3.0)
    assert read_log(log)[-1]["cost_estimated"] is True


async def test_budget_refuses_before_any_request(tmp_path):
    n = {"calls": 0}

    def h(req):
        n["calls"] += 1
        return httpx.Response(200, json=OK)

    c, _ = client(tmp_path, h, cap=0.0)
    with pytest.raises(BudgetExceeded):
        await ask(c)
    assert n["calls"] == 0


async def test_cost_is_recorded_in_budget(tmp_path):
    c, _ = client(tmp_path, lambda req: httpx.Response(200, json=OK))
    await ask(c)
    assert c.budget.spent == pytest.approx(0.0012)


async def test_json_schema_downgrade_on_400(tmp_path):
    formats = []

    def h(req):
        formats.append(json.loads(req.content).get("response_format", {}).get("type"))
        if formats[-1] == "json_schema":
            return httpx.Response(400, json={"error": {"message": "response_format json_schema not supported"}})
        return httpx.Response(200, json=OK)

    c, log = client(tmp_path, h)
    await ask(c, json_schema={"type": "object"})
    await ask(c, json_schema={"type": "object"})  # capability cached per model
    assert formats == ["json_schema", "json_object", "json_object"]
    assert "format_downgraded" in (read_log(log)[0]["error"] or "")


async def test_empty_content_is_error(tmp_path):
    body = {**OK, "choices": [{"message": {"content": None}}]}
    c, _ = client(tmp_path, lambda req: httpx.Response(200, json=body))
    with pytest.raises(LLMError, match="empty_content"):
        await ask(c)


async def test_200_with_error_body_is_retried(tmp_path):
    calls = iter([httpx.Response(200, json={"error": {"message": "upstream"}}), httpx.Response(200, json=OK)])
    c, _ = client(tmp_path, lambda req: next(calls))
    assert (await ask(c)).retries == 1


async def test_transport_error_is_retried(tmp_path):
    state = {"n": 0}

    def h(req):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json=OK)

    c, _ = client(tmp_path, h)
    assert (await ask(c)).retries == 1


async def test_key_status_returns_numbers_only(tmp_path):
    data = {"data": {"label": "sk-or-v1-SECRETSECRET", "limit": 5, "limit_remaining": 4.5, "usage": 0.5,
                     "is_free_tier": False}}
    c, _ = client(tmp_path, lambda req: httpx.Response(200, json=data))
    st = await c.key_status()
    assert st == {"limit": 5, "limit_remaining": 4.5, "usage": 0.5, "is_free_tier": False}


@pytest.mark.parametrize("text", ['```json\n{"a": 1}\n```', 'Sure! {"a": 1} hope this helps', '{"a": 1}'])
def test_parse_json_loose(text):
    assert parse_json_loose(text) == {"a": 1}


@pytest.mark.parametrize("text", ["no json here", "[1, 2]", "{broken"])
def test_parse_json_loose_rejects(text):
    with pytest.raises(ValueError):
        parse_json_loose(text)


async def test_exhausted_retries_carry_status_and_kind(tmp_path):
    c, _ = client(tmp_path, lambda req: httpx.Response(429, json={"error": {"message": "Provider returned error"}}))
    with pytest.raises(LLMError) as e:
        await ask(c)
    assert e.value.status == 429 and e.value.kind == "transient"


async def test_empty_content_kind(tmp_path):
    body = {**OK, "choices": [{"message": {"content": ""}}]}
    c, _ = client(tmp_path, lambda req: httpx.Response(200, json=body))
    with pytest.raises(LLMError) as e:
        await ask(c)
    assert e.value.kind == "empty_content"
