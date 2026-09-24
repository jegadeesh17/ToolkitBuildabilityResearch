import json
from pathlib import Path

import pytest

from agent.observability import read_log
from agent.tools import (
    ALLOWED_SLUGS,
    FETCH_SLUG,
    SEARCH_FALLBACK_SLUG,
    SEARCH_SLUG,
    DisallowedToolError,
    ToolConfigError,
    is_thin,
    parse_fetch_text,
    parse_search_hits,
)

FX = Path("tests/fixtures")


def test_allowlist_is_exactly_search_and_fetch():
    assert ALLOWED_SLUGS == {SEARCH_SLUG, SEARCH_FALLBACK_SLUG, FETCH_SLUG}


async def test_disallowed_slug_never_executes(tool_client):
    tc, calls = tool_client(lambda slug, args: {"successful": True, "data": {}})
    with pytest.raises(DisallowedToolError):
        await tc._execute("GMAIL_SEND_EMAIL", {}, app_id=1, stage="pass1")
    assert calls == []


@pytest.mark.skipif(not (FX / "composio_search.json").exists(), reason="live fixture not captured yet")
def test_parse_real_search_fixture():
    hits = parse_search_hits(json.loads((FX / "composio_search.json").read_text(encoding="utf-8")))
    assert hits and all(h.url.startswith("http") for h in hits)


@pytest.mark.skipif(not (FX / "composio_fetch.json").exists(), reason="live fixture not captured yet")
def test_parse_real_fetch_fixture():
    text, _, _ = parse_fetch_text(json.loads((FX / "composio_fetch.json").read_text(encoding="utf-8")))
    assert len(text) > 200


def test_parse_nested_unknown_shapes_and_dedupe():
    data = {"a": {"b": [{"link": "https://x.com/", "title": "X"}, {"url": "https://x.com"},
                        {"url": "https://y.com/p", "snippet": "s"}, {"url": "ftp://z"}]}}
    assert [h.url for h in parse_search_hits(data)] == ["https://x.com/", "https://y.com/p"]


def test_parse_fetch_matches_requested_url():
    data = {"results": [{"url": "https://a.com/x", "text": "A" * 50},
                        {"url": "https://b.com/y/", "title": "B", "text": "B" * 10}]}
    assert parse_fetch_text(data, "https://b.com/y") == ("B" * 10, "B", None)
    assert parse_fetch_text(data, "https://other.com")[0] == "A" * 50


async def test_fetch_truncates_and_hashes(tool_client):
    tc, calls = tool_client(lambda s, a: {"successful": True, "data": {"content": "é" * 30000}})
    page = await tc.fetch("https://x.com", app_id=1, stage="pass1")
    assert len(page.text) == 20000 and len(page.sha256) == 64 and page.error is None and not page.thin
    assert calls[0] == (FETCH_SLUG, {"urls": ["https://x.com"], "text": True, "max_characters": 20000})


async def test_backoff_then_fetch_failed_logged(tool_client):
    tc, calls = tool_client(lambda s, a: {"successful": False, "error": "429 Too Many Requests"})
    page = await tc.fetch("https://x.com", app_id=1, stage="pass1")
    assert page.error == "fetch_failed" and len(calls) == 4  # 1 try + 3 backoffs (1s, 2s, 4s)
    line = read_log(tc.logger.path)[-1]
    assert line["status"] == "error" and line["retries"] == 3 and line["kind"] == "tool"


async def test_non_retryable_error_fails_fast(tool_client):
    tc, calls = tool_client(lambda s, a: {"successful": False, "error": "invalid url"})
    page = await tc.fetch("https://x.com", app_id=1, stage="pass1")
    assert page.error == "fetch_failed" and len(calls) == 1


async def test_permission_error_is_config_error(tool_client):
    class PermissionDeniedError(Exception):
        pass

    def boom(slug, args):
        raise PermissionDeniedError("403 key has no access for tool_execution")
    tc, _ = tool_client(boom)
    with pytest.raises(ToolConfigError) as e:
        await tc.search("q", app_id=1, stage="pass1")
    assert e.value.var == "COMPOSIO_API_KEY"


async def test_search_falls_back_to_second_slug(tool_client):
    def ex(slug, args):
        results = [] if slug == SEARCH_SLUG else [{"url": "https://y.com", "title": "Y"}]
        return {"successful": True, "data": {"results": results}}
    tc, calls = tool_client(ex)
    assert [h.url for h in await tc.search("q", app_id=1, stage="pass1")] == ["https://y.com"]
    assert [c[0] for c in calls] == [SEARCH_SLUG, SEARCH_FALLBACK_SLUG]


async def test_search_caps_at_k(tool_client):
    tc, _ = tool_client(lambda s, a: {"successful": True,
                                      "data": {"results": [{"url": f"https://x.com/{i}"} for i in range(9)]}})
    assert len(await tc.search("q", app_id=1, stage="pass1", k=3)) == 3


def test_thin_page_detection():
    assert is_thin("Please enable JavaScript to view this site")
    assert is_thin("short")
    assert not is_thin("word " * 200)
