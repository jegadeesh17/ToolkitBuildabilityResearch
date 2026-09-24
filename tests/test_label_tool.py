import json
import re
from pathlib import Path

import pytest

from agent.schema import SCORED_FIELDS, AccessModel, AuthMethod, GroundTruth
from scripts.gen_label_tool import build_label_html, main, schema_spec

APPS = json.loads(Path("data/apps.json").read_text(encoding="utf-8"))
SAMPLE = json.loads(Path("data/sample.json").read_text(encoding="utf-8"))["ids"]


def _config(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="label-config">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def test_schema_spec_covers_all_scored_fields_with_unknown():
    spec = schema_spec()
    assert [f["name"] for f in spec["fields"]] == list(SCORED_FIELDS)
    for f in spec["fields"]:
        assert "unknown" in f["options"] and f["definition"]
    auth = next(f for f in spec["fields"] if f["name"] == "auth_methods")
    assert auth["multi"] and set(AuthMethod) <= set(auth["options"])
    acc = next(f for f in spec["fields"] if f["name"] == "access_model")
    assert not acc["multi"] and set(AccessModel) == set(acc["options"])


def test_html_embeds_exactly_the_sample_apps():
    cfg = _config(build_label_html(APPS, SAMPLE, schema_spec()))
    assert sorted(a["id"] for a in cfg["apps"]) == sorted(SAMPLE)
    assert all(set(a) == {"id", "app", "category", "hint", "home"} for a in cfg["apps"])


def test_label_html_contains_no_agent_output():  # Review Focus #5
    html = build_label_html(APPS, SAMPLE, schema_spec()).lower()
    for forbidden in ("results/", "pass1", "confidence", "run_id"):
        assert forbidden not in html
    assert "<link" not in html and 'src="http' not in html  # fully offline


def test_main_writes_file(tmp_path):
    out = tmp_path / "label.html"
    assert main(["--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").lower().startswith("<!doctype html>")


@pytest.mark.browser
def test_fill_and_export_validates_as_ground_truth(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    out = tmp_path / "label.html"
    main(["--out", str(out)])
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            pytest.skip("chromium not installed")
        page = browser.new_page(accept_downloads=True)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(out.as_uri())
        assert page.is_disabled("#export")
        page.evaluate("window.__fillAllForTest()")
        with page.expect_download() as dl:
            page.click("#export")
        data = json.loads(Path(dl.value.path()).read_text(encoding="utf-8"))
        browser.close()
    assert errors == []
    gt = GroundTruth.model_validate(data)
    assert sorted(lbl.id for lbl in gt.labels) == sorted(SAMPLE)
