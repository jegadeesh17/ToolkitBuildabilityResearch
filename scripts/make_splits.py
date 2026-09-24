"""Seeded, stratified splits: 20-app held-out sample (2/category) + 10-app pilot (1/category)."""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make_splits(apps: list[dict], seed: int) -> tuple[list[int], list[int]]:
    """Return (sample_ids, pilot_ids), both sorted. Pilot is drawn from non-sample apps."""
    rng = random.Random(seed)
    by_cat: dict[str, list[int]] = defaultdict(list)
    for a in apps:
        by_cat[a["category"]].append(a["id"])
    sample: list[int] = []
    pilot: list[int] = []
    for cat in sorted(by_cat):  # fixed iteration order keeps the split reproducible
        ids = sorted(by_cat[cat])
        chosen = rng.sample(ids, 2)
        sample += chosen
        pilot += rng.sample([i for i in ids if i not in chosen], 1)
    return sorted(sample), sorted(pilot)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--apps", default=str(ROOT / "data" / "apps.json"))
    ap.add_argument("--out-dir", default=str(ROOT / "data"))
    args = ap.parse_args(argv)
    apps = json.loads(Path(args.apps).read_text(encoding="utf-8"))
    sample, pilot = make_splits(apps, args.seed)
    out = Path(args.out_dir)
    for name, ids in (("sample", sample), ("pilot", pilot)):
        payload = json.dumps({"seed": args.seed, "ids": ids}) + "\n"
        (out / f"{name}.json").write_text(payload, encoding="utf-8")
    print(f"sample={len(sample)} ids, pilot={len(pilot)} ids, seed={args.seed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
