import json

import pytest

from scripts.pilot_compare import compare_models, main, render_markdown

DOC = "https://ex.com/docs"
TEXT = "The API uses OAuth 2.0 tokens for every request. " * 20
FIELDS = ("auth_methods", "access_model", "api_type", "api_breadth", "existing_mcp", "verdict", "blocker")


def _rows(make_row, model, run_id, **over2):
    meta = {"pass": 1, "run_id": run_id, "model": model, "prompt_version": "p1-abc", "updated_at": "t"}
    ev = {f: [{"url": DOC, "quote": "The API uses OAuth 2.0 tokens"}] for f in FIELDS}
    return [make_row(id=8, meta=meta, evidence=ev), make_row(id=20, meta=meta, evidence=ev, **over2)]


def _log(model, run_id, first_valid):
    out = []
    for app_id, ok in first_valid.items():
        out.append({"kind": "llm_validation", "stage": "pilot", "model": model, "run_id": run_id, "app_id": app_id,
                    "schema_valid": ok, "retries": 0})
        out.append({"kind": "llm", "stage": "pilot", "model": model, "run_id": run_id, "app_id": app_id,
                    "cost_usd": 0.002, "latency_ms": 1000})
    return out


def test_metrics(make_row, make_bundle):
    a = _rows(make_row, "free/m:free", "ra")
    b = _rows(make_row, "paid/m", "rb", api_breadth="narrow", flags=["needs_human"])
    log = _log("free/m:free", "ra", {8: False, 20: True}) + _log("paid/m", "rb", {8: True, 20: True})
    res = compare_models({"free/m:free": a, "paid/m": b}, log,
                         bundle_loader=lambda rid, aid: make_bundle([(DOC, TEXT)]))
    fa, pb = res["models"]["free/m:free"], res["models"]["paid/m"]
    assert fa["schema_valid_rate"] == 0.5 and pb["schema_valid_rate"] == 1.0
    assert fa["grounded_rate"] == 1.0 and fa["n_rows"] == 2
    assert pb["needs_human_rate"] == 0.5 and fa["unknown_rate"] == 0.0
    assert fa["cost_usd"] == pytest.approx(0.004) and fa["mean_latency_ms"] == 1000
    assert res["agreement"]["free/m:free | paid/m"] == pytest.approx(13 / 14, abs=1e-4)
    assert "free/m:free" in render_markdown(res)


def test_missing_bundle_counts_as_ungrounded(make_row):
    def loader(rid, aid):
        raise FileNotFoundError
    res = compare_models({"m": _rows(make_row, "m", "r")}, [], bundle_loader=loader)
    assert res["models"]["m"]["grounded_rate"] == 0.0 and res["models"]["m"]["schema_valid_rate"] is None


def test_cli_requires_inputs(tmp_path):
    assert main(["--inputs", str(tmp_path / "none*.json")]) == 2


def test_cli_end_to_end(tmp_path, make_row, make_bundle, monkeypatch):
    import scripts.pilot_compare as pc
    monkeypatch.setattr(pc, "load_bundle", lambda rid, aid: make_bundle([(DOC, TEXT)]))
    p = tmp_path / "m.json"
    p.write_text(json.dumps([r.to_json_dict() for r in _rows(make_row, "m", "r")]), encoding="utf-8")
    out = tmp_path / "cmp.json"
    assert main(["--inputs", str(p), "--json", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["models"]["m"]["n_rows"] == 2
