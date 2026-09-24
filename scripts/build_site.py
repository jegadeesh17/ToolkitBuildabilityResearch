"""Build the single-page report: site/index.html + site/results.json (+ replay.json, run_log_summary.json).

Every number on the page is computed here from results files and the run log, embedded in the page as
<script type="application/json" id="results">, and published beside it as results.json (SPEC §2.8).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jinja2 import Environment, FileSystemLoader, select_autoescape  # noqa: E402

from agent import config  # noqa: E402
from agent.cost_report import summarize  # noqa: E402
from agent.observability import read_log  # noqa: E402
from agent.schema import SCORED_FIELDS  # noqa: E402

VERDICTS = ("buildable_now", "buildable_gated", "blocked", "unknown")
VERDICT_LABEL = {"buildable_now": "Buildable now", "buildable_gated": "Gated", "blocked": "Blocked",
                 "unknown": "Unknown"}
VERDICT_MARK = {"buildable_now": "✓", "buildable_gated": "◐", "blocked": "✗", "unknown": "?"}
ACCESS_LABEL = {"self_serve_free": "Self-serve, free", "self_serve_trial": "Self-serve, trial",
                "paid_plan_required": "Paid plan", "admin_or_approval": "Admin or approval",
                "partner_or_sales": "Partner or sales", "unknown": "Unknown"}
BLOCKER_LABEL = {"paid_plan_required": "Paid plan required", "approval_or_partnership": "Approval or partnership",
                 "sales_contact_only": "Sales contact only", "no_public_api": "No public API",
                 "limited_api_surface": "Limited API surface", "oauth_app_review": "OAuth app review",
                 "docs_unavailable": "Docs unavailable", "other": "Other", "none": "None", "unknown": "Unknown"}
AUTH_LABEL = {"oauth2": "OAuth 2.0", "api_key": "API key", "basic": "Basic auth", "bearer_token": "Bearer token",
              "other": "Other", "none": "No auth", "unknown": "Unknown"}
MCP_LABEL = {"official": "Official", "third_party": "Third-party", "none_found": "None found", "unknown": "Unknown"}
FIELD_LABEL = {"auth_methods": "Auth methods", "access_model": "Access model", "api_type": "API type",
               "api_breadth": "API breadth", "existing_mcp": "Existing MCP", "verdict": "Verdict",
               "blocker": "Blocker"}
EASY_BREADTH = {"moderate", "broad"}
OUTREACH_BLOCKERS = {"approval_or_partnership", "sales_contact_only"}


def _load_json(path: Path) -> Any | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _values(v: Any) -> list[str]:
    return list(v) if isinstance(v, list) else [v]


def compute_patterns(rows: list[dict]) -> dict:
    n = len(rows)
    verdicts = Counter(r["verdict"] for r in rows)
    auth = Counter(a for r in rows for a in _values(r["auth_methods"]))
    access = Counter(r["access_model"] for r in rows)
    api_type = Counter(t for r in rows for t in _values(r["api_type"]))
    mcp = Counter(r["existing_mcp"] for r in rows)
    blockers = Counter(r["blocker"] for r in rows if r["blocker"] not in ("none", "unknown"))
    by_category: dict[str, dict] = defaultdict(lambda: {"apps": [], "verdicts": Counter(), "access": Counter()})
    for r in sorted(rows, key=lambda r: r["id"]):
        c = by_category[r["category"]]
        c["apps"].append({"id": r["id"], "app": r["app"], "verdict": r["verdict"]})
        c["verdicts"][r["verdict"]] += 1
        c["access"][r["access_model"]] += 1
    easy = [{"id": r["id"], "app": r["app"]} for r in rows
            if r["verdict"] == "buildable_now" and r["api_breadth"] in EASY_BREADTH]
    outreach = [{"id": r["id"], "app": r["app"], "blocker": r["blocker"]} for r in rows
                if r["verdict"] == "buildable_gated" and r["blocker"] in OUTREACH_BLOCKERS]
    ranking = blockers.most_common()
    top = ranking[0] if ranking else (None, 0)
    takeaways = _takeaways(n, auth, access, mcp, ranking, by_category, easy, outreach, rows)

    def pct(k: int) -> float:
        return round(100 * k / n, 1) if n else 0.0
    return {
        "n": n,
        "verdict_counts": {v: verdicts.get(v, 0) for v in VERDICTS},
        "auth_counts": dict(auth.most_common()),
        "access_counts": dict(access.most_common()),
        "api_type_counts": dict(api_type.most_common()),
        "mcp_counts": {k: mcp.get(k, 0) for k in MCP_LABEL},
        "blocker_ranking": [{"blocker": b, "n": k} for b, k in ranking],
        "by_category": {cat: {"apps": c["apps"], "verdicts": {v: c["verdicts"].get(v, 0) for v in VERDICTS},
                              "access": dict(c["access"])} for cat, c in by_category.items()},
        "easy_wins": sorted(easy, key=lambda x: x["app"].lower()),
        "needs_outreach": sorted(outreach, key=lambda x: x["app"].lower()),
        "takeaways": takeaways,
        "headline": {"pct_buildable_now": pct(verdicts.get("buildable_now", 0)),
                     "pct_gated": pct(verdicts.get("buildable_gated", 0)),
                     "pct_blocked": pct(verdicts.get("blocked", 0)),
                     "pct_unknown": pct(verdicts.get("unknown", 0)),
                     "top_blocker": top[0], "top_blocker_n": top[1]},
    }


def _takeaways(n, auth, access, mcp, ranking, by_category, easy, outreach, rows) -> dict[str, str]:
    """One plain sentence per pattern, computed from the counts (never hand-written numbers)."""
    known = [(k, v) for k, v in auth.most_common() if k != "unknown"]
    both = sum(1 for r in rows if isinstance(r["auth_methods"], list)
               and {"oauth2", "api_key"} <= set(r["auth_methods"]))
    auth_s = (f"{AUTH_LABEL[known[0][0]]} ({known[0][1]}) and {AUTH_LABEL[known[1][0]]} ({known[1][1]}) dominate; "
              f"{both} apps offer both.") if len(known) >= 2 else "Auth methods could not be established."
    self_serve = access.get("self_serve_free", 0) + access.get("self_serve_trial", 0)
    gated = sum(access.get(k, 0) for k in ("paid_plan_required", "admin_or_approval", "partner_or_sales"))
    access_s = (f"{self_serve} of {n} apps let a developer get working credentials alone "
                f"({access.get('self_serve_free', 0)} free, {access.get('self_serve_trial', 0)} on a trial); "
                f"{gated} need a paid plan, an approval or a partner/sales route.")
    cats = sorted(by_category.items(), key=lambda kv: kv[0])
    open_cat = max(cats, key=lambda kv: (kv[1]["verdicts"]["buildable_now"], kv[0]))
    gated_cat = max(cats, key=lambda kv: (kv[1]["verdicts"]["buildable_gated"] + kv[1]["verdicts"]["blocked"], kv[0]))
    gated_n = gated_cat[1]["verdicts"]["buildable_gated"] + gated_cat[1]["verdicts"]["blocked"]
    cat_s = (f"Most self-serve: {open_cat[0]} ({open_cat[1]['verdicts']['buildable_now']} of "
             f"{len(open_cat[1]['apps'])} buildable now). Most gated: {gated_cat[0]} ({gated_n} of "
             f"{len(gated_cat[1]['apps'])}).")
    runner_up = (f", ahead of {BLOCKER_LABEL[ranking[1][0]].lower()} ({ranking[1][1]})."
                 if len(ranking) > 1 else ".")
    blocker_s = (f"{BLOCKER_LABEL[ranking[0][0]]} is the most common blocker ({ranking[0][1]} apps){runner_up}"
                 if ranking else "No app has a blocker.")
    none_found = mcp.get("none_found", 0)
    mcp_s = (f"{mcp.get('official', 0)} apps already have an official MCP server and {mcp.get('third_party', 0)} a "
             f"third-party one" + (f"; none was found for {none_found}." if none_found else "."))
    wins_s = (f"{len(easy)} easy wins (buildable now with a moderate or broad API) versus {len(outreach)} that "
              "need outreach (a partnership, approval or sales gate).")
    return {"auth": auth_s, "access": access_s, "categories": cat_s, "blockers": blocker_s, "mcp": mcp_s,
            "wins": wins_s}


def human_verified(spot: dict | None, pass1: list[dict], final: list[dict]) -> dict | None:
    """Agent verdicts vs verdicts a person confirmed by hand (spot check); 'can't tell' items are excluded."""
    if not spot:
        return None
    p1 = {r["id"]: r["verdict"] for r in pass1}
    p2 = {r["id"]: r["verdict"] for r in final}
    items = []
    for c in spot.get("checks") or []:
        if c.get("judgement") not in ("correct", "wrong") or "id" not in c or not c.get("reference_verdict"):
            continue
        truth = c.get("corrected_verdict") or c["reference_verdict"]
        items.append({"id": c["id"], "app": c["app"], "category": c.get("category"), "truth": truth,
                      "pass1": p1.get(c["id"]), "pass2": p2.get(c["id"]),
                      "ok1": p1.get(c["id"]) == truth, "ok2": p2.get(c["id"]) == truth})
    if not items:
        return None
    return {"n": len(items), "pass1_correct": sum(i["ok1"] for i in items),
            "pass2_correct": sum(i["ok2"] for i in items), "items": items}


