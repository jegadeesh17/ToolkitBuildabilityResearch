import json

import pytest

from agent.cost_report import main, render, summarize

RECS = [
    {"kind": "llm", "run_id": "r1", "stage": "pilot", "model": "a", "cost_usd": 0.01, "retries": 1, "status": "ok"},
    {"kind": "llm", "run_id": "r2", "stage": "pass1", "model": "b", "cost_usd": 0.02, "retries": 0,
     "status": "error"},
    {"kind": "tool", "run_id": "r2", "stage": "pass1", "model": None, "cost_usd": None, "retries": 2,
     "status": "ok"},
    {"kind": "llm_validation", "run_id": "r2", "stage": "pass1", "model": "b", "schema_valid": False},
    {"kind": "llm_validation", "run_id": "r2", "stage": "pass1", "model": "b", "schema_valid": True},
    {"kind": "app_error", "run_id": "r2", "stage": "pass1"},
    {"kind": "llm", "run_id": "r2", "stage": "pass1", "model": "b", "cost_usd": 0.03, "retries": 0, "status": "ok",
     "cost_estimated": True},
]


def test_totals():
    s = summarize(RECS)
    assert s["total_cost_usd"] == pytest.approx(0.06) and s["llm_calls"] == 3 and s["tool_calls"] == 1
    assert s["retries"] == 3 and s["failures"] == 2 and s["schema_invalid_rate"] == 0.5
    assert s["estimated_cost_lines"] == 1


def test_group_by_stage_and_model():
    assert summarize(RECS, by="stage")["groups"]["pass1"]["cost_usd"] == pytest.approx(0.05)
    by_model = summarize(RECS, by="model")["groups"]
    assert by_model["a"]["llm_calls"] == 1 and by_model["(tool)"]["tool_calls"] == 1


def test_filter_by_run_id():
    s = summarize(RECS, run_id="r1")
    assert s["total_cost_usd"] == pytest.approx(0.01) and s["llm_calls"] == 1


def test_empty_log():
    s = summarize([])
    assert s["total_cost_usd"] == 0 and s["schema_invalid_rate"] is None


def test_render_mentions_total():
    assert "$0.0600" in render(summarize(RECS))


def test_cli_json(tmp_path, monkeypatch, capsys):
    p = tmp_path / "l.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in RECS), encoding="utf-8")
    monkeypatch.setattr("agent.config.RUN_LOG", p)
    assert main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["llm_calls"] == 3
