# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `docs/SPEC.md`: authoritative specification (requirements, schema, pipeline, verification loops, accuracy method, Definition of Done).
- Project scaffolding: `.env.example`, `.gitignore`, `CLAUDE.md`, `requirements.txt` (pinned).
- Living docs: `docs/architecture.md`, `docs/CHANGELOG.md`, `docs/reference/README.md`.
- `.claude/hooks/stop_hook.py`: runs the offline test suite when a Claude Code turn ends; no-op until tests exist.
- `data/apps.json`: the 100 apps (10 categories × 10) to research.
