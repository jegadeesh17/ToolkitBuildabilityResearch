# SPEC — Toolkit Buildability Research

Authoritative, self-contained specification. Any engineer or agent should be able to build the project from this file alone. Where this file and any other note disagree, this file wins.

**One-paragraph summary.** Research 100 SaaS apps (10 categories × 10, `data/apps.json`) for whether each could become an *agent toolkit* (a set of API-backed tools an AI agent can call) today. An agent pipeline collects evidence from real documentation and fills a fixed schema per app; verification loops (deterministic + cross-model + judge) then raise accuracy; a human blind-labels a 20-app held-out sample so accuracy is measured honestly, hits and misses included. The output is patterns across the 100 apps, published as ONE self-explanatory static HTML page plus `results.json`, and this repository with a README that runs the agent.

**Posture.** Prototype, time-boxed, accuracy-first on the data. Production hardening is out of scope; schema validation, evidence on every claim, cost/run observability and honest reporting are in scope.

---

# Part 1 — Product Requirements

## 1.1 Personas
1. **Reviewer (primary).** A person (or their agent) scanning for ~2 minutes with no narration. Needs insight first, then proof. Must be able to see what the agent got wrong.
2. **Operator (secondary).** The researcher, then any reviewer cloning the repo, who wants to run the agent from the README.

## 1.2 Journey A — Reviewer on the HTML page
Single page, dark theme, sections in this fixed order:

| # | Section | Reviewer sees | Interaction |
|---|---|---|---|
| 0 | Top bar | Title (no third-party wordmark), small green `RESEARCH` tag; mono-uppercase anchors `PATTERNS · AGENT · VERIFICATION · TABLE · DATA ↗` | Anchor scroll; `DATA ↗` opens `results.json` |
| 1 | Hero + headline strip | One-line result; 3 stat tiles: accuracy pass 1 → pass 2 (n=20 shown), % of 100 apps `buildable_now`, #1 blocker | none |
| 2 | Patterns | 4–5 insight cards with segmented dot-matrix bars: dominant auth method(s); access model / verdict by category (10 × tiers); blocker ranking; easy wins vs needs-outreach | hover for counts |
| 3 | The agent | Pipeline steps (search → fetch → extract → validate → verify → judge → re-research); "human needed here" callouts; run-stats panel (models, calls, total USD, retries, failures) | none |
| 4 | Verification | Pass 1 vs pass 2 accuracy bars, overall and per field; hits AND misses list; confidence calibration; "Where the agent failed / human needed" section | expand miss rows |
| 5 | Findings table | 100 rows, leaderboard-style: rank, app, category, auth chips, access model, API type/breadth, MCP, verdict chip, confidence bar | filter chips (category / auth / verdict); click row → evidence URLs+quotes, blocker, pass 1 ↔ pass 2 diff |
| 6 | Proof & reproduce | README command `python -m agent.run --ids 1`; links to `results.json`, `run_log.jsonl` (public summary), one recorded replay of a single app | links only (no live trigger) |
| 7 | Limitations | Known limitations & tradeoffs; sentence stating it is an independent research submission | none |

**Page requirements**
- Works without JavaScript: the table is rendered statically at build time from `results.json`; JS only adds filter/expand. The data is also embedded as `<script type="application/json" id="results">` and published as `results.json` beside the page.
- Semantic `<table>` for the 100 rows (agent-readable). Verdicts are never conveyed by colour alone (text label + ✓/✗).
- Every accuracy chart shows the sample size (`n=20`) and lists misses as well as hits.
- Body text contrast: nothing below `rgba(255,255,255,.60)` for content (dimmer allowed only for decoration).
- Mobile (≤768 px): bordered columns collapse to one column; table becomes stacked cards; no horizontal page scroll.
- Single self-contained HTML (inline CSS/JS); the only external requests are Google Fonts (with system-font fallbacks).

