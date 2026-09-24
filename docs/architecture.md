# Architecture

High-level view of how the research agent, verification loops, scoring and site build fit together. Details (schema, CLI flags, exit codes, Definition of Done) live in [SPEC.md](SPEC.md).

Status: **design only**. The modules below are specified, not yet implemented; update this file as they land.

## Data flow

```
                     data/apps.json (100 apps: id, app, category, hint)
                              │
          scripts/make_splits.py --seed 20260924
                              │──► data/sample.json (20, scored)   data/pilot.json (10, smoke test)
                              ▼
 ┌──────────────── PASS 1  (agent/run.py → agent/pipeline.py, per app, asyncio sem=4) ───────────────┐
 │ 1 search   4 query templates ──► tools.py ──► Composio search (top 3 each)                        │
 │ 2 fetch    dedupe, prefer hint domain, ≤6 pages, ≤20k chars ──► evidence bundle                   │
 │                                         └─► results/raw/<run_id>/bundle_<id>.json                 │
 │ 3 extract  one schema-bound call ──► llm_client.py ──► OpenRouter (PASS1_MODEL)                   │
 │ 4 validate Pydantic (schema.py); ≤2 repair calls carrying the error                               │
 │ 5 check    rules.py (consistency) + L1 grounding                                                  │
 └──────────────────────────────────────────┬────────────────────────────────────────────────────────┘
                                            ▼
                                   results/pass1.json
                                            │
 ┌──────────────── PASS 2  (agent/verify.py) ─────────────────────────────────────────────────────────┐
 │ L1 grounding    deterministic: url in bundle, normalised quote ⊂ page text                        │
 │ L2 cross-model  VERIFY_MODEL extracts independently from the SAME bundle                           │
 │ L3 judge        JUDGE_MODEL, disputed fields only                                                  │
 │ L4 re-research  bounded tool loop (≤5 calls) for ungrounded / no-evidence / rule-violating rows     │
 │ merge           field-by-field; every change → pass2_diff; deterministic confidence (§2.7)        │
 └──────────────────────────────────────────┬────────────────────────────────────────────────────────┘
                                            ▼
                                   results/pass2.json
                     ┌──────────────────────┼─────────────────────────┐
                     ▼                      ▼                         ▼
     agent/score.py vs              agent/validate.py         scripts/build_site.py (Jinja2)
     verification/ground_truth.json   (schema+rules+evidence)   └─► site/index.html + site/results.json
     └─► verification/score_report_pass{1,2}.json, compare.json         │
                                                                         ▼
                                                        scripts/qa_site.py (Playwright) ─► results/qa/
                                                                         │
                                                                         ▼
                                                               GitHub Pages (gh-pages)
```

Every LLM and tool call, from every stage, appends one line to `results/runs/run_log.jsonl`. `budget.py` reads it before each call; `cost_report.py` and the page's run-stats panel are built from it.

## Module boundaries

| Module | Owns | Must not |
|---|---|---|
| `agent/config.py` | Load `.env`, validate required vars (exit 3), expose settings | Print or log key values |
| `agent/schema.py` | `AppResult` + enums, evidence objects, Pydantic validators | Contain business rules beyond field shape |
| `agent/rules.py` | The 5 deterministic consistency rules (§2.3) | Call models or tools |
| `agent/llm_client.py` | The ONLY OpenRouter caller: retries (tenacity), `usage` cost capture, JSON-schema response format, raw prompt/response capture, run-log line | Be bypassed by any other module |
| `agent/tools.py` | The ONLY Composio caller: slug allowlist (search/fetch only), backoff 1/2/4 s, run-log line | Execute any slug outside the allowlist |
| `agent/budget.py` | Sum logged spend; refuse a call that would start past `BUDGET_CAP_USD` (exit 4) | Estimate spend from anything but the log (+ fallback pricing) |
| `agent/observability.py` | Append-only JSONL writer, `run_id` generation | Store secrets |
| `agent/prompts/` | Versioned prompt files; `prompt_version` = short content hash | Be edited after the sample is scored without bumping the version |
| `agent/pipeline.py` | Pass-1 per-app flow (search → fetch → extract → validate → check) | Talk to APIs directly |
| `agent/verify.py` | Pass-2 loops L1–L4 and merge | Look at ground truth |
| `agent/score.py` | Accuracy vs `ground_truth.json`, hits/misses, calibration | Modify results |
| `agent/validate.py` | CLI validity gate on a results file (exit 5 on failure) | Fix data |
| `agent/run.py` | CLI entry: arg parsing (exit 2), resume, dispatch | Hold pipeline logic |
| `scripts/*` | Splits, label tool, pilot comparison, site build, site QA | Call OpenRouter/Composio except via `agent/` |

## External dependencies

| Service | Used for | Access path | Contract notes |
|---|---|---|---|
| OpenRouter | All LLM calls (pass 1, verifier, judge, re-research) | `httpx` in `llm_client.py` | [reference/README.md](reference/README.md) |
| Composio (no-auth Search toolkit) | Web search + URL fetch | `composio` SDK in `tools.py` | [reference/README.md](reference/README.md) |
| Google Fonts | Page typography (system-font fallback) | `<link>` in the built page | — |
| GitHub Pages | Hosting `site/` | `gh-pages` branch | — |

## Known architectural limitation
L2 cross-model shares the evidence bundle with pass 1, so retrieval misses can be correlated across both models. L4 re-research is the only loop that gathers new evidence. This is disclosed on the page.
