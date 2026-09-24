"""Deterministic consistency rules (SPEC §2.3) and the evidence-or-unknown guardrail.

Pure functions over `AppResult`; no model or tool calls.
"""
from __future__ import annotations

from dataclasses import dataclass

from agent.schema import (
    REASONABLE_FIELDS,
    SCORED_FIELDS,
    UNKNOWN,
    AccessModel,
    ApiType,
    AppResult,
    Blocker,
    Flag,
    UnknownReason,
    Verdict,
)

GATED_ACCESS = {AccessModel.PAID_PLAN_REQUIRED, AccessModel.ADMIN_OR_APPROVAL, AccessModel.PARTNER_OR_SALES}
SELF_SERVE = {AccessModel.SELF_SERVE_FREE, AccessModel.SELF_SERVE_TRIAL}


@dataclass(frozen=True)
class Violation:
    rule: int
    message: str


def unknown_fields(row: AppResult) -> list[str]:
    return [f for f in REASONABLE_FIELDS if getattr(row, f) == UNKNOWN]


def evidence_gaps(row: AppResult) -> list[str]:
    """Scored fields that claim a value but cite no evidence."""
    return [f for f in SCORED_FIELDS if not row.is_unknown(f) and not row.evidence.get(f)]


def check_rules(row: AppResult) -> list[Violation]:
    out: list[Violation] = []
    if row.api_type == [ApiType.NONE_PUBLIC] and row.verdict != Verdict.BLOCKED:
        out.append(Violation(1, "api_type none_public requires verdict blocked"))
    if row.verdict == Verdict.BUILDABLE_NOW and (row.blocker != Blocker.NONE or row.access_model not in SELF_SERVE):
        out.append(Violation(2, "buildable_now requires blocker none and self-serve access"))
    if row.access_model in GATED_ACCESS and row.verdict not in {Verdict.BUILDABLE_GATED, Verdict.BLOCKED}:
        out.append(Violation(3, f"access_model {row.access_model} requires verdict buildable_gated or blocked"))
    if row.blocker == Blocker.NONE and row.verdict not in {Verdict.BUILDABLE_NOW, Verdict.UNKNOWN}:
        out.append(Violation(4, "blocker none requires verdict buildable_now or unknown"))
    missing = [f for f in unknown_fields(row) if f not in row.unknown_reason]
    if missing:
        out.append(Violation(5, f"unknown without unknown_reason: {', '.join(missing)}"))
    return out


def _with(row: AppResult, **update) -> AppResult:
    return AppResult.model_validate({**row.to_json_dict(), **update})


def apply_rules(row: AppResult) -> AppResult:
    """Set or clear the rule_violation flag to match check_rules."""
    flags = {str(f) for f in row.flags} - {Flag.RULE_VIOLATION.value}
    if check_rules(row):
        flags.add(Flag.RULE_VIOLATION.value)
    return _with(row, flags=sorted(flags))


def enforce_evidence(row: AppResult) -> tuple[AppResult, list[str]]:
    """Guardrail: a scored field with no evidence becomes `unknown` / model_unsure."""
    gaps = evidence_gaps(row)
    if not gaps:
        return row, []
    reasons = {k: str(v) for k, v in row.unknown_reason.items()}
    update: dict = {}
    for f in gaps:
        update[f] = UNKNOWN
        reasons.setdefault(f, UnknownReason.MODEL_UNSURE.value)
    return _with(row, **update, unknown_reason=reasons), gaps
