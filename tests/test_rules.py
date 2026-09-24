from agent.rules import apply_rules, check_rules, enforce_evidence, evidence_gaps, unknown_fields
from agent.schema import Flag


def rules(row):
    return sorted(v.rule for v in check_rules(row))


def test_valid_row_has_no_violations(make_row):
    assert rules(make_row()) == []


def test_rule1_none_public_requires_blocked(make_row):
    assert 1 in rules(make_row(api_type=["none_public"], verdict="buildable_gated", blocker="no_public_api",
                               access_model="paid_plan_required"))
    assert 1 not in rules(make_row(api_type=["none_public"], verdict="blocked", blocker="no_public_api",
                                   access_model="unknown", unknown_reason={"access_model": "no_docs_found"}))


def test_rule2_now_requires_none_blocker_and_self_serve(make_row):
    assert 2 in rules(make_row(blocker="paid_plan_required"))
    assert 2 in rules(make_row(access_model="partner_or_sales"))
    assert 2 in rules(make_row(access_model="unknown", unknown_reason={"access_model": "model_unsure"}))
    assert 2 not in rules(make_row(access_model="self_serve_trial"))


def test_rule3_gated_access_requires_gated_or_blocked(make_row):  # D16
    assert 3 in rules(make_row(access_model="paid_plan_required", verdict="unknown", blocker="paid_plan_required",
                               unknown_reason={"verdict": "model_unsure"}))
    assert 3 not in rules(make_row(access_model="admin_or_approval", verdict="buildable_gated",
                                   blocker="approval_or_partnership"))


def test_rule4_none_blocker_allows_now_or_unknown(make_row):
    assert 4 in rules(make_row(verdict="blocked", blocker="none", access_model="unknown",
                               unknown_reason={"access_model": "model_unsure"}))
    assert 4 not in rules(make_row(verdict="unknown", blocker="none", unknown_reason={"verdict": "model_unsure"}))


def test_rule5_unknown_needs_reason(make_row):
    assert 5 in rules(make_row(api_breadth="unknown"))
    assert unknown_fields(make_row(api_breadth="unknown")) == ["api_breadth"]
    assert 5 not in rules(make_row(api_breadth="unknown", unknown_reason={"api_breadth": "model_unsure"}))


def test_pricing_tier_unknown_needs_reason_too(make_row):
    assert "api_pricing_tier" in unknown_fields(make_row(api_pricing_tier="unknown"))


def test_apply_rules_sets_and_clears_flag(make_row):
    assert Flag.RULE_VIOLATION in apply_rules(make_row(blocker="paid_plan_required")).flags
    assert Flag.RULE_VIOLATION not in apply_rules(make_row(flags=["rule_violation"])).flags
    assert Flag.NEEDS_HUMAN in apply_rules(make_row(flags=["needs_human"])).flags


def test_enforce_evidence_converts_unsupported_fields(make_row):
    row = make_row(evidence={"auth_methods": [{"url": "https://ex.com", "quote": "OAuth 2.0 is supported"}]})
    fixed, changed = enforce_evidence(row)
    assert "access_model" in changed and "auth_methods" not in changed and len(changed) == 6
    assert fixed.access_model == "unknown" and fixed.unknown_reason["access_model"] == "model_unsure"
    assert fixed.api_type == "unknown"
    assert evidence_gaps(fixed) == []


def test_enforce_evidence_keeps_existing_reason(make_row):
    row = make_row(api_breadth="unknown", unknown_reason={"api_breadth": "js_only_docs"})
    fixed, changed = enforce_evidence(row)
    assert changed == [] and fixed.unknown_reason["api_breadth"] == "js_only_docs"
