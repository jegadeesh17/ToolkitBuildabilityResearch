# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `scripts/make_splits.py` → `data/sample.json` (20, 2/category) + `data/pilot.json` (10, 1/category), seed 20260924.
- `scripts/gen_label_tool.py` → `verification/label.html`: offline blind-labelling form (enum-enforced, autosave, rule warnings, exports `ground_truth.json`).
- `agent/schema.py` (AppResult, enums, evidence, bundles, ground truth), `agent/rules.py`, `agent/grounding.py` (L1).
- `agent/config.py`, `agent/observability.py` (append-only run log, redaction), `agent/store.py`, `agent/budget.py`.
- `agent/llm_client.py` (sole OpenRouter caller) and `agent/tools.py` (sole Composio caller, slug allowlist).
- `agent/prompts/extract.md` + `repair.md`; `agent/pipeline.py` (pass 1); `agent/run.py` CLI with `--resume` and `--bundles-from`.
- `agent/validate.py`, `agent/score.py`, `agent/cost_report.py`, `scripts/pilot_compare.py`.
- Offline test suite (208 tests; network blocked, no keys needed).
- Live contracts verified: Composio search/fetch slugs + response shapes (fixtures), OpenRouter `usage.cost`, key limits.
- 10-app pilot (`results/pilot/`): deepseek-v4-flash chosen as PASS1_MODEL (90% schema-valid first try, 99% grounded, $0.022); prompt frozen at `p1-12ca05`.

- `results/pass1.json`: pass 1 on all 100 apps — run `20260924T143612Z-929b15` (the message of commit ee7b9d1 names the run id incorrectly; this is the correct id), prompt `p1-12ca05`, deepseek-v4-flash, re-extracted from the run-1 evidence bundles after two pipeline fixes so every row uses one pipeline version. `validate --expect 100` OK; 45/700 fields unknown; grounded 778/850 (91.5%). Spend (all runs to date) $0.7498 per run log vs $0.7420 per OpenRouter key usage (1.1%).
- `agent/verify.py` (pass 2: grounding, cross-model, judge, bounded re-research, deterministic confidence, `pass2_diff`) with `prompts/judge.md` and `prompts/reresearch.md` — built early, on the milestone-1 branch, after the M2 plan was approved.

- `results/pass2.json`: pass 2 on all 100 apps (verify runs `20260924T150032Z-15c3cf` + resume `20260924T151837Z-f99e52` after a Windows file-lock crash at row 76; fixed by retrying the atomic rename). `validate --expect 100` OK; unknown fields 45 → 5; grounding-failed apps 37 → 6; rule violations 1 → 0; 142 fields changed (judge 119, re-research 23), each in `pass2_diff`; 4 apps still need a human.
- `results/runs/run_log.jsonl`: the complete append-only run log (secret-scanned) behind every spend and call count.

- `verification/ground_truth.json`: reference labels for the 20-app sample produced by an independent Gemini CLI agent (self-reported model "Gemini 3.8 Flash") working in a separate folder outside this repository, validated by `scripts/import_reference_labels.py`. **Deviation from SPEC §2.6**: the labels are not human; accuracy figures are reported as agreement with this reference. Agreement pass 1 → pass 2: 74.3% → 77.9% (n=140; fixed 11, regressed 6). No prompt or loop changed after scoring.
- Human-verified verdict check on the page: the spot check confirmed the correct verdict for 10 apps (one per category); the agent's verdict matched for 6/10 after pass 1 and 8/10 after pass 2 (misses: MongoDB Atlas unknown, iPayX gated-vs-now). Pattern cards and the hero now state each finding in one computed sentence; the page names the Composio search toolkit and discloses the absence of a browser loop and of a hosted run button.
- `scripts/gen_spot_check.py` → `verification/spot_check.html`: guided human check of the reference verdict for one sample app per category (seed 20260925).
- `scripts/build_site.py`, `scripts/templates/index.html.j2`, `scripts/qa_site.py`: the single-page report and its browser QA.

### Budget
- Pass 2 cost more than estimated (the judge, called on most apps, was ~$0.03 per call). With the owner's explicit approval, `BUDGET_CAP_USD` was raised from $4.00 to $4.50 for the pass-2 resume only. Cumulative spend $4.38 (run log), so the milestone-2 target of ≤ $4 was missed by $0.38. The OpenRouter key's own $5 limit stayed in place.

### Changed
- Extraction repair loop also triggers when values are given without evidence; a retry after an empty reply requests low reasoning effort (fixes found on non-sample apps in full run #1).
- Judge sees only the pages cited for the disputed fields, each capped at 12k chars (cost control).
- An app's four searches and its page fetches run concurrently (global Composio throttle unchanged).
- Extraction allows 8k output tokens (reasoning models); prompt adds stricter verbatim-quote guidance (tuned on pilot apps only).

### Fixed
- Exhausted LLM retries (429/5xx) now stop the app (retried by `--resume`) instead of producing an all-`unknown` row.
- Model output with evidence keyed by non-scored fields no longer fails validation (entries dropped; stored rows stay strict).
- Grounding treats `<br>`-style tags and escaped `
` as whitespace (formatting-only differences).
- `docs/SPEC.md`: authoritative specification (requirements, schema, pipeline, verification loops, accuracy method, Definition of Done).
- Project scaffolding: `.env.example`, `.gitignore`, `CLAUDE.md`, `requirements.txt` (pinned).
- Living docs: `docs/architecture.md`, `docs/CHANGELOG.md`, `docs/reference/README.md`.
- `.claude/hooks/stop_hook.py`: runs the offline test suite when a Claude Code turn ends; no-op until tests exist.
- `data/apps.json`: the 100 apps (10 categories × 10) to research.
