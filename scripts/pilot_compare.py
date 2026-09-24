"""Compare pass-1 models on the 10-app pilot: schema-valid rate, grounded rate, unknown rate, cost,
latency and cross-model agreement (SPEC §2.11). Reads results/pilot/*.json and the run log.
"""
from __future__ import annotations

import argparse
import glob
import itertools
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import config  # noqa: E402
from agent.grounding import ground_row  # noqa: E402
from agent.observability import read_log  # noqa: E402
from agent.schema import SCORED_FIELDS, SET_FIELDS, AppResult, Flag  # noqa: E402
from agent.store import load_bundle, load_results, write_json_atomic  # noqa: E402


def _same(field: str, a, b) -> bool:
    if field in SET_FIELDS and isinstance(a, list) and isinstance(b, list):
        return set(map(str, a)) == set(map(str, b))
    return str(a) == str(b)


def _rate(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def compare_models(rows_by_model: dict[str, list[AppResult]], log: list[dict],
                   bundle_loader: Callable | None = None) -> dict:
    loader = bundle_loader or load_bundle
    models: dict[str, dict] = {}
    for model, rows in rows_by_model.items():
        run_ids = {r.meta.run_id for r in rows}
        mine = [x for x in log if x.get("model") == model and x.get("run_id") in run_ids]
        first = {x["app_id"]: bool(x.get("schema_valid")) for x in mine
                 if x.get("kind") == "llm_validation" and not x.get("retries")}
        llm = [x for x in mine if x.get("kind") == "llm"]
        grounded = total = 0
        for r in rows:
            try:
                rep = ground_row(r, loader(r.meta.run_id, r.id))
                grounded, total = grounded + rep.grounded, total + rep.total
            except FileNotFoundError:
                total += sum(len(v) for v in r.evidence.values())
        n = len(rows)
        models[model] = {
            "n_rows": n,
            "schema_valid_rate": _rate(sum(first.values()), len(first)),
            "needs_human_rate": _rate(sum(Flag.NEEDS_HUMAN in r.flags for r in rows), n),
            "grounded_rate": _rate(grounded, total),
            "unknown_rate": _rate(sum(r.is_unknown(f) for r in rows for f in SCORED_FIELDS), 7 * n),
            "cost_usd": round(sum(float(x.get("cost_usd") or 0) for x in llm), 6),
            "mean_latency_ms": round(sum(int(x.get("latency_ms") or 0) for x in llm) / len(llm)) if llm else None,
        }
    agreement = {}
    for (ma, ra), (mb, rb) in itertools.combinations(sorted(rows_by_model.items()), 2):
        a, b = {r.id: r for r in ra}, {r.id: r for r in rb}
        pairs = [_same(f, getattr(a[i], f), getattr(b[i], f)) for i in sorted(set(a) & set(b)) for f in SCORED_FIELDS]
        agreement[f"{ma} | {mb}"] = _rate(sum(pairs), len(pairs))
    return {"models": models, "agreement": agreement}


def render_markdown(res: dict) -> str:
    def pct(v):
        return "n/a" if v is None else f"{v:.0%}"
    lines = ["| model | rows | schema-valid (1st try) | grounded | unknown | needs_human | cost $ | latency ms |",
             "|---|---|---|---|---|---|---|---|"]
    for m, s in res["models"].items():
        lines.append(f"| {m} | {s['n_rows']} | {pct(s['schema_valid_rate'])} | {pct(s['grounded_rate'])} | "
                     f"{pct(s['unknown_rate'])} | {pct(s['needs_human_rate'])} | {s['cost_usd']:.4f} | "
                     f"{s['mean_latency_ms']} |")
    for pair, v in res["agreement"].items():
        lines.append(f"\nCross-model agreement ({pair}): {pct(v)} of scored fields")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inputs", default=str(config.RESULTS_DIR / "pilot" / "*.json"))
    ap.add_argument("--json", default=str(config.RESULTS_DIR / "pilot" / "compare.json"))
    args = ap.parse_args(argv)
    files = [Path(p) for p in sorted(glob.glob(args.inputs)) if not p.endswith("compare.json")]
    if not files:
        print(f"no pilot result files match {args.inputs}")
        return 2
    rows_by_model: dict[str, list[AppResult]] = {}
    for f in files:
        for r in load_results(f):
            rows_by_model.setdefault(r.meta.model, []).append(r)
    res = compare_models(rows_by_model, read_log(config.RUN_LOG), bundle_loader=load_bundle)
    write_json_atomic(Path(args.json), res)
    print(render_markdown(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