## 1.3 Design direction
Visual language inspired by Composio's public benchmark page (`composio.dev/bench`): dark, pure-black, framed with 1-px low-opacity borders. **Do not copy** any logo, wordmark, product name, copy or assets; the page must read as an independent submission.
- **Type:** Geist Sans for headings/body (large, light, tracking −0.011em); JetBrains Mono (or Geist Mono) for labels, nav, table headers (10–13 px, UPPERCASE, wide tracking, `tabular-nums`).
- **Colour:** background `#000`; borders `rgba(255,255,255,.10)`; row dividers `.06`; row hover `.03`; text white with muted `.70`; 1-px outlined pills in emerald-400 `#34d399` and blue-400 `#60a5fa`, `border-radius: 2px`; primary button white background / black mono text; accent blue `#51a2ff`, highlight `#d4ff4a`; per-series bar hues: blue, coral, yellow, orange, magenta, cyan, indigo.
- **Signature elements:** dot-matrix / dithered horizontal bars; segmented-cell bars in leaderboard rows; row grid `2.5rem | 14rem | 1fr | 6rem`; two-column bordered chart cards; a small mono footnote under each chart (e.g. `% OF 20 SAMPLED APPS · PASS 1 → PASS 2`).

## 1.4 Journey B — Operator runs the agent
`git clone` → `pip install -r requirements.txt` → `cp .env.example .env` (add OpenRouter + Composio keys) → `python -m agent.run --ids 1` (one app) or `--all` (100) → `python -m agent.verify` → `python -m agent.score` → `python scripts/build_site.py`.

## 1.5 Error handling and edge cases
| Situation | Required behaviour |
|---|---|
| Unknown app name / bad CLI arg | exit code 2, print valid apps (from `data/apps.json`) |
| Missing/invalid key or model var | exit code 3, message pointing to `.env.example`; never print key values |
| Budget cap reached (`BUDGET_CAP_USD`) | abort BEFORE the next call, exit code 4, print cost summary; runs are idempotent/resumable (`--resume` skips apps already in the output) |
| LLM output fails schema | up to 2 repair calls that include the validation error; then affected fields = `unknown` with reason, row flagged `needs_human` |
| Fetch failure / HTTP 429 / JS-only docs | backoff 1 s, 2 s, 4 s; then `unknown` + reason (`fetch_failed`, `js_only_docs`); every failure logged |
| No evidence or contradictory sources | `unknown` (or low confidence) + reason; never a silent best guess |
| Cited quote not found in fetched evidence | `grounding_failed` flag; row goes to re-research/judge |
| Pass 2 cannot resolve a row | row keeps best value at confidence 0.2 with `needs_human`; listed on the page |

## 1.6 Non-goals (MVP)
No backend, database, auth or accounts. No multi-page site, framework build step, or chat UI. No live server-side "run the agent" button (key exposure + credit burn). No apps beyond the 100 and no per-app deep-dive pages. No fields beyond those in §2.3. No production CI beyond the offline test suite and verification scripts. Not fields: rate limits, approval-process detail, last-verified date (Future Work).

---

# Part 2 — Technical Architecture

## 2.1 Stack
| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.13 | agent, verify, score, site build |
| Schema | Pydantic v2 | enums, evidence objects, cross-field validators |
| LLM access | `httpx` → OpenRouter `POST /api/v1/chat/completions` with `tenacity` retries | ALL calls via `agent/llm_client.py`; `temperature=0`; request `usage: {"include": true}` to get cost; JSON-schema `response_format` when the model supports it, otherwise "JSON only" + Pydantic validation + repair loop |
| Search / fetch | `composio` Python SDK, no-auth Search toolkit (Web Search, DuckDuckGo, Fetch URL Content) | ALL calls via `agent/tools.py` with a slug allowlist. Exact slugs and `user_id` handling are verified against Composio's docs at implementation and recorded in `docs/reference/README.md` |
| Storage | JSON / JSONL files, no database | ~100 rows |
| Concurrency | `asyncio`, semaphore 4; Composio ≈1–2 req/s | 100 apps ≈ 10–15 min (estimate) |
| Site | `scripts/build_site.py` + Jinja2 → `site/index.html`, `site/results.json` | avoid a top-level Python package named `site` (shadows stdlib) |
| Tests | pytest, fully offline (LLM and tools mocked) | must pass without any API key |
| QA | Playwright (Chromium) screenshot/DOM checks of the built page | verification of the deliverable only, not an agent loop |
| Hosting | GitHub Pages from a `gh-pages` branch containing `site/` | public repo required |

