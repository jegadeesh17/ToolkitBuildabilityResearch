import json
import re
from pathlib import Path

import pytest

from agent.store import load_apps, load_split
from scripts.build_site import build_results, compute_patterns, main, render_html

SECTIONS = ["top", "result", "patterns", "agent", "verification", "table", "proof", "limitations"]
KINDS = [  # (verdict, access_model, blocker, api_breadth)
    ("buildable_now", "self_serve_free", "none", "broad"),
    ("buildable_now", "self_serve_trial", "none", "narrow"),
    ("buildable_gated", "partner_or_sales", "sales_contact_only", "broad"),
    ("buildable_gated", "paid_plan_required", "paid_plan_required", "moderate"),
    ("blocked", "unknown", "no_public_api", "unknown"),
]


@pytest.fixture
def rows(make_row):
    out = []
    for a in load_apps():
        verdict, access, blocker, breadth = KINDS[a.id % len(KINDS)]
        reasons = {f: "model_unsure" for f, val in (("access_model", access), ("api_breadth", breadth))
                   if val == "unknown"}
        out.append(make_row(id=a.id, app=a.app, category=a.category, verdict=verdict, access_model=access,
                            blocker=blocker, api_breadth=breadth, unknown_reason=reasons).to_json_dict())
    return out


@pytest.fixture
def results(rows):
    splits = {"seed": 20260924, "sample": load_split("sample")}
    return build_results(rows, rows, "pass1", {}, [], splits, None)


def test_patterns_match_definitions(rows):
    p = compute_patterns(rows)
    assert sum(p["verdict_counts"].values()) == 100
    easy = {x["id"] for x in p["easy_wins"]}
    assert easy == {r["id"] for r in rows
                    if r["verdict"] == "buildable_now" and r["api_breadth"] in ("moderate", "broad")}
    outreach = {x["id"] for x in p["needs_outreach"]}
    assert outreach == {r["id"] for r in rows if r["verdict"] == "buildable_gated"
                        and r["blocker"] in ("approval_or_partnership", "sales_contact_only")}
    assert p["headline"]["top_blocker"] in {"sales_contact_only", "paid_plan_required", "no_public_api"}
    assert "none" not in {b["blocker"] for b in p["blocker_ranking"]}


def test_sections_in_spec_order(results):
    html = render_html(results)
    positions = [html.index(f'id="{s}"') for s in SECTIONS]
    assert positions == sorted(positions)


def test_exactly_100_table_rows(results):
    assert render_html(results).count('<tr class="app-row"') == 100


def test_headline_numbers_recomputed_from_results(results):
    html = render_html(results)
    vc = results["patterns"]["verdict_counts"]
    n_now = sum(r["verdict"] == "buildable_now" for r in results["rows"])
    assert vc["buildable_now"] == n_now
    assert f"<h1>{n_now} of 100 SaaS apps" in html
    assert f'{round(100 * n_now / 100)}<small>%</small>' in html


def test_embedded_json_equals_results(results):
    html = render_html(results)
    m = re.search(r'<script type="application/json" id="results">(.*?)</script>', html, re.S)
    assert json.loads(m.group(1)) == json.loads(json.dumps(results))


def test_limitations_and_independence_statement(results):
    html = render_html(results)
    assert "Limitations and tradeoffs" in html and "independent research submission" in html


def test_only_external_requests_are_google_fonts(results):
    html = render_html(results)
    requested = re.findall(r'<(?:link|script|img)[^>]+(?:href|src)="(https?://[^"]+)"', html)
    assert requested and all(u.startswith(("https://fonts.googleapis.com", "https://fonts.gstatic.com"))
                             for u in requested)


def test_verdicts_never_colour_only(results):
    html = render_html(results)
    chips = re.findall(r'<span class="pill (now|gated|blocked|unknown)">([^<]+)</span>', html)
    assert chips and all(text.strip()[0] in "✓◐✗?" and len(text.strip()) > 2 for _, text in chips)


