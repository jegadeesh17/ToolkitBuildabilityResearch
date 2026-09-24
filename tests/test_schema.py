import pytest
from pydantic import ValidationError

from agent.schema import (
    SCORED_FIELDS,
    UNKNOWN,
    AppResult,
    AuthMethod,
    Evidence,
    Extraction,
    GroundTruth,
    GroundTruthLabel,
)

META = {"pass": 1, "run_id": "r", "model": "m", "prompt_version": "p1-abc", "updated_at": "t"}


def base(**over):
    d = dict(description="CRM platform", auth_methods=["oauth2"], access_model="self_serve_free",
             api_type=["rest"], api_breadth="broad", existing_mcp="official", existing_mcp_url=None,
             verdict="buildable_now", blocker="none", api_pricing_tier="free", confidence=0.8,
             evidence={f: [{"url": "https://ex.com/docs", "quote": "The API uses OAuth 2.0 tokens"}]
                       for f in SCORED_FIELDS},
             unknown_reason={})
    d.update(over)
    return d


def test_scored_fields_order():
    assert SCORED_FIELDS == ("auth_methods", "access_model", "api_type", "api_breadth",
                             "existing_mcp", "verdict", "blocker")


def test_set_field_dedupes_and_sorts_in_enum_order():
    e = Extraction.model_validate(base(auth_methods=["api_key", "oauth2", "api_key"]))
    assert e.auth_methods == [AuthMethod.OAUTH2, AuthMethod.API_KEY]


@pytest.mark.parametrize("val", ["unknown", ["unknown"]])
def test_set_field_accepts_unknown(val):
    assert Extraction.model_validate(base(auth_methods=val)).auth_methods == UNKNOWN


@pytest.mark.parametrize("field,val", [("auth_methods", []), ("auth_methods", ["none", "oauth2"]),
                                       ("api_type", ["none_public", "rest"]), ("auth_methods", ["saml"]),
                                       ("auth_methods", "oauth2")])
def test_set_field_rejects_invalid(field, val):
    with pytest.raises(ValidationError):
        Extraction.model_validate(base(**{field: val}))


def test_enum_rejects_out_of_vocab():
    with pytest.raises(ValidationError):
        Extraction.model_validate(base(verdict="maybe"))


def test_blocker_allows_unknown():  # D1
    assert Extraction.model_validate(base(blocker="unknown")).blocker == UNKNOWN


@pytest.mark.parametrize("quote", ["API", "   short    ", "x" * 601])
def test_evidence_quote_length_bounds(quote):  # D8
    with pytest.raises(ValidationError):
        Evidence(url="https://ex.com", quote=quote)


def test_evidence_url_must_be_http():
    with pytest.raises(ValidationError):
        Evidence(url="ftp://ex.com/x", quote="a long enough quote here")


def test_description_truncated_not_rejected():
    assert len(Extraction.model_validate(base(description="x" * 300)).description) <= 160


def test_confidence_clamped_and_defaulted():
    assert Extraction.model_validate(base(confidence=1.7)).confidence == 1.0
    assert Extraction.model_validate(base(confidence="high")).confidence == 0.5


def test_extraction_ignores_extra_keys_but_appresult_forbids():
    Extraction.model_validate(base(chatter="hi"))
    row = dict(base(), id=1, app="X", category="C", flags=[], meta=META)
    AppResult.model_validate(row)
    with pytest.raises(ValidationError):
        AppResult.model_validate(dict(row, chatter="hi"))


def test_meta_pass_alias_roundtrip():
    row = AppResult.model_validate(dict(base(), id=1, app="X", category="C", flags=[], meta=META))
    d = row.to_json_dict()
    assert d["meta"]["pass"] == 1 and "pass_" not in d["meta"]
    assert d["auth_methods"] == ["oauth2"] and d["verdict"] == "buildable_now"
    assert AppResult.model_validate(d) == row


def test_flags_deduped_and_sorted():
    row = AppResult.model_validate(dict(base(), id=1, app="X", category="C", meta=META,
                                        flags=["rule_violation", "needs_human", "rule_violation"]))
    assert [str(f) for f in row.flags] == ["needs_human", "rule_violation"]


def test_evidence_keys_must_be_known_fields():
    with pytest.raises(ValidationError):
        Extraction.model_validate(base(evidence={"colour": []}))


def test_unknown_reason_keys_must_be_known_fields():
    with pytest.raises(ValidationError):
        Extraction.model_validate(base(unknown_reason={"colour": "model_unsure"}))


def test_is_unknown():
    e = Extraction.model_validate(base(api_breadth="unknown", api_type="unknown"))
    assert e.is_unknown("api_breadth") and e.is_unknown("api_type") and not e.is_unknown("verdict")


LABEL = dict(id=1, app="X", auth_methods=["oauth2"], access_model="unknown", api_type=["rest"],
             api_breadth="broad", existing_mcp="none_found", verdict="buildable_gated",
             blocker="paid_plan_required", source_url="https://ex.com/docs")


def test_ground_truth_label_requires_source_url():
    with pytest.raises(ValidationError):
        GroundTruthLabel.model_validate(dict(LABEL, source_url=""))
    with pytest.raises(ValidationError):
        GroundTruthLabel.model_validate(dict(LABEL, source_url="notaurl"))


def test_ground_truth_must_be_blind():
    GroundTruth.model_validate({"created_at": "t", "blind": True, "labels": [LABEL]})
    with pytest.raises(ValidationError):
        GroundTruth.model_validate({"created_at": "t", "blind": False, "labels": [LABEL]})