## 2.2 Repository layout
```
agent/            __init__.py  config.py  schema.py  rules.py  llm_client.py  tools.py
                  budget.py  observability.py  pipeline.py  prompts/  verify.py
                  score.py  cost_report.py  validate.py  run.py
scripts/          make_splits.py  build_site.py  gen_label_tool.py  pilot_compare.py  qa_site.py
data/             apps.json  sample.json  pilot.json
results/          pass1.json  pass2.json  runs/run_log.jsonl  raw/ (gitignored)  qa/
verification/     label.html  ground_truth.json  score_report_pass1.json  score_report_pass2.json  compare.json
site/             index.html  results.json  (template in scripts/templates/)
tests/            test_schema.py test_rules.py test_budget.py test_grounding.py test_score.py
                  test_splits.py test_llm_client.py test_tools.py test_build_site.py
docs/             SPEC.md  architecture.md  CHANGELOG.md  reference/README.md
```

## 2.3 Data schema (`agent/schema.py`)
One `AppResult` per app. Enums are fixed so patterns are countable.

| Field | Type / values | Scored | Definition |
|---|---|---|---|
| `id`, `app`, `category` | copied from `data/apps.json` | no | `category` is an INPUT, not researched |
| `description` | one line, ≤160 chars | no | what the app is |
| `auth_methods` | set of `oauth2, api_key, basic, bearer_token, other, none`; or `unknown` | ✓ | methods the public API supports |
| `access_model` | `self_serve_free, self_serve_trial, paid_plan_required, admin_or_approval, partner_or_sales, unknown` | ✓ | how a developer obtains working credentials |
| `api_type` | set of `rest, graphql, soap, other, none_public`; or `unknown` | ✓ | public API style(s) |
| `api_breadth` | `narrow, moderate, broad, unknown` | ✓ | **narrow** = 1–2 resource types or <~15 endpoints / single-purpose API; **moderate** = several resource groups, partial CRUD; **broad** = full CRUD across many resource groups |
| `existing_mcp` | `official, third_party, none_found, unknown` | ✓ | an MCP server for the app exists; `existing_mcp_url` (extra) holds its URL |
| `verdict` | `buildable_now, buildable_gated, blocked, unknown` | ✓ | **now** = public API + self-serve credentials (free/trial); **gated** = public API but paid plan / approval / partnership / sales contact required; **blocked** = no public API, read-only, or terms forbid automation |
| `blocker` | `none, paid_plan_required, approval_or_partnership, sales_contact_only, no_public_api, limited_api_surface, oauth_app_review, docs_unavailable, other` | ✓ | single primary blocker; `none` iff `buildable_now` |
| `api_pricing_tier` (extra) | `free, trial, paid_standard, enterprise_only, unknown` | no | plan level needed for API access |
| `confidence` (extra) | float 0–1 | no | see §2.7 for pass 2 |
| `existing_mcp_url` (extra) | URL or null | no | |
| `evidence` | map scored field → list of `{url, quote}`; ≥1 entry unless the field is `unknown` | grounded rate | `quote` is a verbatim snippet from the cited page |
| `unknown_reason` | map field → `no_docs_found, js_only_docs, contradictory_sources, paywalled_docs, fetch_failed, model_unsure` | no | required whenever a field is `unknown` |
| `flags` | subset of `needs_human, rule_violation, grounding_failed, cross_model_disagree` | no | |
| `pass2_diff` | list of `{field, before, after, resolved_by, reason}` | no | present in pass 2 rows |
| `meta` | `{pass, run_id, model, prompt_version, updated_at}` | no | |

**Consistency rules** (`agent/rules.py`, deterministic, applied after every extraction):
1. `api_type == [none_public]` ⇒ `verdict == blocked`.
2. `verdict == buildable_now` ⇒ `blocker == none` AND `access_model ∈ {self_serve_free, self_serve_trial}`.
3. `access_model ∈ {paid_plan_required, admin_or_approval, partner_or_sales}` ⇒ `verdict ∈ {buildable_gated, blocked}`.
4. `blocker == none` ⇒ `verdict ∈ {buildable_now, unknown}`.
5. Any `unknown` field must have an `unknown_reason`.
A violation sets `rule_violation` and routes the row to re-research/judge.

