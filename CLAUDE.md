# CLAUDE.md — Toolkit Buildability Research

An agent pipeline that researches 100 SaaS apps and records, with evidence quotes from their real docs, whether each could become an AI-agent toolkit today. Verification loops plus a blind human-labelled 20-app sample measure accuracy honestly; results ship as one static HTML page plus `results.json`.

`docs/SPEC.md` is authoritative. If anything here disagrees with it, the spec wins.

## Commands (run from repo root, inside `.venv`)
```
py -3.13 -m venv .venv && .venv\Scripts\activate   # Windows (source .venv/bin/activate elsewhere)
pip install -r requirements.txt
python -m playwright install chromium               # only for scripts/qa_site.py

pytest -q                                           # offline test suite; no API keys needed
ruff check agent scripts tests                      # lint

python -m agent.run --ids 1 --stage pass1           # one app (also --app NAME | --pilot | --sample | --all, --resume)
python -m agent.verify --input results/pass1.json --output results/pass2.json
python -m agent.validate results/pass1.json --expect 100
python -m agent.score --input results/pass1.json
python -m agent.cost_report --by stage
python scripts/make_splits.py --seed 20260924
python scripts/gen_label_tool.py                    # verification/label.html
python scripts/build_site.py                        # site/index.html + site/results.json
python scripts/qa_site.py                           # Playwright DOM/screenshot checks
```
Exit codes: 0 ok · 2 usage/unknown app · 3 missing config · 4 budget cap · 5 validation failure.

## Non-negotiable guardrails
1. **Zero unverified claims.** Never say "done", "passing" or "works" without showing the command output that proves it. Report failures and skipped steps plainly.
2. **Evidence or `unknown`.** Every non-`unknown` scored field needs a `{url, quote}` copied verbatim from a fetched page. Never fill a field from model memory; never make a silent best guess.
3. **Secrets isolation.** Keys live only in `.env` (gitignored). Never print, log, echo or commit key values; read them via `agent/config.py` only. Log/error messages name the variable, never its value.
4. **Single chokepoints.** Only `agent/llm_client.py` calls OpenRouter; only `agent/tools.py` calls Composio (slug allowlist). Every call is appended to `results/runs/run_log.jsonl`.
5. **Budget.** `agent/budget.py` refuses any call that would start past `BUDGET_CAP_USD`. No paid 100-app run before the 10-app pilot passes. Use free models for dev/tuning.
6. **Anti-leakage.** Tune prompts on pilot apps only. Never look at sample-app errors to change prompts or loops; if it happens, bump `prompt_version` and label it "tuned on sample" on the page.
7. **Tests stay offline.** `pytest` mocks all LLM and tool calls and must pass with no `.env`.
8. **Plan before code.** Non-trivial changes follow explore → plan → implement → verify → review. Stop after 2 failed fix attempts and ask.
9. **Don't copy or name** any third-party logo, wordmark or copy on the page (design is inspired-by only).

## Lessons from the build (retrospective, 2026-09-24)
10. **Verify credential scopes with one real call at setup**: a Composio key can list tools yet lack `tool_execution` (403); OpenRouter free models can be upstream-429 for hours. Probe before planning around them.
11. **Measure cost on a 3-app live sample before any full paid run** and set `BUDGET_CAP_USD` from it (the judge cost ~2x the estimate). Raising the cap is the owner's decision, per run, and is disclosed.
12. **Paid runs must survive local I/O errors**: on Windows, readers (IDE watchers, antivirus) briefly lock files; atomic writes retry, and a save failure must never kill a run mid-flight.
13. **Inspect `llm_validation` failures on pilot runs before the full run**: schema-valid replies with no evidence and empty replies from reasoning overruns only surfaced at scale.

## Repo etiquette
- Branches: `feature/milestone-<n>-<slug>`; never commit unverified code to `main`.
- Commits: Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`), one logical change each, tests green first.
- Never commit: `.env`, `results/raw/`, `docs/reference/ASSIGNMENT.md`, `docs/reference/HANDOFF.md`, `docs/PROJECT_MENTAL_MODEL.md`, `docs/PROJECT_STATUS.md`.
- Before any push: `git ls-files` and `git grep -nE "sk-or-v1-|OPENROUTER_API_KEY=.+"` must be clean. Pushes and `gh-pages` deploys need the user's explicit OK each time.
- Don't create a top-level Python package named `site` (shadows stdlib); the page lives in `site/` as static files only.

## CI / quality gates
- No hosted CI (out of scope). The gate is local: `pytest -q` green, `python -m agent.validate` exit 0 on any results file you produce.
- A Stop hook (`.claude/hooks/stop_hook.py`) runs `pytest -q` when Claude finishes a turn and blocks completion on failures once tests exist.
- Hosting: GitHub Pages from a `gh-pages` branch containing `site/`.

## Living docs (keep in sync at every milestone)
- `docs/SPEC.md` — requirements, schema, CLI contract, Definition of Done
- `docs/architecture.md` — module boundaries and data flow
- `docs/CHANGELOG.md` — Keep-a-Changelog, `[Unreleased]` first
- `docs/PROJECT_STATUS.md` — active milestone, checklist, resume point (local only, gitignored)
- `docs/reference/README.md` — verified OpenRouter / Composio contracts and cheatsheets
