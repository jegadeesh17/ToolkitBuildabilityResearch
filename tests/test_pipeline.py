import json

import pytest

from agent.pipeline import (
    QUERY_TEMPLATES,
    load_prompt,
    parse_hint,
    render_user_message,
    research_app,
    salvage,
    select_urls,
    url_score,
)
from agent.schema import SCORED_FIELDS, UNKNOWN, AppSeed, Flag
from agent.tools import SearchHit

APP = AppSeed(id=3, app="Pipe", category="CRM", hint="pipe.com")
DOC = "https://pipe.com/docs/auth"
PAGE_TEXT = ("Pipe API. Authenticate with OAuth 2.0 or a personal API token. Free developer sandbox accounts "
             "are available to everyone. The REST API covers deals, persons, organizations, activities and "
             "more with full create, read, update and delete. Pipe publishes an official MCP server. ") * 3


def q(s):
    return [{"url": DOC, "quote": s}]


VALID_DICT = {
    "description": "Sales CRM", "auth_methods": ["oauth2", "api_key"], "access_model": "self_serve_free",
    "api_type": ["rest"], "api_breadth": "broad", "existing_mcp": "official", "existing_mcp_url": None,
    "verdict": "buildable_now", "blocker": "none", "api_pricing_tier": "free", "confidence": 0.8,
    "evidence": {
        "auth_methods": q("Authenticate with OAuth 2.0 or a personal API token"),
        "access_model": q("Free developer sandbox accounts are available to everyone"),
        "api_type": q("The REST API covers deals, persons"),
        "api_breadth": q("with full create, read, update and delete"),
        "existing_mcp": q("Pipe publishes an official MCP server"),
        "verdict": q("Free developer sandbox accounts are available to everyone"),
        "blocker": q("Free developer sandbox accounts are available to everyone")},
    "unknown_reason": {}}
VALID = json.dumps(VALID_DICT)
THIN = ("Please enable JavaScript. Authenticate with OAuth 2.0 or a personal API token. "
        "Free developer sandbox accounts are available to everyone. The REST API covers deals, persons. "
        "with full create, read, update and delete. Pipe publishes an official MCP server.")


def kw(**over):
    d = dict(model="m", stage="pass1", run_id="r", prompt=load_prompt())
    d.update(over)
    return d


def test_query_templates_match_spec():
    assert QUERY_TEMPLATES == (
        "{app} API documentation authentication {hint}",
        "{app} API pricing plans access free trial API key {hint}",
        "{app} MCP server",
        "{app} API rate limits OpenAPI reference {hint}",
    )


def test_parse_hint_variants():
    assert parse_hint("twenty.com (open-source CRM)").host == "twenty.com"
    h = parse_hint("developers.facebook.com/docs/whatsapp")
    assert (h.host, h.path) == ("developers.facebook.com", "/docs/whatsapp")
    assert parse_hint("paygent (NMI-powered)").host is None
    assert parse_hint("").host is None


def test_url_score_prefers_hint_path_on_shared_hosts():
    h = parse_hint("developers.facebook.com/docs/whatsapp")
    assert (url_score("https://developers.facebook.com/docs/whatsapp/cloud-api", h)
            > url_score("https://developers.facebook.com/docs/instagram", h)
            > url_score("https://reddit.com/r/x", h))


def test_url_score_registrable_domain_match():
    assert url_score("https://www.klaviyo.com/pricing", parse_hint("developers.klaviyo.com")) >= 1


def test_select_urls_round_robin_dedupe_cap():
    h = parse_hint("ex.com")
    qs = [[SearchHit(f"https://ex.com/docs/{i}") for i in range(3)],
          [SearchHit("https://ex.com/docs/0/"), SearchHit("https://ex.com/pricing")],
          [SearchHit("https://github.com/ex/mcp")],
          [SearchHit("https://ex.com/ref")]]
    urls = select_urls(qs, h, max_pages=6)
    assert len(urls) == len(set(urls)) == 6
    assert "https://github.com/ex/mcp" in urls  # the MCP query always gets a slot
    assert sum("docs/0" in u for u in urls) == 1  # dedupe via canonical_url


