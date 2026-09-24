"""Spend and reliability report from results/runs/run_log.jsonl (SPEC §2.10)."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Literal

from agent import config
from agent.observability import read_log


def _group() -> dict:
    return {"cost_usd": 0.0, "llm_calls": 0, "tool_calls": 0, "retries": 0, "failures": 0}


def summarize(records: list[dict], by: Literal["stage", "model"] = "stage", run_id: str | None = None) -> dict:
    recs = [r for r in records if run_id is None or r.get("run_id") == run_id]
    out = {"total_cost_usd": 0.0, "llm_calls": 0, "tool_calls": 0, "retries": 0, "failures": 0,
           "schema_invalid_rate": None, "estimated_cost_lines": 0, "by": by, "groups": {}}
    valid = invalid = 0
    for r in recs:
        kind = r.get("kind")
        if kind == "llm_validation":
            valid += bool(r.get("schema_valid"))
            invalid += not r.get("schema_valid")
            continue
        if kind not in ("llm", "tool", "app_error"):
            continue
        key = str(r.get("stage") or "?") if by == "stage" else (
            "(tool)" if kind == "tool" else str(r.get("model") or "?"))
        g = out["groups"].setdefault(key, _group())
        failed = kind == "app_error" or r.get("status") == "error"
        cost = float(r.get("cost_usd") or 0) if kind == "llm" else 0.0
        for target in (out, g):
            target["failures"] += failed
            if kind == "app_error":
                continue
            target["retries"] += int(r.get("retries") or 0)
            target["llm_calls" if kind == "llm" else "tool_calls"] += 1
        out["total_cost_usd"] += cost
        g["cost_usd"] += cost
        out["estimated_cost_lines"] += bool(kind == "llm" and r.get("cost_estimated"))
    if valid + invalid:
        out["schema_invalid_rate"] = round(invalid / (valid + invalid), 4)
    out["total_cost_usd"] = round(out["total_cost_usd"], 6)
    for g in out["groups"].values():
        g["cost_usd"] = round(g["cost_usd"], 6)
    return out


def render(s: dict) -> str:
    lines = [f"{s['by'].upper():<34} {'COST USD':>10} {'LLM':>6} {'TOOL':>6} {'RETRY':>6} {'FAIL':>6}"]
    for key, g in sorted(s["groups"].items()):
        lines.append(f"{key:<34} {g['cost_usd']:>10.4f} {g['llm_calls']:>6} {g['tool_calls']:>6} "
                     f"{g['retries']:>6} {g['failures']:>6}")
    rate = "n/a" if s["schema_invalid_rate"] is None else f"{s['schema_invalid_rate']:.1%}"
    lines.append(f"TOTAL ${s['total_cost_usd']:.4f} · {s['llm_calls']} LLM calls · {s['tool_calls']} tool calls · "
                 f"{s['retries']} retries · {s['failures']} failures · schema-invalid {rate}"
                 + (f" · {s['estimated_cost_lines']} estimated-cost lines" if s["estimated_cost_lines"] else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--by", choices=("stage", "model"), default="stage")
    ap.add_argument("--run-id")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    s = summarize(read_log(config.RUN_LOG), by=args.by, run_id=args.run_id)
    print(json.dumps(s, indent=1) if args.json else render(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