**Example row (abridged)**
```json
{"id": 2, "app": "HubSpot", "category": "CRM and Sales",
 "auth_methods": ["oauth2", "bearer_token"], "access_model": "self_serve_free",
 "api_type": ["rest"], "api_breadth": "broad", "existing_mcp": "official",
 "existing_mcp_url": "https://…", "verdict": "buildable_now", "blocker": "none",
 "confidence": 0.9,
 "evidence": {"auth_methods": [{"url": "https://developers.hubspot.com/…", "quote": "…"}]},
 "flags": [], "meta": {"pass": 1, "model": "…", "prompt_version": "p1-3fa9c2", "run_id": "…"}}
```

## 2.4 Pipeline
```
data/apps.json ─► PASS 1 (fixed pipeline, per app)
   1. search: 4 query templates via Composio (below), top 3 results each
   2. fetch: dedupe URLs, prefer domains matching the app's hint domain, fetch ≤ 6 pages,
      truncate each to 20,000 chars → EVIDENCE BUNDLE saved to results/raw/<run_id>/bundle_<id>.json
      ({url, title, text, http_status, fetched_at, sha256})
   3. extract: ONE schema-bound LLM call (worker model) over the bundle
   4. validate: Pydantic; on failure ≤2 repair calls carrying the validation error
   5. rules (§2.3) + grounding (§2.5 L1)
   ► results/pass1.json
PASS 2 (verification loops, agent/verify.py)
   L1 grounding      deterministic
   L2 cross-model    different-family model extracts independently from the SAME bundle
   L3 judge          strongest model adjudicates only disputed fields / failed rows
   L4 re-research    bounded tool loop (≤5 tool calls) ONLY for rows flagged no-evidence, ungrounded, or rule-violating
   ► results/pass2.json ─► scripts/build_site.py ─► site/index.html + site/results.json
SCORING  agent/score.py vs verification/ground_truth.json ► verification/score_report_*.json, compare.json
```
Query templates (with `{app}` and `{hint}` from `apps.json`): `{app} API documentation authentication {hint}` · `{app} API pricing plans access free trial API key {hint}` · `{app} MCP server` · `{app} API rate limits OpenAPI reference {hint}`.

**Extraction prompt requirements** (stored as versioned files in `agent/prompts/`; `prompt_version` = short hash of the file): answer ONLY from the supplied evidence, never from model memory; every non-`unknown` scored field must include ≥1 `{url, quote}` where `url` is in the bundle and `quote` is copied verbatim; if evidence is missing or contradictory return `unknown` with a reason code; use the enum definitions in §2.3 verbatim; output JSON matching the schema and nothing else.

## 2.5 Verification loops (Pass 2)
- **L1 Grounding (deterministic).** For each `{url, quote}`: URL must be in the stored bundle; the quote, after whitespace/case/punctuation normalisation, must be a substring of that page's text. Failure ⇒ `grounding_failed`. Also reports `grounded_rate` over all claims.
- **L2 Cross-model.** `VERIFY_MODEL` (different family from `PASS1_MODEL`) extracts independently from the same bundle. Any differing scored field ⇒ `cross_model_disagree` for that field.
- **L3 Judge.** `JUDGE_MODEL` receives the bundle, both extractions and only the disputed fields; returns the final value with a quote. Judged fields must pass L1.
- **L4 Re-research.** For flagged rows (`unknown`/no-evidence, `grounding_failed`, `rule_violation` not resolved by L3): a bounded tool-using loop, max 5 tool calls via `agent/tools.py`, then re-extraction and L1.
- **Merge.** Field-by-field: pass 1 == verifier and grounded ⇒ accept; disagreement ⇒ judge; ungrounded ⇒ re-research then judge. Every change is recorded in `pass2_diff`.

Known limitation to disclose: L2 shares the evidence bundle with pass 1, so retrieval misses can be correlated across the two models; L4 is the only loop that gathers new evidence.

