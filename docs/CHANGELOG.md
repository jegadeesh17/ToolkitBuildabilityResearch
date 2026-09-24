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

### Changed
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
