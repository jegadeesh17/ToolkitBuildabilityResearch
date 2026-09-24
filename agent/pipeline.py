"""Pass 1 for one app (SPEC §2.4): search → fetch → evidence bundle → one schema-bound extraction
(≤2 repair calls) → evidence-or-unknown → L1 grounding → consistency rules.

Talks to the outside world only through the injected `llm` (LLMClient) and `tools` (ToolClient).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from agent.grounding import apply_grounding, canonical_url, ground_row
from agent.llm_client import LLMError, parse_json_loose
from agent.observability import RunLogger, utc_now
from agent.rules import apply_rules, enforce_evidence, unknown_fields
from agent.schema import (
    REASONABLE_FIELDS,
    SCORED_FIELDS,
    UNKNOWN,
    AppResult,
    AppSeed,
    Evidence,
    EvidenceBundle,
    Extraction,
    Flag,
    UnknownReason,
)
from agent.store import save_bundle

QUERY_TEMPLATES = (
    "{app} API documentation authentication {hint}",
    "{app} API pricing plans access free trial API key {hint}",
    "{app} MCP server",
    "{app} API rate limits OpenAPI reference {hint}",
)
MAX_PAGES = 6
RESULTS_PER_QUERY = 3
MAX_REPAIRS = 2
MAX_OUTPUT_TOKENS = 8000  # reasoning models spend output tokens before the JSON
PROMPTS_DIR = Path(__file__).parent / "prompts"
EXTRACTION_SCHEMA = Extraction.model_json_schema()
_BINARY = re.compile(r"\.(pdf|png|jpe?g|gif|svg|zip|gz|mp4|mp3|docx?|xlsx?|pptx?)(\?|$)", re.IGNORECASE)
_DOCSY = re.compile(r"docs|developer|api|reference", re.IGNORECASE)


@dataclass(frozen=True)
class Prompt:
    text: str
    version: str


def load_prompt(name: str = "extract") -> Prompt:
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")  # universal newlines: CRLF-safe hash
    digest = hashlib.sha256((text + json.dumps(EXTRACTION_SCHEMA, sort_keys=True)).encode("utf-8")).hexdigest()
    return Prompt(text=text, version="p1-" + digest[:6])


def _repair_template() -> str:
    return (PROMPTS_DIR / "repair.md").read_text(encoding="utf-8")


@dataclass(frozen=True)
class HintTarget:
    host: str | None
    path: str


def parse_hint(hint: str) -> HintTarget:
    token = (hint or "").split()[0] if (hint or "").split() else ""
    if "." not in token:
        return HintTarget(None, "")
    host, _, path = token.partition("/")
    return HintTarget(host.lower().removeprefix("www."), ("/" + path).rstrip("/") if path else "")


def _base_domain(host: str) -> str:
    return ".".join(host.split(".")[-2:])


def url_score(url: str, hint: HintTarget) -> float:
    s = urlsplit(url)
    host = (s.hostname or "").lower().removeprefix("www.")
    path = s.path.lower()
    score = 0.0
    if hint.host:
        host_match = host == hint.host or host.endswith("." + hint.host)
        if host_match and hint.path and path.startswith(hint.path.lower()):
            score = 2.0
        elif host_match or _base_domain(host) == _base_domain(hint.host):
            score = 1.0
    if _DOCSY.search(host + path):
        score += 0.5
    return score


def select_urls(hits_by_query: list[list[Any]], hint: HintTarget, max_pages: int = MAX_PAGES) -> list[str]:
    """Round 1: best hit of each query (topic coverage). Round 2: fill remaining slots by score."""
    chosen: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> bool:
        key = canonical_url(url)
        if key in seen or _BINARY.search(url):
            return False
        seen.add(key)
        chosen.append(url)
        return True

    for hits in hits_by_query:
        if len(chosen) >= max_pages:
            break
        for _, h in sorted(enumerate(hits), key=lambda ih: (-url_score(ih[1].url, hint), ih[0])):
            if add(h.url):
                break
    rest = [(url_score(h.url, hint), qi, i, h.url) for qi, hits in enumerate(hits_by_query) for i, h in enumerate(hits)]
    for _, _, _, url in sorted(rest, key=lambda t: (-t[0], t[1], t[2])):
        if len(chosen) >= max_pages:
            break
        add(url)
    return chosen


async def gather_bundle(app: AppSeed, tools: Any, run_id: str, stage: str) -> EvidenceBundle:
    texts = [" ".join(t.format(app=app.app, hint=app.hint).split()) for t in QUERY_TEMPLATES]
    # Concurrent within the app; ToolClient's global throttle still spaces the calls. gather keeps order.
    hits_by_query = list(await asyncio.gather(
        *(tools.search(q, app_id=app.id, stage=stage, k=RESULTS_PER_QUERY) for q in texts)))
    queries = [{"query": q, "hits": [asdict(h) for h in hits]} for q, hits in zip(texts, hits_by_query, strict=True)]
    urls = select_urls(hits_by_query, parse_hint(app.hint))
    pages = list(await asyncio.gather(*(tools.fetch(u, app_id=app.id, stage=stage) for u in urls)))
    bundle = EvidenceBundle(app_id=app.id, app=app.app, run_id=run_id, created_at=utc_now(),
                            queries=queries, pages=pages)
    save_bundle(bundle)
    return bundle


def usable_pages(bundle: EvidenceBundle) -> list:
    return [p for p in bundle.pages if p.text and not p.error]


def render_user_message(app: AppSeed, bundle: EvidenceBundle, max_chars: int | None = None) -> str:
    pages = usable_pages(bundle)
    parts = [f"App: {app.app}", f"Category: {app.category}", "",
             f"Evidence pages ({len(pages)}). Cite only these URLs and copy quotes verbatim.", ""]
    for p in pages:
        text = p.text if max_chars is None else p.text[:max_chars]
        title = (p.title or "").replace('"', "'")
        parts.append(f'<page url="{p.url}" title="{title}">\n{text}\n</page>\n')
    return "\n".join(parts)


def _unknown_base() -> dict:
    return {f: UNKNOWN for f in SCORED_FIELDS}


def all_unknown(reason: UnknownReason, description: str = "") -> Extraction:
    return Extraction.model_validate({**_unknown_base(), "description": description, "confidence": 0.0,
                                      "unknown_reason": {f: reason.value for f in REASONABLE_FIELDS}})


def salvage(raw: Any) -> tuple[Extraction, list[str]]:
    """Keep every field that validates on its own; the rest become unknown / model_unsure."""
    raw = raw if isinstance(raw, dict) else {}
    data: dict[str, Any] = {**_unknown_base(), "description": raw.get("description") or "",
                            "confidence": raw.get("confidence", 0.5), "existing_mcp_url": raw.get("existing_mcp_url")}
    bad: list[str] = []
    for f in SCORED_FIELDS:
        try:
            Extraction.model_validate({**_unknown_base(), f: raw.get(f)})
            data[f] = raw.get(f)
        except ValidationError:
            bad.append(f)
    try:
        Extraction.model_validate({**_unknown_base(), "api_pricing_tier": raw.get("api_pricing_tier")})
        data["api_pricing_tier"] = raw.get("api_pricing_tier")
    except ValidationError:
        pass
    evidence: dict[str, list] = {}
    for f, items in (raw.get("evidence") or {}).items() if isinstance(raw.get("evidence"), dict) else []:
        if f not in SCORED_FIELDS or not isinstance(items, list):
            continue
        good = []
        for it in items:
            try:
                good.append(Evidence.model_validate(it).model_dump())
            except ValidationError:
                continue
        if good:
            evidence[f] = good
    reasons: dict[str, str] = {}
    for f, r in (raw.get("unknown_reason") or {}).items() if isinstance(raw.get("unknown_reason"), dict) else []:
        if f in REASONABLE_FIELDS and r in {x.value for x in UnknownReason}:
            reasons[f] = r
    for f in bad:
        reasons[f] = UnknownReason.MODEL_UNSURE.value
    data["evidence"], data["unknown_reason"] = evidence, reasons
    return Extraction.model_validate(data), bad


def _short_error(err: Exception) -> str:
    if isinstance(err, ValidationError):
        return "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in err.errors()[:8])
    return str(err)[:500]


async def extract(app: AppSeed, bundle: EvidenceBundle, llm: Any, *, model: str, prompt: Prompt, stage: str,
                  run_id: str, logger: RunLogger) -> tuple[Extraction, bool]:
    """One schema-bound call plus up to MAX_REPAIRS repairs. Returns (extraction, needs_human)."""
    messages = [{"role": "system", "content": prompt.text},
                {"role": "user", "content": render_user_message(app, bundle)}]
    last_parsed: Any = None
    best: Extraction | None = None  # last schema-valid reply (used if evidence is still missing after repairs)
    reasoning: dict | None = None  # set to low effort after an empty reply (reasoning ate the output budget)
    truncated = False
    attempt = 0
    while attempt < MAX_REPAIRS + 1:
        attempt += 1
        text = ""
        try:
            resp = await llm.chat(messages=messages, model=model, stage=stage, app_id=app.id,
                                  prompt_version=prompt.version, json_schema=EXTRACTION_SCHEMA,
                                  max_tokens=MAX_OUTPUT_TOKENS, reasoning=reasoning)
            text = resp.text
            parsed = parse_json_loose(text)
            last_parsed = parsed
            extraction = Extraction.model_validate(parsed)
            best = extraction
            gaps = [f for f in SCORED_FIELDS if not extraction.is_unknown(f) and not extraction.evidence.get(f)]
            if gaps and attempt <= MAX_REPAIRS:
                raise ValueError(f"these fields have a value but no evidence: {', '.join(gaps)}. Add at least one "
                                 "verbatim {url, quote} from the pages for each, or set the field to unknown "
                                 "with an unknown_reason")
            logger.append({"kind": "llm_validation", "run_id": run_id, "app_id": app.id, "stage": stage,
                           "model": model, "prompt_version": prompt.version, "schema_valid": True,
                           "retries": attempt - 1, "error": f"evidence missing: {gaps}" if gaps else None})
            return extraction, False
        except LLMError as e:
            if e.kind != "empty_content":
                if e.status == 400 and "context" in str(e).lower() and not truncated:
                    truncated = True  # one retry with shorter pages; not counted as a repair
                    messages[1] = {"role": "user", "content": render_user_message(app, bundle, max_chars=10_000)}
                    attempt -= 1
                    continue
                raise  # HTTP/transient failures are not schema problems: surface them (--resume retries)
            error = f"no usable reply ({e})"
            reasoning = {"effort": "low"}
        except (ValueError, ValidationError) as e:
            error = _short_error(e)
        logger.append({"kind": "llm_validation", "run_id": run_id, "app_id": app.id, "stage": stage, "model": model,
                       "prompt_version": prompt.version, "schema_valid": False, "retries": attempt - 1,
                       "error": error[:300]})
        if attempt <= MAX_REPAIRS:
            messages = messages + [{"role": "assistant", "content": text or "(empty reply)"},
                                   {"role": "user", "content": _repair_template().format(error=error)}]
    if best is not None:  # valid reply whose evidence gaps survived the repairs: finalize() enforces them
        return best, False
    extraction, _ = salvage(last_parsed or {})
    return extraction, True


def finalize(app: AppSeed, extraction: Extraction, bundle: EvidenceBundle, *, run_id: str, model: str,
             prompt_version: str, needs_human: bool) -> tuple[AppResult, dict]:
    data = extraction.model_dump(mode="json")
    pages = usable_pages(bundle)
    default_reason = (UnknownReason.JS_ONLY_DOCS if pages and all(p.thin for p in pages)
                      else UnknownReason.MODEL_UNSURE).value
    reasons = dict(data["unknown_reason"])
    filled = 0
    for f in REASONABLE_FIELDS:
        if data[f] == UNKNOWN and f not in reasons:
            reasons[f] = default_reason
            filled += 1
    row = AppResult.model_validate({
        **data, "unknown_reason": reasons, "id": app.id, "app": app.app, "category": app.category,
        "flags": [Flag.NEEDS_HUMAN.value] if needs_human else [],
        "meta": {"pass": 1, "run_id": run_id, "model": model, "prompt_version": prompt_version,
                 "updated_at": utc_now()}})
    row, dropped = enforce_evidence(row)
    report = ground_row(row, bundle)
    row = apply_rules(apply_grounding(row, report))
    stats = {"pages": len(pages), "evidence_dropped": len(dropped), "grounded": report.grounded,
             "total_claims": report.total, "unknown_fields": len(unknown_fields(row)), "reasons_filled": filled}
    return row, stats


async def research_app(app: AppSeed, *, llm: Any, tools: Any, logger: RunLogger, model: str, stage: str,
                       run_id: str, prompt: Prompt, bundle: EvidenceBundle | None = None) -> tuple[AppResult, dict]:
    if bundle is None:
        bundle = await gather_bundle(app, tools, run_id, stage)
    elif bundle.run_id != run_id:  # reused bundle: keep a copy beside this run's rows for later grounding
        bundle = bundle.model_copy(update={"run_id": run_id, "app_id": app.id, "app": app.app})
        save_bundle(bundle)
    if not usable_pages(bundle):
        any_hits = any(q.get("hits") for q in bundle.queries) or bool(bundle.pages)
        reason = UnknownReason.FETCH_FAILED if any_hits else UnknownReason.NO_DOCS_FOUND
        extraction, needs_human = all_unknown(reason), True
    else:
        extraction, needs_human = await extract(app, bundle, llm, model=model, prompt=prompt, stage=stage,
                                                run_id=run_id, logger=logger)
    return finalize(app, extraction, bundle, run_id=run_id, model=model, prompt_version=prompt.version,
                    needs_human=needs_human)
