"""Import reference labels produced by an independent labelling agent (kept outside this repo so it works blind).

Validates every label against the ground-truth schema, then writes:
  verification/ground_truth.json      — the scoring input (GroundTruth), labeller recorded
  verification/reference_evidence.json — per-app verdict reason + {url, quote} per field (spot check, page)
Exit 0 ok · 2 input missing · 5 validation problems.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError  # noqa: E402

from agent import config  # noqa: E402
from agent.schema import SCORED_FIELDS, GroundTruth, GroundTruthLabel  # noqa: E402
from agent.store import load_apps, load_split, write_json_atomic  # noqa: E402

DEFAULT_INPUT = ROOT.parent / "ToolkitLabelling" / "reference_labels.json"


def _evidence(raw: dict) -> dict[str, dict]:
    out = {}
    for field, ev in (raw.get("evidence") or {}).items():
        if isinstance(ev, list):
            ev = ev[0] if ev else None
        if isinstance(ev, dict) and str(ev.get("url", "")).startswith(("http://", "https://")):
            out[field] = {"url": ev["url"].strip(), "quote": " ".join(str(ev.get("quote", "")).split())[:600]}
    return out


def convert(doc: dict, sample_ids: list[int], hints: dict[int, str]) -> tuple[dict, dict, list[str]]:
    problems: list[str] = []
    labels, evidence_out = [], {}
    seen = set()
    for i, raw in enumerate(doc.get("labels") or []):
        rid = raw.get("id")
        if rid not in sample_ids:
            problems.append(f"label[{i}]: id {rid!r} is not a sample app")
            continue
        if rid in seen:
            problems.append(f"id {rid}: duplicate label")
            continue
        seen.add(rid)
        ev = _evidence(raw)
        urls = [e["url"] for e in ev.values()]
        source = ev.get("verdict", {}).get("url") or (urls[0] if urls else None)
        home = hints.get(rid, "").split()[0] if hints.get(rid) else ""
        note_bits = [str(raw.get("verdict_reason") or "").strip(), str(raw.get("notes") or "").strip()]
        if source is None:
            source = f"https://{home}" if "." in home else "https://example.invalid/"
            note_bits.append("no evidence URL given by the labeller")
        try:
            label = GroundTruthLabel.model_validate({
                "id": rid, "app": raw.get("app", ""), **{f: raw.get(f, "unknown") for f in SCORED_FIELDS},
                "source_url": source, "extra_urls": sorted(set(urls) - {source}),
                "notes": " | ".join(b for b in note_bits if b)[:1000]})
        except ValidationError as e:
            err = e.errors()[0]
            problems.append(f"id {rid}: {'.'.join(str(x) for x in err['loc'])}: {err['msg']}")
            continue
        labels.append(label.model_dump(mode="json"))
        evidence_out[str(rid)] = {"app": label.app, "verdict": str(label.verdict),
                                  "verdict_reason": str(raw.get("verdict_reason") or ""), "evidence": ev}
    missing = sorted(set(sample_ids) - seen)
    if missing:
        problems.append(f"missing labels for sample ids {missing}")
    gt = {"created_at": doc.get("created_at") or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
          "labeller": str(doc.get("labeller") or "agent:unknown"), "blind": True,
          "labels": sorted(labels, key=lambda x: x["id"])}
    GroundTruth.model_validate(gt)
    return gt, {"labeller": gt["labeller"], "apps": evidence_out}, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--allow-partial", action="store_true", help="write valid labels even if some are missing")
    args = ap.parse_args(argv)
    src = Path(args.input)
    if not src.exists():
        print(f"not found: {src}")
        return 2
    try:
        doc = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"invalid JSON in {src}: {e.msg} (line {e.lineno})")
        return 5
    hints = {a.id: a.hint for a in load_apps()}
    gt, evidence, problems = convert(doc, load_split("sample"), hints)
    for p in problems:
        print(f"  - {p}")
    if problems and not args.allow_partial:
        print(f"FAIL: {len(problems)} problem(s); nothing written")
        return 5
    write_json_atomic(config.VERIFICATION_DIR / "ground_truth.json", gt)
    write_json_atomic(config.VERIFICATION_DIR / "reference_evidence.json", evidence)
    print(f"OK: {len(gt['labels'])} labels by {gt['labeller']} → verification/ground_truth.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