def grounding_stats(rows: list[dict]) -> dict | None:
    """Claims whose quote is found on a stored page (needs results/raw bundles; None in a fresh clone)."""
    from agent.grounding import ground_row
    from agent.schema import AppResult
    from agent.store import load_bundle
    grounded = total = 0
    for r in rows:
        try:
            rep = ground_row(AppResult.model_validate(r), load_bundle(r["meta"]["run_id"], r["id"]))
        except FileNotFoundError:
            return None
        grounded, total = grounded + rep.grounded, total + rep.total
    return {"grounded": grounded, "total": total, "rate": round(grounded / total, 4) if total else None}


def run_stats(log: list[dict], pass1_rows: list[dict], final_rows: list[dict]) -> dict:
    s = summarize(log, by="stage")
    by_model = summarize(log, by="model")["groups"]
    return {"total_cost_usd": s["total_cost_usd"], "llm_calls": s["llm_calls"], "tool_calls": s["tool_calls"],
            "retries": s["retries"], "failures": s["failures"], "schema_invalid_rate": s["schema_invalid_rate"],
            "by_stage": s["groups"], "by_model": {m: g for m, g in by_model.items() if m != "(tool)"},
            "pass1_run_id": pass1_rows[0]["meta"]["run_id"] if pass1_rows else None,
            "final_run_id": final_rows[0]["meta"]["run_id"] if final_rows else None}


