"""Accuracy against the blind human labels (SPEC §2.6).

7 scored fields × sample apps. Single-value fields: exact match. Set fields: correct iff the sets are
equal (Jaccard reported too). A predicted `unknown` is never correct (it counts as an abstention).
Items whose human label is `unknown` are excluded and counted. Every hit and miss is listed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from agent import config
from agent.schema import SCORED_FIELDS, SET_FIELDS, UNKNOWN, AppResult, GroundTruth, GroundTruthLabel
from agent.store import load_results, load_split, write_json_atomic

BUCKETS = ((0.0, 0.5, "0.0-0.5"), (0.5, 0.7, "0.5-0.7"), (0.7, 0.9, "0.7-0.9"), (0.9, 1.01, "0.9-1.0"))


def load_ground_truth(path: Path) -> dict[int, GroundTruthLabel]:
    gt = GroundTruth.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
    out: dict[int, GroundTruthLabel] = {}
    for label in gt.labels:
        if label.id in out:
            raise ValueError(f"duplicate ground-truth label for id {label.id}")
        out[label.id] = label
    return out


def _plain(v: Any) -> Any:
    if isinstance(v, list):
        return [str(x) for x in v]
    return str(v) if v is not None else None


def field_correct(field: str, pred: Any, truth: Any) -> bool:
    if pred == UNKNOWN or pred is None or pred == "missing":
        return False
    if field in SET_FIELDS:
        return isinstance(pred, list) and isinstance(truth, list) and set(map(str, pred)) == set(map(str, truth))
    return str(pred) == str(truth)


def jaccard(pred: Any, truth: Any) -> float:
    if not isinstance(pred, list) or not isinstance(truth, list):
        return 0.0
    a, b = set(map(str, pred)), set(map(str, truth))
    return len(a & b) / len(a | b) if a | b else 0.0


def _metrics(items: list[dict]) -> dict:
    n = len(items)
    correct = sum(i["correct"] for i in items)
    answered = sum(i["answered"] for i in items)
    return {"n": n, "correct": correct, "answered": answered,
            "accuracy_overall": round(correct / n, 4) if n else None,
            "accuracy_when_answered": round(correct / answered, 4) if answered else None,
            "abstain_rate": round((n - answered) / n, 4) if n else None}


def score(rows: list[AppResult], truth: dict[int, GroundTruthLabel], sample_ids: list[int]) -> dict:
    by_id = {r.id: r for r in rows}
    items: list[dict] = []
    excluded = 0
    missing: list[int] = []
    jac: dict[str, list[float]] = {f: [] for f in SET_FIELDS}
    for sid in sorted(sample_ids):
        label = truth.get(sid)
        if label is None:
            continue
        row = by_id.get(sid)
        if row is None:
            missing.append(sid)
        for f in SCORED_FIELDS:
            t = getattr(label, f)
            if t == UNKNOWN:
                excluded += 1
                continue
            p = getattr(row, f) if row is not None else "missing"
            ok = field_correct(f, p, t)
            items.append({"id": sid, "app": label.app, "field": f, "pred": _plain(p), "truth": _plain(t),
                          "confidence": row.confidence if row is not None else None, "correct": ok,
                          "answered": p not in (UNKNOWN, "missing")})
            if f in SET_FIELDS:
                jac[f].append(jaccard(p, t))
    calibration = []
    for lo, hi, name in BUCKETS:
        b = [i for i in items if i["confidence"] is not None and lo <= i["confidence"] < hi]
        c = sum(i["correct"] for i in b)
        calibration.append({"bucket": name, "n": len(b), "correct": c, "accuracy": round(c / len(b), 4) if b else None})

    def listed(ok: bool) -> list[dict]:
        return [{k: i[k] for k in ("id", "app", "field", "pred", "truth", "confidence")} for i in items
                if i["correct"] == ok]

    scored_rows = [by_id[s] for s in sample_ids if s in by_id and s in truth]
    return {
        "pass": scored_rows[0].meta.pass_ if scored_rows else None,
        "n_apps": len([s for s in sample_ids if s in truth]),
        "n_items": len(items),
        "n_excluded_truth_unknown": excluded,
        "overall": _metrics(items),
        "per_field": {f: _metrics([i for i in items if i["field"] == f]) for f in SCORED_FIELDS},
        "jaccard": {f: round(sum(v) / len(v), 4) if v else None for f, v in jac.items()},
        "hits": listed(True),
        "misses": listed(False),
        "missing_rows": missing,
        "calibration": calibration,
        "prompt_versions": sorted({r.meta.prompt_version for r in scored_rows}),
        "models": sorted({r.meta.model for r in scored_rows}),
    }


def compare(r1: dict, r2: dict, tuned_on_sample: bool = False) -> dict:
    def acc(m: dict) -> float:
        return m["accuracy_overall"] or 0.0

    def delta(a: dict, b: dict) -> dict:
        return {"pass1": a["accuracy_overall"], "pass2": b["accuracy_overall"], "delta": round(acc(b) - acc(a), 4)}

    miss1 = {(m["id"], m["field"]) for m in r1["misses"]}
    miss2 = {(m["id"], m["field"]) for m in r2["misses"]}
    return {
        "overall": delta(r1["overall"], r2["overall"]),
        "per_field": {f: delta(r1["per_field"][f], r2["per_field"][f]) for f in SCORED_FIELDS},
        "fixed": [{"id": i, "field": f} for i, f in sorted(miss1 - miss2)],
        "regressed": [{"id": i, "field": f} for i, f in sorted(miss2 - miss1)],
        "pass1_misses": r1["misses"],
        "pass2_misses": r2["misses"],
        "n": r2["overall"]["n"],
        "tuned_on_sample": tuned_on_sample,
    }


def _print_table(rep: dict) -> None:
    print(f"{'FIELD':<14} {'N':>4} {'ACC':>7} {'ACC|ANS':>8} {'ABSTAIN':>8}")
    for name, m in [*rep["per_field"].items(), ("OVERALL", rep["overall"])]:
        fmt = (lambda v: "  n/a" if v is None else f"{v:.1%}")  # noqa: E731
        print(f"{name:<14} {m['n']:>4} {fmt(m['accuracy_overall']):>7} {fmt(m['accuracy_when_answered']):>8} "
              f"{fmt(m['abstain_rate']):>8}")
    print(f"n_apps={rep['n_apps']} · items={rep['n_items']} · "
          f"excluded (human unknown)={rep['n_excluded_truth_unknown']} · misses={len(rep['misses'])} · "
          f"missing rows={rep['missing_rows']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agent.score", description=__doc__)
    ap.add_argument("--input", default=str(config.RESULTS_DIR / "pass1.json"))
    ap.add_argument("--ground-truth", default=str(config.VERIFICATION_DIR / "ground_truth.json"))
    ap.add_argument("--compare", nargs=2, metavar=("PASS1", "PASS2"))
    ap.add_argument("--tuned-on-sample", action="store_true")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code in (0, None) else 2
    gt_path = Path(args.ground_truth)
    if not gt_path.exists():
        print(f"Ground truth not found ({gt_path}). Label the sample with verification/label.html first.")
        return 3
    truth = load_ground_truth(gt_path)
    sha = hashlib.sha256(gt_path.read_bytes()).hexdigest()
    sample = load_split("sample")
    if args.compare:
        r1 = score(load_results(Path(args.compare[0])), truth, sample)
        r2 = score(load_results(Path(args.compare[1])), truth, sample)
        out = compare(r1, r2, args.tuned_on_sample) | {"ground_truth_sha256": sha}
        write_json_atomic(config.VERIFICATION_DIR / "compare.json", out)
        o = out["overall"]
        print(f"overall accuracy pass1 {o['pass1']} -> pass2 {o['pass2']} (delta {o['delta']:+.4f}, n={out['n']}); "
              f"fixed {len(out['fixed'])}, regressed {len(out['regressed'])}")
        return 0
    path = Path(args.input)
    if not path.exists():
        print(f"not found: {path}")
        return 2
    rep = score(load_results(path), truth, sample) | {"ground_truth_sha256": sha, "input": path.as_posix()}
    write_json_atomic(config.VERIFICATION_DIR / f"score_report_pass{rep['pass'] or 1}.json", rep)
    _print_table(rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
