import json
from collections import Counter
from pathlib import Path

from scripts.make_splits import main, make_splits

APPS = json.loads(Path("data/apps.json").read_text(encoding="utf-8"))
CAT = {a["id"]: a["category"] for a in APPS}


def test_sample_is_20_two_per_category():
    sample, _ = make_splits(APPS, 20260924)
    assert len(sample) == 20 == len(set(sample))
    assert set(Counter(CAT[i] for i in sample).values()) == {2}


def test_pilot_is_10_one_per_category_and_disjoint():
    sample, pilot = make_splits(APPS, 20260924)
    assert len(pilot) == 10
    assert set(Counter(CAT[i] for i in pilot).values()) == {1}
    assert not set(sample) & set(pilot)


def test_reproducible_and_seed_sensitive():
    assert make_splits(APPS, 20260924) == make_splits(APPS, 20260924)
    assert make_splits(APPS, 1) != make_splits(APPS, 20260924)


def test_main_writes_files(tmp_path):
    assert main(["--seed", "20260924", "--out-dir", str(tmp_path)]) == 0
    s = json.loads((tmp_path / "sample.json").read_text(encoding="utf-8"))
    p = json.loads((tmp_path / "pilot.json").read_text(encoding="utf-8"))
    assert s == {"seed": 20260924, "ids": make_splits(APPS, 20260924)[0]}
    assert p["seed"] == 20260924 and len(p["ids"]) == 10


def test_committed_files_match_seed():
    s = json.loads(Path("data/sample.json").read_text(encoding="utf-8"))
    p = json.loads(Path("data/pilot.json").read_text(encoding="utf-8"))
    assert (s["ids"], p["ids"]) == make_splits(APPS, s["seed"])