def spot_summary(spot: dict | None) -> dict | None:
    if not spot:
        return None
    checks = spot.get("checks") or []
    c = Counter(x.get("judgement") for x in checks)
    return {"n": len(checks), "correct": c.get("correct", 0), "wrong": c.get("wrong", 0),
            "cant_tell": c.get("cant_tell", 0), "seed": spot.get("seed"), "checks": checks}


def verification_block(scores: dict, pass1: list[dict], final: list[dict], sample_ids: list[int],
                       labeller: str | None = None, spot: dict | None = None) -> dict:
    p1, p2, cmp_ = scores.get("pass1"), scores.get("pass2"), scores.get("compare")
    needs_human = [{"id": r["id"], "app": r["app"]} for r in final if "needs_human" in r["flags"]]
    changed = sum(len(r.get("pass2_diff") or []) for r in final)
    resolved = Counter(d["resolved_by"] for r in final for d in (r.get("pass2_diff") or []))

    def slim(rep: dict | None) -> dict | None:
        if rep is None:
            return None
        return {k: rep[k] for k in ("n_apps", "n_items", "n_excluded_truth_unknown", "overall", "per_field", "hits",
                                    "misses", "calibration", "jaccard", "ground_truth_sha256") if k in rep}
    return {"status": "scored" if p1 else "pending_labels", "n_sample": len(sample_ids), "sample_ids": sample_ids,
            "labeller": labeller, "labeller_is_human": bool(labeller) and labeller.startswith("human"),
            "spot_check": spot_summary(spot), "human_verified": human_verified(spot, pass1, final),
            "pass1": slim(p1), "pass2": slim(p2),
            "compare": {k: cmp_[k] for k in ("overall", "per_field", "fixed", "regressed", "tuned_on_sample", "n")
                        if k in cmp_} if cmp_ else None,
            "flags_pass1": dict(Counter(f for r in pass1 for f in r["flags"])),
            "flags_final": dict(Counter(f for r in final for f in r["flags"])),
            "unknown_fields_pass1": sum(r[f] == "unknown" for r in pass1 for f in SCORED_FIELDS),
            "unknown_fields_final": sum(r[f] == "unknown" for r in final for f in SCORED_FIELDS),
            "needs_human": needs_human, "pass2_changed_fields": changed, "pass2_resolved_by": dict(resolved),
            "grounding_pass1": grounding_stats(pass1), "grounding_final": grounding_stats(final)}


def build_results(pass1: list[dict], final: list[dict], source: str, scores: dict, log: list[dict],
                  splits: dict, pilot: dict | None, labeller: str | None = None, spot: dict | None = None) -> dict:
    rows = sorted(final, key=lambda r: r["id"])
    stats = run_stats(log, pass1, rows)
    models = sorted({r["meta"]["model"] for r in rows})
    return {
        "meta": {"generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": source,
                 "models": models, "prompt_versions": sorted({r["meta"]["prompt_version"] for r in rows}),
                 "total_cost_usd": stats["total_cost_usd"], "calls": stats["llm_calls"] + stats["tool_calls"],
                 "llm_calls": stats["llm_calls"], "tool_calls": stats["tool_calls"], "retries": stats["retries"],
                 "failures": stats["failures"], "seed": splits.get("seed"), "n_sample": len(splits.get("sample", [])),
                 "run_stats": stats, "pilot": pilot},
        "rows": rows,
        "patterns": compute_patterns(rows),
        "verification": verification_block(scores, pass1, rows, splits.get("sample", []), labeller, spot),
    }


