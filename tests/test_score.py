import json

import pytest

from agent.score import compare, field_correct, jaccard, load_ground_truth, main, score


def test_set_equality_and_jaccard():
    assert field_correct("auth_methods", ["oauth2", "api_key"], ["api_key", "oauth2"])
    assert not field_correct("auth_methods", ["oauth2"], ["oauth2", "api_key"])
    assert jaccard(["oauth2"], ["oauth2", "api_key"]) == 0.5 and jaccard("unknown", ["oauth2"]) == 0.0


def test_unknown_prediction_is_never_correct():
    assert not field_correct("verdict", "unknown", "unknown")


def test_metrics_exact(make_row, make_label):
    # App 1: all 7 correct, but truth api_breadth=unknown -> excluded -> 6 items, 6 correct.
    # App 2: truth = defaults except verdict=blocked + blocker=no_public_api;
    #        pred verdict=buildable_now (wrong), blocker=unknown (abstain) -> 7 items, 5 correct, 6 answered.
    rows = [make_row(id=1), make_row(id=2, blocker="unknown", unknown_reason={"blocker": "model_unsure"})]
    truth = {1: make_label(id=1, api_breadth="unknown"),
             2: make_label(id=2, verdict="blocked", blocker="no_public_api")}
    r = score(rows, truth, [1, 2])
    assert r["n_items"] == 13 and r["n_excluded_truth_unknown"] == 1
    assert r["overall"]["correct"] == 11 and r["overall"]["answered"] == 12
    assert r["overall"]["accuracy_overall"] == round(11 / 13, 4)
    assert r["overall"]["accuracy_when_answered"] == round(11 / 12, 4)
    assert r["overall"]["abstain_rate"] == round(1 / 13, 4)
    assert {(m["id"], m["field"]) for m in r["misses"]} == {(2, "verdict"), (2, "blocker")}
    assert len(r["hits"]) == 11
    assert r["per_field"]["verdict"] == {"n": 2, "correct": 1, "answered": 2, "accuracy_overall": 0.5,
                                         "accuracy_when_answered": 0.5, "abstain_rate": 0.0}


def test_missing_row_counts_as_misses(make_label):
    r = score([], {7: make_label(id=7)}, [7])
    assert r["missing_rows"] == [7] and r["overall"]["correct"] == 0 and r["overall"]["n"] == 7
    assert r["overall"]["accuracy_when_answered"] is None
    assert r["misses"][0]["pred"] == "missing"


def test_rows_outside_sample_are_ignored(make_row, make_label):
    r = score([make_row(id=1), make_row(id=99, verdict="blocked")], {1: make_label(id=1)}, [1])
    assert r["n_apps"] == 1 and r["overall"]["n"] == 7


def test_calibration_buckets(make_row, make_label):
    r = score([make_row(id=1, confidence=0.95)], {1: make_label(id=1)}, [1])
    top = next(b for b in r["calibration"] if b["bucket"] == "0.9-1.0")
    assert top == {"bucket": "0.9-1.0", "n": 7, "correct": 7, "accuracy": 1.0}


def test_jaccard_reported(make_row, make_label):
    r = score([make_row(id=1, auth_methods=["oauth2", "api_key"])], {1: make_label(id=1)}, [1])
    assert r["jaccard"]["auth_methods"] == 0.5 and r["jaccard"]["api_type"] == 1.0


def test_compare_reports_signed_delta(make_row, make_label):
    truth = {1: make_label(id=1)}
    r1 = score([make_row(id=1, api_breadth="narrow")], truth, [1])
    r2 = score([make_row(id=1)], truth, [1])
    c = compare(r1, r2)
    assert c["per_field"]["api_breadth"]["delta"] == 1.0
    assert c["overall"]["delta"] == pytest.approx(round(1 / 7, 4), abs=1e-4)
    assert c["fixed"] == [{"id": 1, "field": "api_breadth"}] and c["regressed"] == []
    assert c["tuned_on_sample"] is False
    assert compare(r2, r1)["regressed"] == [{"id": 1, "field": "api_breadth"}]


def test_load_ground_truth_rejects_duplicates(tmp_path, make_label):
    lbl = make_label(id=1).model_dump(mode="json")
    p = tmp_path / "gt.json"
    p.write_text(json.dumps({"created_at": "t", "blind": True, "labels": [lbl, lbl]}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_ground_truth(p)


def test_cli_missing_ground_truth_exit_3(tmp_path, capsys):
    assert main(["--ground-truth", str(tmp_path / "none.json")]) == 3
    assert "label.html" in capsys.readouterr().out


def test_cli_writes_report(tmp_path, make_row, make_label, monkeypatch):
    import agent.config as config
    monkeypatch.setattr(config, "VERIFICATION_DIR", tmp_path)
    sample = json.loads(open("data/sample.json", encoding="utf-8").read())["ids"]
    rows = [make_row(id=i).to_json_dict() for i in sample]
    labels = [make_label(id=i).model_dump(mode="json") for i in sample]
    (tmp_path / "p1.json").write_text(json.dumps(rows), encoding="utf-8")
    (tmp_path / "gt.json").write_text(json.dumps({"created_at": "t", "blind": True, "labels": labels}),
                                      encoding="utf-8")
    assert main(["--input", str(tmp_path / "p1.json"), "--ground-truth", str(tmp_path / "gt.json")]) == 0
    rep = json.loads((tmp_path / "score_report_pass1.json").read_text(encoding="utf-8"))
    assert rep["overall"]["accuracy_overall"] == 1.0 and rep["n_apps"] == 20 and rep["ground_truth_sha256"]
    assert main(["--compare", str(tmp_path / "p1.json"), str(tmp_path / "p1.json"),
                 "--ground-truth", str(tmp_path / "gt.json")]) == 0
    assert json.loads((tmp_path / "compare.json").read_text(encoding="utf-8"))["overall"]["delta"] == 0