## 2.6 Accuracy measurement
- **Sets (disjoint):** *sample* — 20 apps, 2 per category, seeded random, held out and scored; *pilot* — 10 apps drawn from the 80 non-sample apps (1 per category), smoke test only, no accuracy claim.
- **Ground truth:** a human labels the sample BLIND (before seeing any agent output) using `verification/label.html`, an offline form generated from `agent/schema.py`: a dropdown or multi-select per scored field including `unknown`, plus a required source URL and optional notes; it exports `verification/ground_truth.json`.
- **Scoring (`agent/score.py`):** 7 scored fields × 20 apps = 140 judgements. Single-value enums: exact match. Set fields (`auth_methods`, `api_type`): correct iff sets are equal (also report Jaccard). `unknown` is NOT correct. Report `accuracy_overall`, `accuracy_when_answered`, `abstain_rate`, per field, per pass, with `n` shown, and the list of every hit and miss.
- **Evidence validity:** (a) automated `grounded_rate` over all rows; (b) SHOULD: human-judged "does the cited quote support the claim" for each sampled app's `verdict` claim (20 checks), reported separately; if skipped, report (a) only and say so.
- **Confidence calibration:** accuracy by confidence bucket for the sample.
- **Anti-leakage rules:** (1) prompt and model for pass 1 are frozen before the sample is scored; any later change bumps `prompt_version` and is reported; (2) pass-2 loops are designed from general principles, never by inspecting which sample apps were wrong; any fix made after looking at sample errors is disclosed on the page as "tuned on sample" and reported separately; (3) prompts are tuned on pilot apps only.

## 2.7 Confidence in pass 2 (deterministic)
0.9 if pass 1 == verifier and grounded; 0.7 if resolved by judge and grounded; 0.4 if resolved by re-research and grounded; 0.2 if unresolved (`needs_human`).

## 2.8 Files and contracts
| Path | Written by | Read by |
|---|---|---|
| `data/apps.json` | seed (id, app, category, hint) | agent |
| `data/sample.json`, `data/pilot.json` | `scripts/make_splits.py --seed 20260924` → `{seed, ids}` | agent, score |
| `results/pass1.json`, `results/pass2.json` | agent / verify: JSON arrays of `AppResult` | validate, score, site |
| `results/runs/run_log.jsonl` | observability | cost_report, site |
| `results/raw/<run_id>/…` | wrapper: prompts, responses, bundles | verify (bundles); gitignored |
| `verification/ground_truth.json` | human via label tool | score |
| `verification/score_report_pass{1,2}.json`, `compare.json` | score | site |
| `site/index.html`, `site/results.json` | build_site | Pages |

`results.json` = `{meta: {generated_at, models, total_cost_usd, calls, retries, failures, seed, n_sample}, rows: [...], patterns: {...}, verification: {...}}`. Patterns computed at build time: auth distribution; access model and verdict by category; verdict counts; blocker ranking; **easy win** = `buildable_now` with `api_breadth ∈ {moderate, broad}`; **needs outreach** = `buildable_gated` with blocker ∈ {`approval_or_partnership`, `sales_contact_only`}. Headline: % `buildable_now`, % gated, % blocked, most common non-`none` blocker, accuracy pass 1 → pass 2.

## 2.9 CLI contract
```
python -m agent.run (--app NAME | --ids 1,2 | --pilot | --sample | --all) [--stage pass1] [--model SLUG] [--resume] [--out PATH]
python -m agent.verify [--input results/pass1.json] [--output results/pass2.json] [--loops grounding,cross,judge,reresearch]
python -m agent.validate PATH --expect N        # schema + rules + evidence/unknown-reason checks; non-zero exit on failure
python -m agent.score [--input results/pass1.json] [--compare results/pass1.json results/pass2.json]
python -m agent.cost_report [--by stage|model]
python scripts/make_splits.py --seed 20260924
python scripts/gen_label_tool.py                # writes verification/label.html
python scripts/pilot_compare.py                 # schema-valid rate, grounded rate, cost, latency, cross-model agreement per model
python scripts/build_site.py                    # writes site/index.html + site/results.json
python scripts/qa_site.py                       # Playwright checks on the built page
```
Exit codes: 0 ok · 2 usage/unknown app · 3 missing config · 4 budget cap · 5 validation failure.

**Environment (`.env`, gitignored; `.env.example` committed with placeholders):** `OPENROUTER_API_KEY`, `COMPOSIO_API_KEY`, `PASS1_MODEL`, `VERIFY_MODEL`, `JUDGE_MODEL`, `BUDGET_CAP_USD` (default `4.00`), `MODEL_PRESET` (`paid` | `free`). `.env.example` pins the exact models used for the reported results; `MODEL_PRESET=free` documents a $0 alternative.