def replay(app_id: int, pass1: list[dict], final: list[dict], log: list[dict]) -> dict:
    """One app's recorded trace: searches, pages, model calls, pass-1 answer and pass-2 changes (no page text)."""
    p1 = next(r for r in pass1 if r["id"] == app_id)
    fin = next(r for r in final if r["id"] == app_id)
    run_ids = {p1["meta"]["run_id"], fin["meta"]["run_id"]}
    steps = [{k: x.get(k) for k in ("ts", "kind", "stage", "tool", "model", "url", "status", "latency_ms", "tokens_in",
                                    "tokens_out", "cost_usd", "schema_valid", "error")}
             for x in log if x.get("app_id") == app_id and x.get("run_id") in run_ids]
    bundle_path = config.RAW_DIR / p1["meta"]["run_id"] / f"bundle_{app_id}.json"
    bundle = _load_json(bundle_path) or {}
    return {"app_id": app_id, "app": p1["app"],
            "queries": [{"query": q["query"], "hits": [h["url"] for h in q.get("hits", [])]}
                        for q in bundle.get("queries", [])],
            "pages": [{"url": p["url"], "title": p.get("title"), "chars": len(p.get("text") or ""),
                       "error": p.get("error"), "sha256": p.get("sha256")} for p in bundle.get("pages", [])],
            "calls": steps, "pass1": p1, "final": fin}


def render_html(results: dict) -> str:
    env = Environment(loader=FileSystemLoader(str(ROOT / "scripts" / "templates")),
                      autoescape=select_autoescape(["html", "j2"]))
    env.filters["pct"] = lambda v: "n/a" if v is None else f"{100 * v:.0f}%"
    embedded = json.dumps(results, ensure_ascii=False).replace("</", "<\\/")
    return env.get_template("index.html.j2").render(
        r=results, embedded_json=embedded, fields=SCORED_FIELDS, verdicts=VERDICTS, VERDICT_LABEL=VERDICT_LABEL,
        VERDICT_MARK=VERDICT_MARK, ACCESS_LABEL=ACCESS_LABEL, BLOCKER_LABEL=BLOCKER_LABEL, AUTH_LABEL=AUTH_LABEL,
        MCP_LABEL=MCP_LABEL, FIELD_LABEL=FIELD_LABEL)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pass1", default=str(config.RESULTS_DIR / "pass1.json"))
    ap.add_argument("--pass2", default=str(config.RESULTS_DIR / "pass2.json"))
    ap.add_argument("--out-dir", default=str(ROOT / "site"))
    ap.add_argument("--replay-id", type=int, default=3, help="app id for the recorded replay (a non-sample app)")
    args = ap.parse_args(argv)
    pass1 = _load_json(Path(args.pass1))
    if not pass1:
        print(f"not found or empty: {args.pass1}")
        return 2
    pass2 = _load_json(Path(args.pass2))
    final, source = (pass2, "pass2") if pass2 and len(pass2) == len(pass1) else (pass1, "pass1")
    v = config.VERIFICATION_DIR
    scores = {"pass1": _load_json(v / "score_report_pass1.json"), "pass2": _load_json(v / "score_report_pass2.json"),
              "compare": _load_json(v / "compare.json")}
    splits = {"seed": (_load_json(config.DATA_DIR / "sample.json") or {}).get("seed"),
              "sample": (_load_json(config.DATA_DIR / "sample.json") or {}).get("ids", [])}
    if args.replay_id in splits["sample"]:
        print(f"--replay-id {args.replay_id} is a sample app; choose a non-sample app")
        return 2
    log = read_log(config.RUN_LOG)
    gt = _load_json(v / "ground_truth.json") or {}
    results = build_results(pass1, final, source, scores, log, splits,
                            _load_json(config.RESULTS_DIR / "pilot" / "compare.json"),
                            gt.get("labeller"), _load_json(v / "spot_check.json"))
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "run_log_summary.json").write_text(json.dumps(results["meta"]["run_stats"], indent=1) + "\n",
                                             encoding="utf-8")
    (out / "replay.json").write_text(json.dumps(replay(args.replay_id, pass1, final, log), ensure_ascii=False,
                                                indent=1) + "\n", encoding="utf-8")
    (out / "index.html").write_text(render_html(results), encoding="utf-8")
    print(f"wrote {out / 'index.html'} + results.json ({len(results['rows'])} rows, source={source}, "
          f"verification={results['verification']['status']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
