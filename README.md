# Toolkit Buildability Research

Which of 100 SaaS apps could become an AI-agent toolkit today? This repository holds a research agent that reads each app's own documentation, records how an agent would authenticate, how a developer gets credentials, what API exists and what blocks it — with a verbatim quote behind every answer — then re-checks its answers with verification loops and measures its agreement with blind reference labels for a 20-app sample (produced by an independent AI agent and spot-checked by a person).

The findings ship as one static page (`site/index.html`) plus the data behind every number (`site/results.json`).

## How it works

```
data/apps.json (100 apps, 10 categories)
  │
  ├─ PASS 1  agent/run.py → agent/pipeline.py          per app, 4–6 at a time
  │    search (4 query templates) → fetch ≤6 pages → evidence bundle
  │    → one schema-bound extraction (answers only from the pages, verbatim quotes)
  │    → schema validation + up to 2 repair calls → consistency rules → quote grounding
  │    → results/pass1.json
  │
  ├─ PASS 2  agent/verify.py
  │    L1 quote grounding · L2 second model (different family, same pages)
  │    · L3 judge on disputed fields only · L4 re-research (≤5 tool calls)
  │    → results/pass2.json (every change recorded in pass2_diff)
  │
  ├─ SCORING agent/score.py vs verification/ground_truth.json (blind reference labels, 20 apps)
  │
  └─ PAGE    scripts/build_site.py → site/index.html + site/results.json
```

- **Evidence or `unknown`.** Every answered field carries `{url, quote}`; the quote must appear on the cited page (checked deterministically). No evidence → `unknown` with a reason code.
- **Single chokepoints.** Only `agent/llm_client.py` calls the LLM provider (OpenRouter); only `agent/tools.py` calls the search/fetch tools (Composio search toolkit, slug allow-list).
- **Observability.** Every model and tool call appends one line to `results/runs/run_log.jsonl` (tokens, cost, latency, retries, errors). `agent/budget.py` refuses a call that would pass `BUDGET_CAP_USD`.
- **Measured, with the method disclosed.** 20 apps (2 per category, seeded) were labelled blind by an independent Gemini CLI agent working outside this repository (a model family not used in the pipeline), and a person spot-checked one reference verdict per category. Results are reported as agreement with these reference labels. Prompts were tuned only on a separate 10-app pilot set and frozen before scoring.

Design details and every contract: [`docs/SPEC.md`](docs/SPEC.md) · module boundaries: [`docs/architecture.md`](docs/architecture.md) · verified API contracts: [`docs/reference/README.md`](docs/reference/README.md) · history: [`docs/CHANGELOG.md`](docs/CHANGELOG.md).

## Quickstart

Python 3.13.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate elsewhere)
pip install -r requirements.txt
pytest -q                          # offline test suite — no API keys needed

cp .env.example .env               # add your own OpenRouter and Composio keys (never commit .env)
python -m agent.run --ids 1        # research one app → results/pass1.json
python -m agent.validate results/pass1.json --expect 1
```

Full run and page:

```bash
python -m agent.run --all --resume                     # pass 1 on all 100 apps
python -m agent.verify                                 # pass 2 → results/pass2.json
python -m agent.validate results/pass2.json --expect 100
python -m agent.score --compare results/pass1.json results/pass2.json   # needs verification/ground_truth.json
python -m agent.cost_report --by stage
python scripts/build_site.py                           # site/index.html + site/results.json
python -m playwright install chromium && python scripts/qa_site.py
```

Exit codes: `0` ok · `1` some apps errored (rerun with `--resume`) · `2` usage / unknown app · `3` missing config · `4` budget cap · `5` validation failure.

Bring your own keys. The OpenRouter key should carry a credit limit; the Composio key needs `tool_execution` access and no connected accounts.

## Results

See the page (`site/index.html`) or `site/results.json`: verdict counts and patterns across all 100 apps, pass 1 → pass 2 accuracy on the blind sample with every hit and miss listed, and the run statistics (models, calls, spend) taken from the run log.

## Known limitations and tradeoffs

- **Reference labels are from an AI agent, not a person.** Hand-labelling 140 fields did not fit the time available (a recorded deviation from the spec); a person spot-checked 10 of the reference verdicts. With 20 apps × 7 fields, treat the agreement figures as estimates with wide error bars.
- **Retrieval limits.** Web search plus plain page fetches miss documentation that needs JavaScript, a login or a PDF; those fields end up `unknown` with a reason.
- **Correlated evidence.** The second model reads the same evidence bundle as the first, so a retrieval miss can fool both; only re-research (≤5 tool calls per app) gathers new pages.
- **Narrow definition of "buildable now".** Public API plus credentials a developer can obtain alone (free or trial). Rate limits, deep terms-of-service review and approval timelines are out of scope.
- **Operational changes are disclosed.** A different free model was piloted when the planned one was unavailable; pass 1 was re-run once on the same evidence after two pipeline fixes found on non-sample apps. Spend against the budget cap is recorded in `docs/CHANGELOG.md`.
- **Prototype scope.** No backend, database or hosted CI; the offline test suite and the validation/QA scripts are the quality gate.
- **Independent research submission**, not affiliated with or endorsed by any company whose product is listed.

## Tests

`pytest -q` runs the offline suite (LLM and tool calls are faked; sockets to the internet are blocked in tests; no `.env` is read). `ruff check agent scripts tests` lints.

Built with Claude Code under an iterative plan → build → verify workflow; all code was reviewed and tested by the author.