## 2.10 Observability
- Single chokepoint: no code calls OpenRouter or Composio except `llm_client.py` / `tools.py`.
- Append-only `results/runs/run_log.jsonl`, one line per LLM call and per tool call: `kind (llm|tool), run_id, ts, app_id, stage (pilot|pass1|verify|judge|reresearch), model, prompt_version, tokens_in, tokens_out, cost_usd, latency_ms, retries, status, error, schema_valid, tool, url, http_status, raw_path`.
- `cost_usd` comes from OpenRouter usage accounting (`usage.cost`); verify the field name against a live response and fall back to token counts × published price if absent.
- `budget.py` sums the log and refuses a call that would start past `BUDGET_CAP_USD`.
- `cost_report.py` prints spend by stage/model, calls, retries, failures, schema-invalid rate.
- Reconcile once against the OpenRouter Activity dashboard; totals must be within ~10%.
- The page's run-stats panel is generated from this log.

## 2.11 Models and credits
Free models for development and prompt/schema tuning; paid credits only for reported runs; every reported number traces to a logged run. A 10-app pilot on `data/pilot.json` compares a free model against `deepseek/deepseek-v4-flash` (schema-valid rate, grounded rate, cost, latency, cross-model agreement) and picks the pass-1 worker. Verifier: a different-family model (candidates `openai/gpt-5.6-luna`, `qwen/qwen3.7-plus`; escalate to `deepseek/deepseek-v4-pro` only if needed). Judge: `anthropic/claude-sonnet-5`, disputed fields only. Estimated total spend $2–4 (back-of-envelope, to be measured). Guardrails: $5 credit limit on the API key, `BUDGET_CAP_USD` in code, ≥$2 kept in reserve, no full 100-app paid run before the pilot passes. Free `:free` models are limited to 20 requests/minute and 1,000/day for accounts that have purchased ≥$10 of credit (per OpenRouter docs); confirm via `GET /api/v1/key`.

## 2.12 Security and secrets
Keys only in `.env` (gitignored), never printed, logged or committed. The Composio project is dedicated, has no connected third-party accounts, and the tool allowlist rejects any slug outside the search/fetch tools. Composio and OpenRouter keys are deleted/rotated after submission. The README tells reviewers to bring their own keys.

