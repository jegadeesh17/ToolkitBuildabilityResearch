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
| Key status | `GET https://openrouter.ai/api/v1/key` → `data.limit`, `data.limit_remaining`, `data.usage`, `data.is_free_tier` | **Verified by live call 2026-09-24 18:38 IST**: limit 5, limit_remaining 5, usage 0, is_free_tier false. No `free_model_daily_requests` field is returned; `is_free_tier: false` indicates ≥$10 purchased (1,000 free req/day per docs) |
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
| Toolkit | `composio_search` (no auth), toolkit version `20260903_00` | **Verified** via `tools.get_raw_composio_tools(toolkits=["composio_search"])`, 2026-09-24 18:36 IST |
| Tool slugs (allowlist) | `COMPOSIO_SEARCH_WEB` (`query`), `COMPOSIO_SEARCH_DUCK_DUCK_GO` (`query`, `start`), `COMPOSIO_SEARCH_FETCH_URL_CONTENT` (`urls[]` required; `text` bool default true; `max_characters`; `summary`; `extras`) | **Verified** (metadata listing). Fetch rejects images/PDFs/binaries per its schema |
| Call signature / `user_id` | `client.tools.execute(slug, arguments, user_id="default", version="20260903_00")` → dict `{data, error, successful}` | **Verified by live call 2026-09-24 ~18:55 IST** after the key was re-issued with `tool_execution` write access (the first key returned 403: Composio keys DO have per-key permissions) |
| Response: `COMPOSIO_SEARCH_WEB` | `data = {answer: str (AI-generated summary), citations: [{id, title, url, image?}]}` | Verified; ~3.2 s. The pipeline uses citation URLs only; the generated `answer` is never evidence |
| Response: `COMPOSIO_SEARCH_FETCH_URL_CONTENT` | `data = {requestId, results: [{id, url, title, text, author}], statuses: [{id, status, source}]}`; `text` is markdown | Verified; ~1.7 s per URL |
| Rate limit | assumed ≈1–2 req/s; client throttles to ≥0.6 s between calls | *UNVERIFIED*: measure during the pilot |

Allowlist rule: `tools.py` rejects any slug not listed in this section.

---

## GitHub Pages (hosting)

- Deploy from a `gh-pages` branch that contains the contents of `site/`. Branch deploy avoids needing the `workflow` token scope.
- Repo must be public. Enable Pages via the REST API (`POST /repos/{owner}/{repo}/pages`, source branch `gh-pages`, path `/`), but only with the user's approval.
- Verify with `curl -sI <url>` and `curl -s <url>/results.json` (both HTTP 200).
