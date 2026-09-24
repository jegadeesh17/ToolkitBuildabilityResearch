"""Pass 2 verification loops (SPEC §2.5, §2.7).

L1 grounding → L2 cross-model (VERIFY_MODEL, same frozen prompt, same bundle) → L3 judge (JUDGE_MODEL,
disputed / ungrounded / rule-violating fields only) → L4 bounded re-research (≤5 tool calls) → merge with
deterministic confidence and a `pass2_diff` entry for every changed field.

python -m agent.verify [--input results/pass1.json] [--output results/pass2.json]
                       [--loops grounding,cross,judge,reresearch] [--resume] [--ids 1,2] [--concurrency 4]

Never reads the human ground truth. Progress lines print counts only.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from pydantic import ValidationError

from agent import config
from agent.budget import BudgetExceeded, BudgetGuard
from agent.config import ConfigError, config_error_message, load_settings, secret_values
from agent.cost_report import render, summarize
from agent.grounding import apply_grounding, canonical_url, ground_row
from agent.llm_client import LLMError, parse_json_loose
from agent.observability import RunLogger, new_run_id, read_log, redact, utc_now
from agent.pipeline import (
    PROMPTS_DIR,
    Prompt,
    _repair_template,
    _unknown_base,
    extract,
    load_prompt,
    parse_hint,
    render_user_message,
    select_urls,
    usable_pages,
)
from agent.rules import apply_rules, check_rules, enforce_evidence
from agent.schema import (
    FIELD_DEFINITIONS,
    SCORED_FIELDS,
    SET_FIELDS,
    UNKNOWN,
    AppResult,
    AppSeed,
    Evidence,
    EvidenceBundle,
    Extraction,
    Flag,
    UnknownReason,
)
from agent.store import load_apps, load_bundle, load_results, save_bundle, write_results_atomic

LOOPS = ("grounding", "cross", "judge", "reresearch")
CONFIDENCE = {"agree": 0.9, "judge": 0.7, "grounded_only": 0.7, "reresearch": 0.4, "unresolved": 0.2}
MAX_RERESEARCH_TOOL_CALLS = 5
RERESEARCH_QUERIES = 2
RERESEARCH_FETCHES = 3
RULE_FIELDS = {1: ("api_type", "verdict"), 2: ("verdict", "blocker", "access_model"),
               3: ("access_model", "verdict"), 4: ("blocker", "verdict")}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:6]


def judge_prompt() -> Prompt:
    base = (PROMPTS_DIR / "extract.md").read_text(encoding="utf-8")
    definitions = base[base.index("# Scored fields"):base.index("# Other fields")]
    text = (PROMPTS_DIR / "judge.md").read_text(encoding="utf-8") + "\n" + definitions
    return Prompt(text=text, version="j-" + _hash(text))


def reresearch_prompt() -> Prompt:
    text = (PROMPTS_DIR / "reresearch.md").read_text(encoding="utf-8")
    return Prompt(text=text, version="r-" + _hash(text))


def plain(value: Any) -> Any:
    if isinstance(value, list):
        return sorted(str(v) for v in value)
    return None if value is None else str(value)


def same_value(field_name: str, a: Any, b: Any) -> bool:
    if field_name in SET_FIELDS and isinstance(a, list) and isinstance(b, list):
        return set(map(str, a)) == set(map(str, b))
    return plain(a) == plain(b)


def normalized(field_name: str, value: Any) -> Any:
    """Validated value for one scored field, or raises ValidationError."""
    return getattr(Extraction.model_validate({**_unknown_base(), field_name: value}), field_name)


def valid_evidence(items: Any) -> list[Evidence]:
    out = []
    for it in items if isinstance(items, list) else []:
        try:
            out.append(Evidence.model_validate(it))
        except ValidationError:
            continue
    return out


def grounded(field_name: str, value: Any, evidence: list[Evidence], bundle: EvidenceBundle) -> bool:
    if value == UNKNOWN or not evidence:
        return False
    probe = Extraction.model_validate({**_unknown_base(), field_name: value,
                                       "evidence": {field_name: [e.model_dump() for e in evidence]}})
    report = ground_row(probe, bundle)
    return report.total > 0 and not report.failed_fields


@dataclass
class FieldResult:
    value: Any
    evidence: list[Evidence]
    resolved_by: str
    confidence: float
    reason: str


@dataclass
class VerifyContext:
    llm: Any
    tools: Any
    logger: RunLogger
    verify_model: str
    judge_model: str
    run_id: str
    loops: set[str]
    prompt: Prompt
    judge: Prompt
    rer: Prompt


async def _ask_json(ctx: VerifyContext, *, app: AppSeed, system: str, user: str, model: str, stage: str,
                    prompt_version: str, max_tokens: int) -> dict | None:
    """One call plus one repair; returns the parsed object or None. HTTP/transient errors propagate."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in (1, 2):
        text = ""
        try:
            resp = await ctx.llm.chat(messages=messages, model=model, stage=stage, app_id=app.id,
                                      prompt_version=prompt_version, max_tokens=max_tokens)
            text = resp.text
            parsed = parse_json_loose(text)
            ctx.logger.append({"kind": "llm_validation", "run_id": ctx.run_id, "app_id": app.id, "stage": stage,
                               "model": model, "prompt_version": prompt_version, "schema_valid": True,
                               "retries": attempt - 1})
            return parsed
        except LLMError as e:
            if e.kind != "empty_content":
                raise
            error = "empty reply"
        except ValueError as e:
            error = str(e)
        ctx.logger.append({"kind": "llm_validation", "run_id": ctx.run_id, "app_id": app.id, "stage": stage,
                           "model": model, "prompt_version": prompt_version, "schema_valid": False,
                           "retries": attempt - 1, "error": error[:300]})
        messages = messages + [{"role": "assistant", "content": text or "(empty reply)"},
                               {"role": "user", "content": _repair_template().format(error=error)}]
    return None


