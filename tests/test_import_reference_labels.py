import json

from agent.schema import GroundTruth
from agent.store import load_apps, load_split
from scripts.import_reference_labels import convert, main

SAMPLE = load_split("sample")
HINTS = {a.id: a.hint for a in load_apps()}
NAMES = {a.id: a.app for a in load_apps()}


def label(i, **over):
    d = {"id": i, "app": NAMES.get(i, "X"), "auth_methods": ["api_key"], "access_model": "self_serve_free",
         "api_type": ["rest"], "api_breadth": "broad", "existing_mcp": "none_found", "verdict": "buildable_now",
         "blocker": "none", "verdict_reason": "Free developer keys.",
         "evidence": {"verdict": {"url": f"https://docs.example.com/{i}", "quote": "Anyone can create a key."},
                      "auth_methods": {"url": f"https://docs.example.com/{i}/auth", "quote": "Use an API key."}}}
    d.update(over)
    return d


def test_valid_doc_converts_to_ground_truth():
    gt, ev, problems = convert({"labeller": "agent:gemini-cli (pro)", "labels": [label(i) for i in SAMPLE]},
                               SAMPLE, HINTS)
    assert problems == [] and len(gt["labels"]) == 20
    GroundTruth.model_validate(gt)
    first = gt["labels"][0]
    assert first["source_url"] == f"https://docs.example.com/{first['id']}"
    assert first["extra_urls"] == [f"https://docs.example.com/{first['id']}/auth"]
    assert ev["apps"][str(first["id"])]["evidence"]["verdict"]["quote"] == "Anyone can create a key."
    assert gt["labeller"].startswith("agent:gemini")


def test_problems_are_reported():
    labels = [label(i) for i in SAMPLE[1:]] + [label(SAMPLE[2]), label(999), label(SAMPLE[0], verdict="maybe")]
    _, _, problems = convert({"labels": labels}, SAMPLE, HINTS)
    text = " ".join(problems)
    assert "duplicate" in text and "not a sample app" in text and "verdict" in text


def test_label_without_evidence_gets_vendor_home_and_note():
    gt, _, problems = convert({"labels": [label(i, evidence={}) if i == SAMPLE[0] else label(i) for i in SAMPLE]},
                              SAMPLE, HINTS)
    assert problems == []
    lbl = gt["labels"][0]
    assert lbl["source_url"].startswith("https://") and "no evidence URL" in lbl["notes"]


def test_main_writes_files_or_refuses(tmp_path, monkeypatch):
    import agent.config as config
    monkeypatch.setattr(config, "VERIFICATION_DIR", tmp_path)
    good = tmp_path / "in.json"
    good.write_text(json.dumps({"labeller": "agent:x", "labels": [label(i) for i in SAMPLE]}), encoding="utf-8")
    assert main(["--input", str(good)]) == 0
    assert len(json.loads((tmp_path / "ground_truth.json").read_text(encoding="utf-8"))["labels"]) == 20
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"labels": [label(i) for i in SAMPLE[:5]]}), encoding="utf-8")
    (tmp_path / "ground_truth.json").unlink()
    assert main(["--input", str(bad)]) == 5 and not (tmp_path / "ground_truth.json").exists()
    assert main(["--input", str(tmp_path / "missing.json")]) == 2
