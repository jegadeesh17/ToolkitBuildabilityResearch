import json

import pytest

from agent.store import load_apps
from agent.validate import main, validate_rows

APPS = load_apps()


def good(make_row, n=3):
    return [make_row(id=a.id, app=a.app, category=a.category).to_json_dict() for a in APPS[:n]]


def test_good_rows_pass(make_row):
    assert validate_rows(good(make_row), APPS, 3) == []


@pytest.mark.parametrize("mutate,needle", [
    (lambda rs: rs.append(dict(rs[0])), "duplicate"),
    (lambda rs: rs.pop(), "expected 3"),
    (lambda rs: rs[0].update(evidence={}), "no evidence"),
    (lambda rs: rs[0].update(api_breadth="unknown", evidence={**rs[0]["evidence"], "api_breadth": []}),
     "no unknown_reason"),
    (lambda rs: rs[0].update(category="Wrong"), "category"),
    (lambda rs: rs[0].update(id=999), "not in data/apps.json"),
    (lambda rs: rs[0].update(blocker="paid_plan_required"), "rule_violation flag"),
    (lambda rs: rs[0].update(verdict="maybe"), "row[0]"),
    (lambda rs: rs[1]["meta"].update({"pass": 2}), "meta.pass"),
])
def test_each_problem_detected(make_row, mutate, needle):
    rs = good(make_row)
    mutate(rs)
    problems = validate_rows(rs, APPS, 3)
    assert any(needle in p for p in problems), problems


def test_flagged_rule_violation_is_allowed(make_row):
    rs = good(make_row)
    rs[0].update(blocker="paid_plan_required", flags=["rule_violation"])
    assert validate_rows(rs, APPS, 3) == []


def test_non_array_rejected():
    assert validate_rows({"rows": []}, APPS, None)


def test_main_exit_codes(tmp_path, make_row, capsys):
    assert main([str(tmp_path / "missing.json"), "--expect", "3"]) == 2
    p = tmp_path / "r.json"
    p.write_text(json.dumps(good(make_row)), encoding="utf-8")
    assert main([str(p), "--expect", "3"]) == 0
    assert "OK: 3 rows, 0 problems" in capsys.readouterr().out
    assert main([str(p), "--expect", "4"]) == 5
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main([str(bad)]) == 5