def test_select_urls_skips_binary_links():
    urls = select_urls([[SearchHit("https://ex.com/a.pdf"), SearchHit("https://ex.com/b")]], parse_hint("ex.com"))
    assert urls == ["https://ex.com/b"]


def test_prompt_never_contains_hint(make_bundle):
    app = AppSeed(id=1, app="Otter", category="AI", hint="help.otter.ai (MCP server)")
    msg = render_user_message(app, make_bundle([("https://a.com", "text " * 50)]))
    assert "MCP server" not in msg and "help.otter.ai" not in msg and 'url="https://a.com"' in msg


def test_prompt_version_is_stable_hash():
    p = load_prompt()
    assert p.version == load_prompt().version and p.version.startswith("p1-") and len(p.version) == 9


def test_salvage_keeps_valid_fields_only():
    ext, bad = salvage({"auth_methods": ["oauth2"], "verdict": "maybe", "access_model": "self_serve_free",
                        "api_type": ["rest"], "api_breadth": "broad", "existing_mcp": "official", "blocker": "none",
                        "evidence": {"auth_methods": [{"url": "https://a.com", "quote": "OAuth 2.0 is supported"},
                                                      {"url": "https://a.com", "quote": "x"}]}})
    assert ext.verdict == UNKNOWN and bad == ["verdict"] and ext.unknown_reason["verdict"] == "model_unsure"
    assert ext.auth_methods == ["oauth2"] and len(ext.evidence["auth_methods"]) == 1


def test_salvage_of_garbage_is_all_unknown():
    ext, bad = salvage({"still": "bad"})
    assert bad == list(SCORED_FIELDS) and all(ext.is_unknown(f) for f in SCORED_FIELDS)


async def test_research_app_happy_path(fake_llm, fake_tools, run_logger):
    llm, tools = fake_llm([VALID]), fake_tools({DOC: PAGE_TEXT}, [DOC])
    row, stats = await research_app(APP, llm=llm, tools=tools, logger=run_logger, **kw())
    assert row.meta.pass_ == 1 and row.meta.model == "m" and row.verdict == "buildable_now" and not row.flags
    assert stats["grounded"] == stats["total_claims"] == 7 and llm.calls == 1 and stats["pages"] == 1


async def test_bundle_is_saved(fake_llm, fake_tools, run_logger):
    from agent.store import load_bundle
    await research_app(APP, llm=fake_llm([VALID]), tools=fake_tools({DOC: PAGE_TEXT}, [DOC]), logger=run_logger,
                       **kw(run_id="runX"))
    b = load_bundle("runX", 3)
    assert len(b.queries) == 4 and b.pages[0].url == DOC


async def test_repair_loop_then_salvage(fake_llm, fake_tools, run_logger):
    from agent.observability import read_log
    llm = fake_llm(["not json", '{"verdict": "maybe"}', '{"still": "bad"}'])
    row, _ = await research_app(APP, llm=llm, tools=fake_tools({DOC: PAGE_TEXT}, [DOC]), logger=run_logger, **kw())
    assert llm.calls == 3 and Flag.NEEDS_HUMAN in row.flags
    assert all(row.is_unknown(f) and f in row.unknown_reason for f in SCORED_FIELDS)
    vals = [r for r in read_log(run_logger.path) if r["kind"] == "llm_validation"]
    assert [r["schema_valid"] for r in vals] == [False, False, False]


async def test_repair_succeeds_on_second_try(fake_llm, fake_tools, run_logger):
    llm = fake_llm(["oops", VALID])
    row, _ = await research_app(APP, llm=llm, tools=fake_tools({DOC: PAGE_TEXT}, [DOC]), logger=run_logger, **kw())
    assert llm.calls == 2 and Flag.NEEDS_HUMAN not in row.flags and row.verdict == "buildable_now"


async def test_zero_pages_skips_llm(fake_llm, fake_tools, run_logger):
    llm = fake_llm([])
    row, _ = await research_app(APP, llm=llm, tools=fake_tools({}, []), logger=run_logger, **kw())
    assert llm.calls == 0 and set(row.unknown_reason.values()) == {"no_docs_found"}
    assert Flag.NEEDS_HUMAN in row.flags