async def judge_fields(app: AppSeed, bundle: EvidenceBundle, row: AppResult, ext2: Extraction | None,
                       fields: list[str], ctx: VerifyContext) -> dict[str, FieldResult | None]:
    """L3. Returns a FieldResult for each field the judge settled with grounded evidence, else None."""
    def candidate(src: Any, f: str) -> dict | None:
        if src is None:
            return None
        return {"value": plain(getattr(src, f)), "evidence": [e.model_dump() for e in src.evidence.get(f, [])]}
    disputes = {f: {"candidate_A": candidate(row, f), "candidate_B": candidate(ext2, f),
                    "definition": FIELD_DEFINITIONS[f]} for f in fields}
    user = (render_user_message(app, bundle) + "\n\nDecide these fields (candidates may be wrong or ungrounded):\n"
            + json.dumps(disputes, ensure_ascii=False, indent=1))
    data = await _ask_json(ctx, app=app, system=ctx.judge.text, user=user, model=ctx.judge_model, stage="judge",
                           prompt_version=ctx.judge.version, max_tokens=4000)
    decided = data.get("fields") if isinstance(data, dict) else None
    out: dict[str, FieldResult | None] = {}
    for f in fields:
        item = decided.get(f) if isinstance(decided, dict) else None
        if not isinstance(item, dict):
            out[f] = None
            continue
        try:
            value = normalized(f, item.get("value"))
        except ValidationError:
            out[f] = None
            continue
        evidence = valid_evidence(item.get("evidence"))
        reason = " ".join(str(item.get("reason") or "judge decision").split())[:200]
        out[f] = FieldResult(value, evidence, "judge", CONFIDENCE["judge"], reason) \
            if grounded(f, value, evidence, bundle) else None
    return out


