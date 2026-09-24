"""Data model for one researched app (SPEC §2.3), evidence bundles, and blind ground-truth labels.

Only field *shape* lives here. Cross-field consistency rules live in `agent/rules.py`.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

UNKNOWN = "unknown"


class AuthMethod(StrEnum):
    OAUTH2 = "oauth2"
    API_KEY = "api_key"
    BASIC = "basic"
    BEARER_TOKEN = "bearer_token"
    OTHER = "other"
    NONE = "none"


class ApiType(StrEnum):
    REST = "rest"
    GRAPHQL = "graphql"
    SOAP = "soap"
    OTHER = "other"
    NONE_PUBLIC = "none_public"


class AccessModel(StrEnum):
    SELF_SERVE_FREE = "self_serve_free"
    SELF_SERVE_TRIAL = "self_serve_trial"
    PAID_PLAN_REQUIRED = "paid_plan_required"
    ADMIN_OR_APPROVAL = "admin_or_approval"
    PARTNER_OR_SALES = "partner_or_sales"
    UNKNOWN = "unknown"


class ApiBreadth(StrEnum):
    NARROW = "narrow"
    MODERATE = "moderate"
    BROAD = "broad"
    UNKNOWN = "unknown"


class ExistingMcp(StrEnum):
    OFFICIAL = "official"
    THIRD_PARTY = "third_party"
    NONE_FOUND = "none_found"
    UNKNOWN = "unknown"


class Verdict(StrEnum):
    BUILDABLE_NOW = "buildable_now"
    BUILDABLE_GATED = "buildable_gated"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class Blocker(StrEnum):
    NONE = "none"
    PAID_PLAN_REQUIRED = "paid_plan_required"
    APPROVAL_OR_PARTNERSHIP = "approval_or_partnership"
    SALES_CONTACT_ONLY = "sales_contact_only"
    NO_PUBLIC_API = "no_public_api"
    LIMITED_API_SURFACE = "limited_api_surface"
    OAUTH_APP_REVIEW = "oauth_app_review"
    DOCS_UNAVAILABLE = "docs_unavailable"
    OTHER = "other"
    UNKNOWN = "unknown"  # not in the SPEC list; needed so a failed extraction can abstain


class ApiPricingTier(StrEnum):
    FREE = "free"
    TRIAL = "trial"
    PAID_STANDARD = "paid_standard"
    ENTERPRISE_ONLY = "enterprise_only"
    UNKNOWN = "unknown"


class UnknownReason(StrEnum):
    NO_DOCS_FOUND = "no_docs_found"
    JS_ONLY_DOCS = "js_only_docs"
    CONTRADICTORY_SOURCES = "contradictory_sources"
    PAYWALLED_DOCS = "paywalled_docs"
    FETCH_FAILED = "fetch_failed"
    MODEL_UNSURE = "model_unsure"


class Flag(StrEnum):
    NEEDS_HUMAN = "needs_human"
    RULE_VIOLATION = "rule_violation"
    GROUNDING_FAILED = "grounding_failed"
    CROSS_MODEL_DISAGREE = "cross_model_disagree"


SCORED_FIELDS = (
    "auth_methods", "access_model", "api_type", "api_breadth", "existing_mcp", "verdict", "blocker",
)
SET_FIELDS = ("auth_methods", "api_type")
REASONABLE_FIELDS = SCORED_FIELDS + ("api_pricing_tier",)  # fields that may carry an unknown_reason
FIELD_ENUMS: dict[str, type[StrEnum]] = {
    "auth_methods": AuthMethod, "access_model": AccessModel, "api_type": ApiType, "api_breadth": ApiBreadth,
    "existing_mcp": ExistingMcp, "verdict": Verdict, "blocker": Blocker,
}
FIELD_DEFINITIONS: dict[str, str] = {  # SPEC §2.3, verbatim
    "auth_methods": "methods the public API supports",
    "access_model": "how a developer obtains working credentials",
    "api_type": "public API style(s)",
    "api_breadth": ("narrow = 1–2 resource types or <~15 endpoints / single-purpose API; moderate = several "
                    "resource groups, partial CRUD; broad = full CRUD across many resource groups"),
    "existing_mcp": "an MCP server for the app exists",
    "verdict": ("now = public API + self-serve credentials (free/trial); gated = public API but paid plan / "
                "approval / partnership / sales contact required; blocked = no public API, read-only, or "
                "terms forbid automation"),
    "blocker": "single primary blocker; none iff buildable_now",
}


def normalize_set(value: Any, enum_cls: type[StrEnum], exclusive: str) -> Any:
    """'unknown' | non-empty, de-duplicated list in enum order; `exclusive` must stand alone."""
    if value == UNKNOWN or value == [UNKNOWN]:
        return UNKNOWN
    if not isinstance(value, list) or not value:
        raise ValueError("must be a non-empty list or 'unknown'")
    order = list(enum_cls)
    vals = sorted({enum_cls(v) for v in value}, key=order.index)
    if enum_cls(exclusive) in vals and len(vals) > 1:
        raise ValueError(f"'{exclusive}' cannot be combined with other values")
    return vals


def _http_url(value: str) -> str:
    value = (value or "").strip()
    if not value.startswith(("http://", "https://")):
        raise ValueError("url must be http(s)")
    return value


class Evidence(BaseModel):
    url: str
    quote: str

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _http_url(v)

    @field_validator("quote")
    @classmethod
    def _quote(cls, v: str) -> str:
        v = v.strip()
        if not 12 <= len(v) <= 600:
            raise ValueError("quote must be 12-600 characters")
        return v


class ScoredFields(BaseModel):
    """The 7 scored fields; shared by agent output and human labels."""

    auth_methods: list[AuthMethod] | Literal["unknown"]
    access_model: AccessModel
    api_type: list[ApiType] | Literal["unknown"]
    api_breadth: ApiBreadth
    existing_mcp: ExistingMcp
    verdict: Verdict
    blocker: Blocker

    @field_validator("auth_methods", mode="before")
    @classmethod
    def _auth(cls, v: Any) -> Any:
        return normalize_set(v, AuthMethod, "none")

    @field_validator("api_type", mode="before")
    @classmethod
    def _api(cls, v: Any) -> Any:
        return normalize_set(v, ApiType, "none_public")

    def is_unknown(self, field: str) -> bool:
        return getattr(self, field) == UNKNOWN


class Extraction(ScoredFields):
    """What the extraction model returns (extra keys ignored)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    description: str = ""
    existing_mcp_url: str | None = None
    api_pricing_tier: ApiPricingTier = ApiPricingTier.UNKNOWN
    confidence: float = 0.5
    evidence: dict[str, list[Evidence]] = Field(default_factory=dict)
    unknown_reason: dict[str, UnknownReason] = Field(default_factory=dict)

    @field_validator("description", mode="before")
    @classmethod
    def _desc(cls, v: Any) -> str:
        v = " ".join(str(v or "").split())
        return v if len(v) <= 160 else v[:159] + "…"

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v: Any) -> float:
        try:
            return min(1.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            return 0.5

    @field_validator("existing_mcp_url", mode="before")
    @classmethod
    def _mcp_url(cls, v: Any) -> str | None:
        if not v or not str(v).strip().startswith(("http://", "https://")):
            return None
        return str(v).strip()

    @field_validator("evidence", "unknown_reason")
    @classmethod
    def _keys(cls, v: dict) -> dict:
        bad = set(v) - set(REASONABLE_FIELDS)
        if bad:
            raise ValueError(f"unknown field keys: {sorted(bad)}")
        return v


class Meta(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    pass_: int = Field(alias="pass")
    run_id: str
    model: str
    prompt_version: str
    updated_at: str


class DiffEntry(BaseModel):
    field: str
    before: Any
    after: Any
    resolved_by: str
    reason: str


class AppResult(Extraction):
    """One row of results/pass{1,2}.json."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: int
    app: str
    category: str
    flags: list[Flag] = Field(default_factory=list)
    pass2_diff: list[DiffEntry] | None = None
    meta: Meta

    @field_validator("flags")
    @classmethod
    def _flags(cls, v: list[Flag]) -> list[Flag]:
        return sorted(set(v), key=str)

    def to_json_dict(self) -> dict:
        return self.model_dump(mode="json", by_alias=True)


class Page(BaseModel):
    url: str
    final_url: str | None = None
    title: str = ""
    text: str = ""
    http_status: int | None = None
    fetched_at: str
    sha256: str = ""
    error: str | None = None
    thin: bool = False


class EvidenceBundle(BaseModel):
    app_id: int
    app: str
    run_id: str
    created_at: str
    queries: list[dict] = Field(default_factory=list)
    pages: list[Page] = Field(default_factory=list)


class AppSeed(BaseModel):
    id: int
    app: str
    category: str
    hint: str


class GroundTruthLabel(ScoredFields):
    model_config = ConfigDict(extra="forbid")

    id: int
    app: str
    source_url: str
    extra_urls: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("source_url")
    @classmethod
    def _src(cls, v: str) -> str:
        return _http_url(v)


class GroundTruth(BaseModel):
    created_at: str
    labeller: str = "human"
    blind: Literal[True]
    labels: list[GroundTruthLabel]