async def test_all_fetches_fail_gives_fetch_failed(fake_llm, fake_tools, run_logger):
    llm = fake_llm([])
    row, _ = await research_app(APP, llm=llm, tools=fake_tools({}, [DOC]), logger=run_logger, **kw())
    assert llm.calls == 0 and set(row.unknown_reason.values()) == {"fetch_failed"}


async def test_thin_pages_default_reason_js_only(fake_llm, fake_tools, run_logger):
    reply = dict(VALID_DICT, api_breadth="unknown", unknown_reason={})
    row, _ = await research_app(APP, llm=fake_llm([json.dumps(reply)]), tools=fake_tools({DOC: THIN}, [DOC]),
                                logger=run_logger, **kw())
    assert row.api_breadth == UNKNOWN and row.unknown_reason["api_breadth"] == "js_only_docs"


async def test_missing_reason_filled_with_model_unsure(fake_llm, fake_tools, run_logger):
    reply = dict(VALID_DICT, api_breadth="unknown", unknown_reason={})
    row, stats = await research_app(APP, llm=fake_llm([json.dumps(reply)]), tools=fake_tools({DOC: PAGE_TEXT}, [DOC]),
                                    logger=run_logger, **kw())
    assert row.unknown_reason["api_breadth"] == "model_unsure" and stats["reasons_filled"] == 1


async def test_evidence_not_in_bundle_flags_grounding(fake_llm, fake_tools, run_logger):
    bad = VALID.replace(DOC, "https://elsewhere.com/x")
    row, stats = await research_app(APP, llm=fake_llm([bad]), tools=fake_tools({DOC: PAGE_TEXT}, [DOC]),
                                    logger=run_logger, **kw())
    assert Flag.GROUNDING_FAILED in row.flags and stats["grounded"] == 0


async def test_unsupported_field_becomes_unknown(fake_llm, fake_tools, run_logger):
    d = json.loads(VALID)
    del d["evidence"]["api_breadth"]
    row, stats = await research_app(APP, llm=fake_llm([json.dumps(d)]), tools=fake_tools({DOC: PAGE_TEXT}, [DOC]),
                                    logger=run_logger, **kw())
    assert row.api_breadth == UNKNOWN and stats["evidence_dropped"] == 1


async def test_rule_violation_flagged(fake_llm, fake_tools, run_logger):
    reply = dict(VALID_DICT, blocker="paid_plan_required")
    row, _ = await research_app(APP, llm=fake_llm([json.dumps(reply)]), tools=fake_tools({DOC: PAGE_TEXT}, [DOC]),
                                logger=run_logger, **kw())
    assert Flag.RULE_VIOLATION in row.flags


async def test_injected_bundle_skips_tools(fake_llm, fake_tools, run_logger, make_bundle):
    tools = fake_tools({}, [])
    row, _ = await research_app(APP, llm=fake_llm([VALID]), tools=tools, logger=run_logger,
                                bundle=make_bundle([(DOC, PAGE_TEXT)]), **kw())
    assert tools.calls == 0 and not row.flags


async def test_http_error_propagates(fake_tools, run_logger):
    from agent.llm_client import LLMError

    class Boom:
        async def chat(self, **kw):
            raise LLMError("http_404: model not found", 404)
    with pytest.raises(LLMError):
        await research_app(APP, llm=Boom(), tools=fake_tools({DOC: PAGE_TEXT}, [DOC]), logger=run_logger, **kw())


async def test_empty_content_counts_as_schema_failure(fake_tools, run_logger):
    from agent.llm_client import LLMError

    class Empty:
        calls = 0

        async def chat(self, **kw):
            Empty.calls += 1
            raise LLMError("empty_content")
    row, _ = await research_app(APP, llm=Empty(), tools=fake_tools({DOC: PAGE_TEXT}, [DOC]), logger=run_logger,
                                **kw())
    assert Empty.calls == 3 and Flag.NEEDS_HUMAN in row.flags