async def reresearch(app: AppSeed, bundle: EvidenceBundle, fields: list[str],
                     ctx: VerifyContext) -> tuple[dict[str, FieldResult], EvidenceBundle, int]:
    """L4: ≤2 targeted searches + ≤3 new pages (≤5 tool calls), then re-extract and re-ground those fields."""
    calls = 0
    user = (f"App: {app.app}\nCategory: {app.category}\nFields still unresolved:\n"
            + json.dumps({f: FIELD_DEFINITIONS[f] for f in fields}, ensure_ascii=False, indent=1))
    data = await _ask_json(ctx, app=app, system=ctx.rer.text, user=user, model=ctx.verify_model, stage="reresearch",
                           prompt_version=ctx.rer.version, max_tokens=1000)
    queries = [q.strip() for q in (data or {}).get("queries", []) if isinstance(q, str) and q.strip()] \
        if isinstance(data, dict) else []
    if not queries:
        queries = [f"{app.app} API " + " ".join(f.replace("_", " ") for f in fields[:3])]
    known = {canonical_url(p.url) for p in bundle.pages}
    hits_by_query = []
    for q in queries[:RERESEARCH_QUERIES]:
        if calls >= MAX_RERESEARCH_TOOL_CALLS:
            break
        hits = await ctx.tools.search(q, app_id=app.id, stage="reresearch", k=5, fallback=False)
        calls += 1
        hits_by_query.append([h for h in hits if canonical_url(h.url) not in known])
    room = min(RERESEARCH_FETCHES, MAX_RERESEARCH_TOOL_CALLS - calls)
    urls = select_urls(hits_by_query, parse_hint(app.hint), max_pages=room) if room > 0 else []
    new_pages = [await ctx.tools.fetch(u, app_id=app.id, stage="reresearch") for u in urls]
    calls += len(urls)
    augmented = bundle.model_copy(update={"pages": [*bundle.pages, *new_pages]})
    found: dict[str, FieldResult] = {}
    if any(p.text and not p.error for p in new_pages):
        ext, _ = await extract(app, augmented, ctx.llm, model=ctx.verify_model, prompt=ctx.prompt,
                               stage="reresearch", run_id=ctx.run_id, logger=ctx.logger)
        for f in fields:
            value, evidence = getattr(ext, f), list(ext.evidence.get(f, []))
            if grounded(f, value, evidence, augmented):
                found[f] = FieldResult(value, evidence, "reresearch", CONFIDENCE["reresearch"],
                                       f"re-researched with {len(urls)} new page(s)")
    return found, augmented, calls


async def verify_row(row: AppResult, bundle: EvidenceBundle, app: AppSeed,
                     ctx: VerifyContext) -> tuple[AppResult, dict]:
    stats = {"disputed": 0, "judged": 0, "tool_calls": 0, "unresolved": 0, "changed": 0}
    ungrounded: set[str] = set()
    if "grounding" in ctx.loops:
        ungrounded = ground_row(row, bundle).failed_fields & set(SCORED_FIELDS)
    ext2: Extraction | None = None
    if "cross" in ctx.loops and usable_pages(bundle):
        ext2, _ = await extract(app, bundle, ctx.llm, model=ctx.verify_model, prompt=ctx.prompt, stage="verify",
                                run_id=ctx.run_id, logger=ctx.logger)
    rule_fields = {f for v in check_rules(row) for f in RULE_FIELDS.get(v.rule, ())}

    results: dict[str, FieldResult] = {}
    to_judge: list[str] = []
    to_research: list[str] = []
    disputed: set[str] = set()
    for f in SCORED_FIELDS:
        v1, ev1 = getattr(row, f), list(row.evidence.get(f, []))
        v2 = getattr(ext2, f) if ext2 is not None else None
        if ext2 is not None and not same_value(f, v1, v2):
            disputed.add(f)
        is_grounded = v1 != UNKNOWN and bool(ev1) and f not in ungrounded
        if f not in disputed and f not in rule_fields and is_grounded:
            by = "agree" if ext2 is not None else "grounded_only"
            results[f] = FieldResult(v1, ev1, by, CONFIDENCE[by],
                                     "pass 1 and verifier agree" if ext2 is not None else "grounded in pass 1")
        elif v1 == UNKNOWN and (ext2 is None or v2 == UNKNOWN):
            to_research.append(f)
        else:
            to_judge.append(f)
    stats["disputed"] = len(disputed)

    if to_judge and "judge" in ctx.loops:
        judged = await judge_fields(app, bundle, row, ext2, to_judge, ctx)
        stats["judged"] = len(to_judge)
        for f in to_judge:
            if judged.get(f) is not None:
                results[f] = judged[f]
            else:
                to_research.append(f)
    else:
        to_research += to_judge

    augmented = bundle
    if to_research and "reresearch" in ctx.loops:
        found, augmented, stats["tool_calls"] = await reresearch(app, bundle, to_research, ctx)
        results.update(found)

    for f in SCORED_FIELDS:
        if f not in results:
            results[f] = FieldResult(getattr(row, f), list(row.evidence.get(f, [])), "unresolved",
                                     CONFIDENCE["unresolved"], "no loop could resolve this field")
    stats["unresolved"] = sum(r.resolved_by == "unresolved" for r in results.values())

    d = row.to_json_dict()
    evidence = dict(d["evidence"])
    reasons = dict(d["unknown_reason"])
    diffs = []
    for f in SCORED_FIELDS:
        r = results[f]
        d[f] = plain(r.value) if isinstance(r.value, list) else str(r.value)
        if r.evidence:
            evidence[f] = [e.model_dump() for e in r.evidence]
        else:
            evidence.pop(f, None)
        if r.value == UNKNOWN:
            reasons.setdefault(f, UnknownReason.MODEL_UNSURE.value)
        else:
            reasons.pop(f, None)
        if not same_value(f, getattr(row, f), r.value):
            diffs.append({"field": f, "before": plain(getattr(row, f)), "after": plain(r.value),
                          "resolved_by": r.resolved_by, "reason": r.reason})
    flags = {str(x) for x in row.flags} - {Flag.NEEDS_HUMAN.value, Flag.CROSS_MODEL_DISAGREE.value}
    if disputed:
        flags.add(Flag.CROSS_MODEL_DISAGREE.value)
    if stats["unresolved"]:
        flags.add(Flag.NEEDS_HUMAN.value)
    d.update(evidence=evidence, unknown_reason=reasons, flags=sorted(flags), pass2_diff=diffs,
             confidence=round(mean(r.confidence for r in results.values()), 4),
             meta={"pass": 2, "run_id": ctx.run_id,
                   "model": f"{row.meta.model} | verify {ctx.verify_model} | judge {ctx.judge_model}",
                   "prompt_version": f"{row.meta.prompt_version}+{ctx.judge.version}+{ctx.rer.version}",
                   "updated_at": utc_now()})
    new, _ = enforce_evidence(AppResult.model_validate(d))
    new = apply_rules(apply_grounding(new, ground_row(new, augmented)))
    save_bundle(augmented.model_copy(update={"run_id": ctx.run_id, "app_id": row.id, "app": row.app}))
    stats["changed"] = len(diffs)
    return new, stats


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m agent.verify", description="Pass-2 verification loops.")
    ap.add_argument("--input", default=None, help="default results/pass1.json")
    ap.add_argument("--output", default=None, help="default results/pass2.json")
    ap.add_argument("--loops", default=",".join(LOOPS))
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--ids", help="comma-separated subset of app ids")
    ap.add_argument("--concurrency", type=int, default=4)
    return ap


