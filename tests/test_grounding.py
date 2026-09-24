from agent.grounding import apply_grounding, canonical_url, ground_row, normalize
from agent.schema import Flag

PAGE = ("Authentication — Use **OAuth 2.0** or a [private app](https://x.com/pa) token. "
        "“API keys” are deprecated.")


def ev(field, url, quote):
    return {field: [{"url": url, "quote": quote}]}


def test_normalize_collapses_case_space_punct():
    assert normalize("  OAuth 2.0 —  Tokens! ") == "oauth 2 0 tokens"


def test_markdown_link_target_removed():
    assert "x com" not in normalize("a [private app](https://x.com/pa) token")


def test_canonical_url_variants_match():
    a = canonical_url("https://www.Example.com/docs/api/#auth")
    assert a == canonical_url("http://example.com/docs/api") == canonical_url("https://example.com/docs/api/")
    assert canonical_url("https://ex.com/a?v=2") != canonical_url("https://ex.com/a?v=3")


def test_quote_with_formatting_differences_grounds(make_row, make_bundle):
    row = make_row(evidence=ev("auth_methods", "https://docs.ex.com/auth/",
                               'Use OAuth 2.0 or a private app token. "API keys" are deprecated'))
    rep = ground_row(row, make_bundle([("https://docs.ex.com/auth", PAGE)]))
    assert rep.checks[0].ok and rep.rate == 1.0 and not rep.failed_fields


def test_url_not_in_bundle_fails(make_row, make_bundle):
    row = make_row(evidence=ev("auth_methods", "https://other.com/x", "Use OAuth 2.0 or a private"))
    rep = ground_row(row, make_bundle([("https://docs.ex.com/auth", PAGE)]))
    assert rep.checks[0].reason == "url_not_in_bundle" and rep.failed_fields == {"auth_methods"}


def test_paraphrase_fails(make_row, make_bundle):
    row = make_row(evidence=ev("auth_methods", "https://docs.ex.com/auth", "OAuth 2.0 is the only method"))
    rep = ground_row(row, make_bundle([("https://docs.ex.com/auth", PAGE)]))
    assert rep.checks[0].reason == "quote_not_found" and rep.rate == 0.0


def test_final_url_after_redirect_counts(make_row, make_bundle):
    bundle = make_bundle([("http://ex.com/a", PAGE, "https://docs.ex.com/b")])
    for cited in ("http://ex.com/a", "https://docs.ex.com/b"):
        row = make_row(evidence=ev("auth_methods", cited, "Use OAuth 2.0 or a private app token"))
        assert ground_row(row, bundle).checks[0].ok


def test_rate_counts_every_claim(make_row, make_bundle):
    row = make_row(evidence={"auth_methods": [
        {"url": "https://docs.ex.com/auth", "quote": "Use OAuth 2.0 or a private app token"},
        {"url": "https://docs.ex.com/auth", "quote": "SAML is the only supported login"}]})
    rep = ground_row(row, make_bundle([("https://docs.ex.com/auth", PAGE)]))
    assert (rep.total, rep.grounded, rep.rate) == (2, 1, 0.5) and rep.failed_fields == {"auth_methods"}


def test_rate_none_when_no_claims(make_row, make_bundle):
    assert ground_row(make_row(evidence={}), make_bundle([])).rate is None


def test_apply_grounding_sets_and_clears_flag(make_row, make_bundle):
    bad = make_row(evidence=ev("verdict", "https://nope.com", "twelve chars quote"))
    assert Flag.GROUNDING_FAILED in apply_grounding(bad, ground_row(bad, make_bundle([]))).flags
    good = make_row(flags=["grounding_failed"], evidence={})
    assert Flag.GROUNDING_FAILED not in apply_grounding(good, ground_row(good, make_bundle([]))).flags
