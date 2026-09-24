import json

from agent.store import load_apps, load_split
from scripts.gen_spot_check import SPOT_SEED, build, main, pick

APPS = {a.id: a for a in load_apps()}
SAMPLE = load_split("sample")


def test_one_app_per_category_and_reproducible():
    chosen = pick(SAMPLE, {i: a.category for i, a in APPS.items()})
    assert len(chosen) == 10 and len({APPS[i].category for i in chosen}) == 10
    assert set(chosen) <= set(SAMPLE) and chosen == pick(SAMPLE, {i: a.category for i, a in APPS.items()})


def test_page_embeds_items_and_is_offline():
    html = build([{"id": 1, "app": "X", "category": "C", "verdict": "buildable_now", "reason": "r",
                   "url": "https://x.com", "quote": "q"}], "agent:test", SPOT_SEED)
    assert '"app": "X"' in html and "<link" not in html and 'src="http' not in html


def test_main_requires_reference_evidence(tmp_path, monkeypatch):
    import agent.config as config
    monkeypatch.setattr(config, "VERIFICATION_DIR", tmp_path)
    assert main(["--out", str(tmp_path / "s.html")]) == 2
    (tmp_path / "reference_evidence.json").write_text(json.dumps({"labeller": "agent:x", "apps": {}}), encoding="utf-8")
    assert main(["--out", str(tmp_path / "s.html")]) == 0 and (tmp_path / "s.html").exists()