def default_clients(settings, logger: RunLogger, budget: BudgetGuard, run_id: str) -> tuple[Any, Any]:
    from agent.run import default_clients as clients
    return clients(settings, logger, budget, run_id)


async def run_verify(rows: list[AppResult], apps: dict[int, AppSeed], ctx: VerifyContext, *, budget: BudgetGuard,
                     out_path: Path, resume: bool, concurrency: int) -> dict:
    existing = {r.id: r for r in load_results(out_path)}
    todo = [r for r in rows if not (resume and r.id in existing)]
    summary: dict[str, Any] = {"done": 0, "skipped": len(rows) - len(todo), "errored": [], "budget_stopped": False,
                               "changed": 0, "unresolved": 0, "judged": 0, "tool_calls": 0}
    sem, lock, stop = asyncio.Semaphore(max(1, concurrency)), asyncio.Lock(), asyncio.Event()
    fatal: list[ConfigError] = []

    async def one(row: AppResult) -> None:
        async with sem:
            if stop.is_set():
                return
            try:
                budget.check()
                new, stats = await verify_row(row, load_bundle(row.meta.run_id, row.id), apps[row.id], ctx)
            except BudgetExceeded as e:
                summary["budget_stopped"] = True
                stop.set()
                print(f"  id={row.id} not started: {e}")
                return
            except ConfigError as e:
                fatal.append(e)
                stop.set()
                return
            except Exception as e:  # one row's failure must not kill the run; --resume retries it
                ctx.logger.append({"kind": "app_error", "run_id": ctx.run_id, "app_id": row.id, "stage": "verify",
                                   "status": "error",
                                   "error": redact(f"{type(e).__name__}: {e}", ctx.logger.secrets)[:300]})
                summary["errored"].append(row.id)
                print(f"  id={row.id} status=error ({type(e).__name__})")
                return
            async with lock:
                existing[new.id] = new
                write_results_atomic(out_path, existing.values())
                summary["done"] += 1
                for k in ("changed", "unresolved", "judged", "tool_calls"):
                    summary[k] += stats[k]
                print(f"[{summary['done']}/{len(todo)}] id={row.id} disputed={stats['disputed']} "
                      f"judged={stats['judged']} tool_calls={stats['tool_calls']} unresolved={stats['unresolved']} "
                      f"changed={stats['changed']} spent=${budget.spent:.4f}")

    try:
        await asyncio.gather(*(one(r) for r in todo))
    finally:
        closer = getattr(ctx.llm, "aclose", None)
        if closer:
            await closer()
    if fatal:
        raise fatal[0]
    return summary


