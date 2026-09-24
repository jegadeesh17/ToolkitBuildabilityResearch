"""L1 grounding (SPEC §2.5): every cited quote must appear on a page stored in the evidence bundle.

Deterministic; the check never re-fetches. Formatting-only differences (case, whitespace, punctuation,
markdown link targets, typographic quotes) are normalised away before the substring test.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from agent.schema import AppResult, EvidenceBundle, Extraction, Flag

_MD_LINK = re.compile(r"\]\([^)]*\)")
_HTML_TAG = re.compile(r"<[^<>]{1,40}>")  # <br>, <br/>, <p> … are layout, not words
_ESCAPED_WS = re.compile(r"\\[nrt]")  # a literal backslash-n typed by the model for a line break
_NON_WORD = re.compile(r"[\W_]+")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = _ESCAPED_WS.sub(" ", _HTML_TAG.sub(" ", _MD_LINK.sub("]", t))).casefold()
    return " ".join(_NON_WORD.sub(" ", t).split())


def canonical_url(url: str) -> str:
    s = urlsplit((url or "").strip())
    host = (s.hostname or "").lower().removeprefix("www.")
    path = s.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, s.query, ""))


@dataclass(frozen=True)
class ClaimCheck:
    field: str
    url: str
    ok: bool
    reason: str  # ok | url_not_in_bundle | quote_not_found


@dataclass
class GroundingReport:
    checks: list[ClaimCheck] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.checks)

    @property
    def grounded(self) -> int:
        return sum(c.ok for c in self.checks)

    @property
    def rate(self) -> float | None:
        return self.grounded / self.total if self.total else None

    @property
    def failed_fields(self) -> set[str]:
        return {c.field for c in self.checks if not c.ok}


def page_index(bundle: EvidenceBundle) -> dict[str, str]:
    index: dict[str, str] = {}
    for page in bundle.pages:
        if not page.text:
            continue
        norm = normalize(page.text)
        for u in (page.url, page.final_url):
            if u:
                index[canonical_url(u)] = norm
    return index


def ground_row(row: Extraction, bundle: EvidenceBundle) -> GroundingReport:
    index = page_index(bundle)
    report = GroundingReport()
    for fname, items in row.evidence.items():
        for ev in items:
            text = index.get(canonical_url(ev.url))
            if text is None:
                report.checks.append(ClaimCheck(fname, ev.url, False, "url_not_in_bundle"))
            elif normalize(ev.quote) in text:
                report.checks.append(ClaimCheck(fname, ev.url, True, "ok"))
            else:
                report.checks.append(ClaimCheck(fname, ev.url, False, "quote_not_found"))
    return report


def apply_grounding(row: AppResult, report: GroundingReport) -> AppResult:
    """Set or clear the grounding_failed flag to match the report."""
    flags = {str(f) for f in row.flags} - {Flag.GROUNDING_FAILED.value}
    if report.failed_fields:
        flags.add(Flag.GROUNDING_FAILED.value)
    return AppResult.model_validate({**row.to_json_dict(), "flags": sorted(flags)})
