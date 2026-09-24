# Reference: third-party contracts and cheatsheets

Verified facts about external APIs this project depends on. Every entry says **how** and **when** it was verified. Anything marked *UNVERIFIED* must be confirmed (live call or official docs) before code depends on it; then update the entry.

Files in this folder that are gitignored (local only, never published): `ASSIGNMENT.md`, `HANDOFF.md`.

---

## OpenRouter (all LLM calls, via `agent/llm_client.py`)

| Item | Value | Status |
|---|---|---|
| Endpoint | `POST https://openrouter.ai/api/v1/chat/completions` (OpenAI-compatible) | per SPEC; confirm on first live call |
| Auth | `Authorization: Bearer $OPENROUTER_API_KEY` | per SPEC |
| Cost accounting | request body `"usage": {"include": true}`; cost expected at `response.usage.cost` | *UNVERIFIED*: confirm the field name against a live response; fall back to tokens × published price |
| Structured output | `response_format: {"type": "json_schema", ...}` where the model supports it | *UNVERIFIED* per model; the pilot records which models honour it |
| Key status | `GET https://openrouter.ai/api/v1/key` → `limit`, `limit_remaining`, `free_model_daily_requests` (field names per earlier docs read) | *UNVERIFIED*: check at M0 before the first paid run |
| Free models | `:free` suffix; 20 req/min; 1,000 req/day once ≥$10 of credit has been purchased (50/day otherwise) | from OpenRouter docs, 2026-09-24; confirm via `/key` |
| Settings | `temperature=0` on every call | project rule |

Candidate models (prices $/M tokens in/out, OpenRouter catalogue 2026-09-24, not benchmarked):

| Role | Model | Price |
|---|---|---|
| Pass 1 (pilot decides) | `deepseek/deepseek-v4-flash` | 0.09 / 0.18 |
| Pass 1 free alternative | `qwen/qwen3.8-27b:free`, `google/gemma-4-31b-it:free` | 0 |
| Verifier (other family) | `openai/gpt-5.6-luna` / `qwen/qwen3.7-plus` | 0.20 / 1.20 · 0.32 / 1.28 |
| Verifier escalation | `deepseek/deepseek-v4-pro` | 0.94 / 1.88 |
| Judge (disputed fields only) | `anthropic/claude-sonnet-5` | 2.00 / 10.00 |

---

## Composio (search + fetch, via `agent/tools.py`)

| Item | Value | Status |
|---|---|---|
| Package | `composio` Python SDK (version pinned in `requirements.txt`) | installed at scaffolding |
| Auth | `COMPOSIO_API_KEY`; dedicated project, **no connected accounts** | per SPEC |
| Toolkit | no-auth Search toolkit: Web Search, DuckDuckGo, Fetch URL Content | *UNVERIFIED*: exact slugs |
| Tool slugs (allowlist) | TBD, e.g. search + DuckDuckGo + fetch-URL slugs | *UNVERIFIED*: record the exact slugs after checking the docs and making one live call |
| Call signature / `user_id` | TBD | *UNVERIFIED* |
| Rate limit | assumed ≈1–2 req/s | *UNVERIFIED*: measure during the pilot |

Allowlist rule: `tools.py` rejects any slug not listed in this section.

---

## GitHub Pages (hosting)

- Deploy from a `gh-pages` branch that contains the contents of `site/`. Branch deploy avoids needing the `workflow` token scope.
- Repo must be public. Enable Pages via the REST API (`POST /repos/{owner}/{repo}/pages`, source branch `gh-pages`, path `/`), but only with the user's approval.
- Verify with `curl -sI <url>` and `curl -s <url>/results.json` (both HTTP 200).
