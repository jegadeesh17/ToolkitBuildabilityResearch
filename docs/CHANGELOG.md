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
- Offline test suite (199 tests; network blocked, no keys needed).
- `docs/SPEC.md`: authoritative specification (requirements, schema, pipeline, verification loops, accuracy method, Definition of Done).
- Project scaffolding: `.env.example`, `.gitignore`, `CLAUDE.md`, `requirements.txt` (pinned).
- Living docs: `docs/architecture.md`, `docs/CHANGELOG.md`, `docs/reference/README.md`.
- `.claude/hooks/stop_hook.py`: runs the offline test suite when a Claude Code turn ends; no-op until tests exist.
- `data/apps.json`: the 100 apps (10 categories × 10) to research.