def test_pending_state_is_honest(results):
    html = render_html(results)
    assert results["verification"]["status"] == "pending_labels" and "Accuracy is pending" in html


def test_scored_state_shows_n_and_misses(results, rows):
    rep = {"n_apps": 20, "n_items": 140, "n_excluded_truth_unknown": 0,
           "overall": {"n": 140, "correct": 100, "answered": 130, "accuracy_overall": 0.7143,
                       "accuracy_when_answered": 0.7692, "abstain_rate": 0.0714},
           "per_field": {f: {"n": 20, "correct": 14, "answered": 19, "accuracy_overall": 0.7,
                             "accuracy_when_answered": 0.7368, "abstain_rate": 0.05}
                         for f in ("auth_methods", "access_model", "api_type", "api_breadth", "existing_mcp",
                                   "verdict", "blocker")},
           "hits": [], "misses": [{"id": 1, "app": "X", "field": "verdict", "pred": "blocked",
                                   "truth": "buildable_now", "confidence": 0.9}],
           "calibration": [{"bucket": "0.9-1.0", "n": 140, "correct": 100, "accuracy": 0.7143}]}
    cmp_ = {"overall": {"pass1": 0.6, "pass2": 0.7143, "delta": 0.1143}, "per_field": {}, "fixed": [],
            "regressed": [], "tuned_on_sample": False, "n": 140}
    splits = {"seed": 20260924, "sample": load_split("sample")}
    res = build_results(rows, rows, "pass2", {"pass1": rep, "pass2": rep, "compare": cmp_}, [], splits, None)
    html = render_html(res)
    assert "60% <small>→</small> 71%" in html and "n=140" in html and "Misses in the final answers (1)" in html


def test_main_writes_site_files(tmp_path, rows):
    p1 = tmp_path / "pass1.json"
    p1.write_text(json.dumps(rows), encoding="utf-8")
    out = tmp_path / "site"
    assert main(["--pass1", str(p1), "--pass2", str(tmp_path / "none.json"), "--out-dir", str(out)]) == 0
    html = (out / "index.html").read_text(encoding="utf-8")
    m = re.search(r'<script type="application/json" id="results">(.*?)</script>', html, re.S)
    assert json.loads(m.group(1)) == json.loads((out / "results.json").read_text(encoding="utf-8"))
    for name in ("replay.json", "run_log_summary.json"):
        assert json.loads((out / name).read_text(encoding="utf-8"))


def test_replay_refuses_sample_app(tmp_path, rows):
    p1 = tmp_path / "pass1.json"
    p1.write_text(json.dumps(rows), encoding="utf-8")
    sample_id = load_split("sample")[0]
    assert main(["--pass1", str(p1), "--out-dir", str(tmp_path / "s"), "--replay-id", str(sample_id)]) == 2


def test_partial_pass2_falls_back_to_pass1(tmp_path, rows):
    p1, p2 = tmp_path / "pass1.json", tmp_path / "pass2.json"
    p1.write_text(json.dumps(rows), encoding="utf-8")
    p2.write_text(json.dumps(rows[:10]), encoding="utf-8")
    out = tmp_path / "site"
    assert main(["--pass1", str(p1), "--pass2", str(p2), "--out-dir", str(out)]) == 0
    assert json.loads((out / "results.json").read_text(encoding="utf-8"))["meta"]["source"] == "pass1"


def test_site_is_not_a_python_package():
    assert not Path("site/__init__.py").exists()


def test_agent_reference_labels_are_disclosed(rows):
    splits = {"seed": 20260924, "sample": load_split("sample")}
    spot = {"seed": 7, "checks": [{"judgement": "correct"}] * 8 + [{"judgement": "wrong"}, {"judgement": "cant_tell"}]}
    res = build_results(rows, rows, "pass2", {}, [], splits, None, "agent:gemini-cli (pro)", spot)
    html = render_html(res)
    assert "independent AI research agent, gemini-cli (pro), a different model family" in html
    assert "not verified accuracy" in html
    assert "<b>8</b> correct, <b>1</b> wrong, <b>1</b> could not tell" in html
    assert res["verification"]["labeller_is_human"] is False


