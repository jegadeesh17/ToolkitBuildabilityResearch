"""Validity gate for a results file (SPEC §2.9): schema, ids, evidence, unknown reasons, rule flags.

python -m agent.validate PATH [--expect N]  → exit 0 ok · 5 problems found · 2 usage / missing file
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent.rules import check_rules, evidence_gaps, unknown_fields
from agent.schema import AppResult, AppSeed, Flag
from agent.store import load_apps


def validate_rows(raw: Any, apps: list[AppSeed], expect: int | None) -> list[str]:
    if not isinstance(raw, list):
        return ["top level must be a JSON array of AppResult rows"]
    seeds = {a.id: a for a in apps}
    problems: list[str] = []
    rows: list[AppResult] = []
    for i, item in enumerate(raw):
        try:
            rows.append(AppResult.model_validate(item))
        except ValidationError as e:
            err = e.errors()[0]
            rid = item.get("id") if isinstance(item, dict) else "?"
            problems.append(f"row[{i}] id={rid}: {'.'.join(str(x) for x in err['loc'])}: {err['msg']}")
    seen: set[int] = set()
    for r in rows:
        if r.id in seen:
            problems.append(f"id={r.id}: duplicate row")
        seen.add(r.id)
        seed = seeds.get(r.id)
        if seed is None:
            problems.append(f"id={r.id}: not in data/apps.json")
        elif (seed.app, seed.category) != (r.app, r.category):
            problems.append(f"id={r.id}: app/category mismatch with data/apps.json")
        for f in evidence_gaps(r):
            problems.append(f"id={r.id}: {f} has a value but no evidence")
        for f in unknown_fields(r):
            if f not in r.unknown_reason:
                problems.append(f"id={r.id}: {f} is unknown with no unknown_reason")
        if check_rules(r) and Flag.RULE_VIOLATION not in r.flags:
            problems.append(f"id={r.id}: violates rules {[v.rule for v in check_rules(r)]} without rule_violation flag")
    passes = {r.meta.pass_ for r in rows}
    if len(passes) > 1:
        problems.append(f"mixed meta.pass values: {sorted(passes)}")
    if expect is not None and len(raw) != expect:
        problems.append(f"expected {expect} rows, found {len(raw)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agent.validate", description=__doc__)
    ap.add_argument("path")
    ap.add_argument("--expect", type=int)
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code in (0, None) else 2
    path = Path(args.path)
    if not path.exists():
        print(f"not found: {path}")
        return 2
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"FAIL: {path} is not valid JSON ({e.msg} at line {e.lineno})")
        return 5
    problems = validate_rows(raw, load_apps(), args.expect)
    for p in problems[:50]:
        print(f"  - {p}")
    if len(problems) > 50:
        print(f"  … {len(problems) - 50} more")
    n = len(raw) if isinstance(raw, list) else 0
    print(f"{'OK' if not problems else 'FAIL'}: {n} rows, {len(problems)} problems")
    return 0 if not problems else 5


if __name__ == "__main__":
    sys.exit(main())