def main(argv: list[str] | None = None, *, clients_factory: Callable | None = None) -> int:
    from agent.run import _utf8_stdout
    _utf8_stdout()
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:
        return 0 if e.code in (0, None) else 2
    loops = {x.strip() for x in args.loops.split(",") if x.strip()}
    if not loops or loops - set(LOOPS):
        print(f"--loops must be a subset of {','.join(LOOPS)}")
        return 2
    in_path = Path(args.input) if args.input else config.RESULTS_DIR / "pass1.json"
    out_path = Path(args.output) if args.output else config.RESULTS_DIR / "pass2.json"
    if not in_path.exists():
        print(f"not found: {in_path}")
        return 2
    rows = load_results(in_path)
    if args.ids:
        try:
            wanted = {int(x) for x in args.ids.split(",")}
        except ValueError:
            print(f"--ids must be comma-separated integers, got '{args.ids}'")
            return 2
        rows = [r for r in rows if r.id in wanted]
    try:
        settings = load_settings(need_tools="reresearch" in loops)
        for var, value in (("VERIFY_MODEL", settings.verify_model), ("JUDGE_MODEL", settings.judge_model)):
            if not value:
                raise ConfigError(var, "not set")
    except ConfigError as e:
        print(config_error_message(e))
        return 3
    if settings.verify_model.split("/")[0] == settings.pass1_model.split("/")[0]:
        print(f"WARNING: VERIFY_MODEL shares a provider family with PASS1_MODEL ({settings.verify_model}).")
    run_id = new_run_id()
    logger = RunLogger(config.RUN_LOG, secrets=secret_values(settings))
    budget = BudgetGuard(settings.budget_cap_usd, config.RUN_LOG)
    print(f"verify {run_id}: {len(rows)} row(s) · loops={','.join(x for x in LOOPS if x in loops)} · "
          f"verify={settings.verify_model} · judge={settings.judge_model} · out={out_path} · "
          f"cap=${settings.budget_cap_usd:.2f} (spent so far ${budget.spent:.4f})")
    try:
        llm, tools = (clients_factory or default_clients)(settings, logger, budget, run_id)
        ctx = VerifyContext(llm=llm, tools=tools, logger=logger, verify_model=settings.verify_model,
                            judge_model=settings.judge_model, run_id=run_id, loops=loops, prompt=load_prompt(),
                            judge=judge_prompt(), rer=reresearch_prompt())
        summary = asyncio.run(run_verify(rows, {a.id: a for a in load_apps()}, ctx, budget=budget,
                                         out_path=out_path, resume=args.resume, concurrency=args.concurrency))
    except ConfigError as e:
        print(config_error_message(e))
        return 3
    print(f"done={summary['done']} skipped={summary['skipped']} errored={summary['errored'] or 0} "
          f"changed_fields={summary['changed']} unresolved_fields={summary['unresolved']} "
          f"judged_fields={summary['judged']} reresearch_tool_calls={summary['tool_calls']}")
    print(render(summarize(read_log(config.RUN_LOG), run_id=run_id)))
    if summary["budget_stopped"]:
        print(f"Budget cap reached (BUDGET_CAP_USD=${settings.budget_cap_usd:.2f}). Progress is saved; "
              "raise the cap deliberately and rerun with --resume.")
        return 4
    if summary["errored"]:
        print(f"{len(summary['errored'])} row(s) errored: {summary['errored']}. Rerun with --resume to retry them.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