def test_pass1_and_pass2_are_defined_for_new_readers(results):
    html = render_html(results)
    agent = html[html.index('id="agent"'):html.index('id="verification"')]
    assert "<b>Pass 1</b> (steps 1–5)" in agent and "<b>Pass 2</b> (steps 6–8)" in agent
    assert agent.count('class="passtag">Pass 1<') == 5 and agent.count('class="passtag">Pass 2<') == 3
    hero = html[html.index('id="result"'):html.index('id="patterns"')]
    assert "Pass 1 is the agent's first answer; pass 2 is after its verification loops" in hero
    verif = html[html.index('id="verification"'):html.index('id="table"')]
    assert "Pass 1 is the agent's first answer" in verif


def test_human_needed_box_matches_who_labelled(rows):
    splits = {"seed": 20260924, "sample": load_split("sample")}
    spot = {"seed": 7, "checks": [{"judgement": "correct"}] * 10}
    agent_html = render_html(build_results(rows, rows, "pass2", {}, [], splits, None, "agent:gemini-cli (x)", spot))
    box = agent_html[agent_html.index("Where a human was needed"):agent_html.index('class="stats"')]
    assert "Labelling the 20-app sample blind" not in box and "Spot-checking" in box
    human_html = render_html(build_results(rows, rows, "pass2", {}, [], splits, None, "human", None))
    box = human_html[human_html.index("Where a human was needed"):human_html.index('class="stats"')]
    assert "Labelling the 20-app sample blind" in box


def test_takeaways_are_plainly_stated_up_top_and_on_cards(results):
    t = results["patterns"]["takeaways"]
    assert set(t) == {"auth", "access", "categories", "blockers", "mcp", "wins"}
    html = render_html(results)
    hero = html[html.index('id="result"'):html.index('id="patterns"')]
    patterns = html[html.index('id="patterns"'):html.index('id="agent"')]
    for k in ("auth", "categories", "blockers", "wins"):
        assert t[k] in hero
    for k in ("auth", "access", "blockers", "mcp", "categories", "wins"):
        assert t[k] in patterns


def test_human_verified_verdicts_are_the_headline_check(rows):
    splits = {"seed": 20260924, "sample": load_split("sample")}
    a, b = rows[0], rows[1]
    spot = {"seed": 7, "checks": [
        {"id": a["id"], "app": a["app"], "category": a["category"], "reference_verdict": a["verdict"],
         "judgement": "correct"},
        {"id": b["id"], "app": b["app"], "category": b["category"], "reference_verdict": "unknown_x",
         "judgement": "wrong", "corrected_verdict": "blocked"},
        {"id": 99, "app": "Z", "category": "C", "reference_verdict": "blocked", "judgement": "cant_tell"}]}
    res = build_results(rows, rows, "pass2", {}, [], splits, None, "agent:x", spot)
    hv = res["verification"]["human_verified"]
    expected = 1 + (b["verdict"] == "blocked")
    assert hv["n"] == 2 and hv["pass2_correct"] == expected
    html = render_html(res)
    hero = html[html.index('id="result"'):html.index('id="patterns"')]
    assert f"{expected}/2 <small>→</small> {expected}/2" in hero and "Verdicts checked by hand" in hero
    verif = html[html.index('id="verification"'):html.index('id="table"')]
    assert "Verdicts checked by hand" in verif and a["app"] in verif


def test_composio_sdk_and_new_limitations_are_stated(results):
    html = render_html(results)
    assert "Composio's search toolkit through its Python SDK" in html
    assert "There is no browser-based verification loop" in html
    assert "There is no hosted run button" in html