## 2.13 Infrastructure provisioning checklist
| # | Item | Owner | When |
|---|---|---|---|
| 1 | OpenRouter key with $5 credit limit; verify `limit` / `limit_remaining` via `GET /api/v1/key` | human | before first paid run |
| 2 | Composio API key in a dedicated project, no connected accounts | human | before first live call |
| 3 | Rename `.env` variables to those in §2.9; write `.env.example` (values never printed) | agent | scaffolding |
| 4 | `pip install composio`; pin all dependencies in `requirements.txt`; verify tool slugs and call signature with one live call | agent | scaffolding |
| 5 | Confirm free-model daily cap (`free_model_daily_requests`) | agent | pilot |
| 6 | Public GitHub repo, `gh-pages` branch, enable Pages via API (only with the human's approval per push) | agent + human | deploy |
| 7 | Verify `.gitignore` covers `.env`, `results/raw/`, private notes; nothing private ever committed | agent | scaffolding + final audit |
| 8 | Blind-label the 20 sample apps with `verification/label.html` (~1–1.5 h) | human | as soon as the label tool exists |
| 9 | Reconcile cost log against the OpenRouter Activity dashboard | human + agent | end of Milestone 1 |
| — | No database, Vercel, Firecrawl or Playwright account needed | — | — |

## 2.14 Risks
Composio search/fetch quality or rate limits worse than assumed (mitigation: two search tools + fetch, log every failure, reason codes). Free models unreliable at strict JSON (mitigation: repair loop; pilot decides). Human labelling is the critical-path human task; delay affects the accuracy number, not the agent runs. JS-only documentation sites may defeat plain fetch (disclose on the page; counted in `unknown_reason`).

---

# Part 3 — Definition of Done

All commands run from the repo root. `pytest` must pass offline with no API keys.

## Milestone 0 — Scaffold (gate before any paid call)
| Check | Command | Pass criterion |
|---|---|---|
| Offline tests | `pytest -q` | exit 0; covers schema, rules, budget guard, grounding, scoring, splits, wrappers with mocks |
| Splits | `python scripts/make_splits.py --seed 20260924 && pytest tests/test_splits.py` | sample = 20 ids (2/category), pilot = 10 ids (1/category), disjoint, reproducible from the seed |
| Label tool | `python scripts/gen_label_tool.py` | `verification/label.html` opens offline, enforces enums, exports valid `ground_truth.json` |
| Live smoke | `python -m agent.run --ids 1 --stage pass1 --model <free model>` | one schema-valid row; ≥1 `llm` and ≥1 `tool` line in `run_log.jsonl`; `.env` values absent from all output and logs |
| Budget guard | `pytest tests/test_budget.py` | a call that would exceed the cap is refused with exit code 4 |

## Milestone 1 — First pass on all 100 + baseline
| Check | Command | Pass criterion |
|---|---|---|
| Pilot | `python -m agent.run --pilot --model <A>` and `--model <B>`; `python scripts/pilot_compare.py` | comparison table produced; pass-1 model chosen and recorded in `.env.example` |
| Full run | `python -m agent.run --all --resume` | completes or resumes cleanly; exit 0 |
| Validity | `python -m agent.validate results/pass1.json --expect 100` | 100 unique rows; schema-valid; every non-`unknown` scored field has evidence; every `unknown` has a reason |
| Baseline | `python -m agent.score --input results/pass1.json` | accuracy overall / per field / abstain rate computed on the 20-app sample; recorded. If ground truth is not yet ready, Milestone 1 closes on the other criteria and scoring runs as soon as it is |
| Cost | `python -m agent.cost_report` | run log sums to the reported total; total ≤ $1; within ~10% of the OpenRouter dashboard |

## Milestone 2 — Verification loops + accuracy delta
| Check | Command | Pass criterion |
|---|---|---|
| Loops | `python -m agent.verify --input results/pass1.json --output results/pass2.json --loops grounding,cross,judge,reresearch` | `results/pass2.json` written; every changed field has a `pass2_diff` entry |
| Validity | `python -m agent.validate results/pass2.json --expect 100` | same criteria as Milestone 1 |
| Delta | `python -m agent.score --compare results/pass1.json results/pass2.json` | `verification/compare.json` with per-field and overall pass 1 → pass 2 change, every hit and miss listed, any post-hoc fix labelled "tuned on sample". The delta is reported whatever its sign; the target is a clear improvement, not a gate |
| Cost | `python -m agent.cost_report` | cumulative spend ≤ $4 |

## Milestone 3 — Patterns, page, deploy, README
| Check | Command | Pass criterion |
|---|---|---|
| Build | `python scripts/build_site.py` | writes `site/index.html` and `site/results.json`; embedded JSON equals `results.json` |
| Structure | `pytest tests/test_build_site.py` | sections in the §1.2 order; exactly 100 table rows; headline numbers equal values recomputed from `results.json`; limitations and "independent submission" text present |
| Visual/DOM QA | `python scripts/qa_site.py` | at 1440 px and 390 px: no console errors, no horizontal overflow, all sections visible; screenshots saved to `results/qa/` |
| Deploy | push `site/` to `gh-pages` (with approval); `curl -sI <url>` and `curl -s <url>/results.json` | HTTP 200 for both; JSON parses; page loads in a private window |
| Fresh install | new venv → `pip install -r requirements.txt && pytest -q` | passes; README quickstart runs `python -m agent.run --ids 1` with keys |
| Hygiene | `git ls-files` + `git grep -nE "sk-or-v1-\|OPENROUTER_API_KEY=.+"` | no `.env`, no keys, no private notes or company-provided brief tracked; README has Known Limitations & Tradeoffs and one high-level sentence on AI tool use |
| Submission | — | live page URL + repo URL ready to send |

## Cross-cutting acceptance
- Every number on the page traces to `results.json`, which traces to logged runs.
- Misses and failures are visible on the page, not only successes.
- A reviewer can understand the page in ~2 minutes without narration.
