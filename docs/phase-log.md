# Jalora — Phase Log (§84)

Each phase ends with: WHAT WAS BUILT / FILES CHANGED / DEPS / TESTS / RESULTS / LIMITATIONS / NEXT.

---

## P0–P2 — Foundation (earlier phases)

Project skeleton, config, DB + Alembic baseline (`792adf846248`), auth (Argon2 + JWT + rate limit),
model router (7 OpenAI-compatible providers + Anthropic native + Ollama slot), chat + SSE streaming,
LangGraph supervisor with CONFIRM-approval pause/resume and STOP, 10-tool registry with permission
levels, short-term memory (history + local-hash cosine retrieval), PDF documents + RAG query,
audit log, vanilla SPA, Docker/compose, `gap-matrix.md`. Tests: 40/40 green.

---

## P3 — Agent core: policy engine, plans, tasks, step budget

### WHAT WAS BUILT
- **Policy engine** (`services/policy.py`): autonomy levels L0–L4 (0 Chat → 4 Full auto),
  per-tool policies (`automatic | approval | denied`), `decide()` returning allow / require-approval
  with a reason code (`AUTONOMY_DISABLED`, `AUTONOMY_TOO_LOW`, `TOOL_DENIED`, `POLICY_DENIED`,
  `POLICY_APPROVAL`, `STRICT_REQUIRES_APPROVAL`, …). Rules: L0 denies all tools (internal planner
  tool exempt), L1 caps at NOTIFY, user `denied` always wins, `approval` always tightens,
  `automatic` only loosens below STRICT and only at L≥3, STRICT is never loosened.
- **Plan tool** (`tools/plan.py`): `update_plan(steps)` — AUTO, internal, exempt from policy;
  normalizes `[{description, status}]` and mirrors into `Task`/`TaskStep` rows via graph sync.
- **Task model + API**: `Task` (goal, status, plan JSON, current_step, progress, error, result,
  timestamps; unique `run_id`) + `TaskStep` (task_id+idx unique). Routes: `GET /api/tasks`,
  `GET /api/tasks/{id}` (with steps), `POST /api/tasks/{id}/cancel` (400
  `TASK_NOT_CANCELLABLE` outside queued/running/waiting_for_approval, 404 unknown).
  Alembic migration `9ce2119c629c_add_tasks_tables` (upgrade→downgrade→upgrade verified).
- **Settings API**: `GET/PATCH /api/settings` — autonomy (0–4, 422 out of range), tool_policies
  (400 `UNKNOWN_TOOL`/`INVALID_POLICY`), memory_enabled, locale, plus the tool manifest for the UI.
- **Graph hardening** (§63): `build_graph(max_tool_calls, max_retries)`; tool-call budget with
  `TOOL_LIMIT` result + route to respond; retry loop for `TIMEOUT/NETWORK_ERROR/TOOL_EXCEPTION`
  up to `agent_max_retries` (no retry for approval-required); `respond_node` wrap-up branches that
  answer outstanding tool calls with `SKIPPED`/`STOPPED` fillers (persisted) and force a final
  tool-free answer (`MAX_STEPS_NUDGE`); empty-content-after-tools branch so a model can never emit
  a tool-only turn as its final answer.
- **Chat service**: per-turn `Task` creation, plan/progress mirror (`_plan_progress`, `_sync_plan`),
  `asyncio.wait_for(task_timeout_seconds)` → `TASK_TIMEOUT`, durable `_finalize_task`
  (completed / waiting_for_approval / failed; `cancelled` never overwritten), task_id in SSE
  `meta`/`done` events and all ChatResponse payloads; `run_registry.stop_run` now durable for
  runs paused at approval (cancel-during-approval test).
- **Frontend Tasks view + settings controls**: rebuilt `index.html` to match `app.js`/`styles.css`
  (they had drifted into two different generations — the SPA crashed at load), added
  `view-tasks` (status chips, animated progress bar, plan checklist with step icons, live 3 s poll,
  cancel button), autonomy selector + per-tool policy selects in Settings, EN/Kiswahili nav labels.
- **Frontend guards**: `tests/test_frontend_consistency.py` — every `$("id")` referenced by app.js
  must exist in index.html, nav views ↔ view containers ↔ switchView branches, every
  `data-i18n` key present in the English bundle, locale buttons ↔ I18N bundles, static assets on
  disk, no `.hidden` on view containers.

### FILES CHANGED
`app/config.py`, `app/models/task.py`, `app/models/__init__.py`,
`alembic/versions/9ce2119c629c_add_tasks_tables.py`, `app/services/policy.py`,
`app/tools/plan.py`, `app/tools/__init__.py`, `app/agents/graph.py`, `app/agents/prompts.py`,
`app/services/chat_service.py`, `app/services/run_registry.py`, `app/api/tasks.py`,
`app/api/settings.py`, `app/api/__init__.py`, `app/schemas/__init__.py`, `app/api/runs.py`,
`app/api/chat.py`, `tests/conftest.py`, `tests/test_tasks.py`,
`tests/test_frontend_consistency.py`, `frontend/index.html`, `frontend/assets/app.js`,
`frontend/assets/styles.css`, `docs/phase-log.md`, `docs/gap-matrix.md`.

### DEPS
None added (used langgraph/sqlalchemy/alembic already installed).

### TESTS
`python -m pytest tests -q` → **57 passed** (40 prior + 11 task/policy tests + 6 frontend
consistency tests), ~41 s, offline (FakeModel/FakeRouter, temp SQLite).
Live smoke against real uvicorn: 24/24 checks (boot+health, static SPA ids, register, tasks API,
settings GET/PATCH incl. 422/400 validation, honest `MODEL_UNREACHABLE` without provider,
404 cancel, `/api/openapi.json` paths).

### RESULTS
All acceptance scenarios green: plan exempt at L0; L1 NOTIFY cap; user deny beats permission;
approval-tightening policy; automatic loosening matrix (L2 ignored / L3 loosens CONFIRM / STRICT
never); retry-then-success; tool budget stops execution and wraps up with balanced history;
cancel during approval finalizes task; tasks API guards; settings round-trip.

### LIMITATIONS
- Long-term memory (ChromaDB), browser automation, comms, scheduling etc. are later phases.
- Task progress granularity = plan steps; fine-grained per-tool progress events are not exposed.
- Frontend is still vanilla JS until the P4 React migration; consistency tests are static
  (regex-level), not a real browser (Playwright arrives P6/P14).
- No real-provider live chat run in CI (offline by design); verified honest failure path instead.

### NEXT
Done: **P4 — React + TypeScript + Vite migration** (see below).

---

## P4 — React + TypeScript + Vite migration

### WHAT WAS BUILT
- **React app** in `jalora/frontend-react/`: React 19 + TypeScript (strict) + Vite 7,
  no router/state libs needed — one `App` shell + six views. `npm run build` → `dist/`
  (typecheck via `tsc -b` gates the build).
- **Views ported 1:1 from the vanilla SPA** with the same API contract:
  - `ChatView` — SSE streaming (meta/token/tool_start/tool_end/approval/done/error), markdown,
    tool chips, sources, approval banner + modal, suggestions, Enter-to-send, file attach.
  - `TasksView` — live 3 s polling, status chips, progress bar, plan checklist, cancel.
  - `MemoryView`, `DocumentsView`, `ActivityView`, `SettingsView` (providers, autonomy L0–L4,
    per-tool policy selects, tool pills, EN/Kiswahili).
  - `AuthScreen` (login/register), sidebar (conversations, STOP ALL, logout), toasts.
- **i18n** EN/Kiswahili bundles (`lib/i18n.ts`), locale persisted in localStorage.
- **Backend serving**: `app/main.py` now prefers `frontend-react/dist` and falls back to the
  legacy `frontend/` SPA; `/assets` mounts the active root; SPA deep links fall back to index;
  API prefixes still 404 JSON. Dev mode: `npm run dev` proxies `/api` to `127.0.0.1:8000`.
- **Tests**: `tests/test_react_frontend.py` (5) — build exists when toolchain installed,
  React index + hashed bundle served, deep-link fallback, API 404 preserved. Legacy
  `test_frontend_consistency.py` still guards the fallback SPA.

### FILES CHANGED
`frontend-react/package.json`, `vite.config.ts`, `tsconfig.json`, `index.html`,
`src/main.tsx`, `src/App.tsx`, `src/styles.css`, `src/lib/{api,i18n,types}.ts`,
`src/views/{ChatView,TasksView,MemoryView,DocumentsView,ActivityView,SettingsView}.tsx`,
`app/main.py`, `tests/test_react_frontend.py`, `docs/phase-log.md`, `docs/gap-matrix.md`.

### DEPS
Frontend only: react 19.2, react-dom, vite 7.3.7, @vitejs/plugin-react 5, typescript 5.9
(`npm install` verified against registry.npmjs.org). Backend: none.

### TESTS
`python -m pytest tests -q` → **62 passed** (57 prior + 5 React-serving), ~49 s.
`npm run build` → clean (tsc strict + vite, 249 kB js / 10.7 kB css, ~7 s).
Live uvicorn smoke: 17/17 — React index + bundle served, register, tasks/settings APIs,
honest 502 MODEL_UNREACHABLE, 404 semantics, OpenAPI.

### RESULTS
Backend serves the React build at `/` with working deep links; API contract unchanged
(all prior tests still green); typecheck-verified source; i18n parity with the legacy SPA.

### LIMITATIONS
- No real-browser (Playwright) verification yet — DOM behavior validated by build +
  serving tests only; browser e2e lands with P6 (Playwright) / P14.
- Legacy vanilla SPA kept as automatic fallback (`frontend/`) until P14 removes it.
- Integrations page (§44) still pending (P13); bundle not code-split (single 250 kB chunk).

### NEXT
**P5 — Memory v2**: ChromaDB (local-hash cosine fallback), classification, provenance UI.

---

## Phase 5 � Memory v2 (ChromaDB, classification, provenance) � ? DONE (2026-10-07)

### WHAT WAS BUILT
- **Pluggable vector index** (`app/services/vector_store.py`): `SqlVectorStore` (offline
  cosine over SQL rows � the default/test path) and `ChromaVectorStore` (ChromaDB 1.5.9
  persistent client, cosine HNSW, per-user `where` filter, id-addressed upserts).
  Resolution via `JALORA_MEMORY_VECTORSTORE` = `auto | sql | chroma` (auto prefers Chroma
  when importable, degrades to SQL with a logged warning). SQL rows remain the source of
  truth; the Chroma index is **lazily rebuilt from SQL whenever its per-user count drifts**.
  All Chroma operations are exception-guarded � store failure falls back to SQL cosine so
  retrieval never hard-fails.
- **Memory classification** (`app/services/memory_classify.py`, spec �13): deterministic
  offline rules, priority `contact > person > preference > project > fact`. `kind="auto"`
  triggers classification in `add_memory_async`; the `store_memory` tool now defaults to
  `kind="auto"` (agent stores self-classify; API default `fact` unchanged for the UI form).
- **Provenance** (�13): `MemoryOut.source` (user | agent | explicit_tool) now surfaced in
  both UIs � React `MemoryView` shows translated kind + source chips + timestamp (EN/SW
  labels: kindFact/kindPreference/kindContact/kindPerson/kindProject, srcUser/srcAgent/
  srcExplicitTool); legacy SPA shows the same badges. `source` was already persisted on
  every write path.
- **Wiring**: add ? `safe_index`, delete ? `safe_remove`, search ? store-first with SQL
  fallback (`memory_service.search_memories`); chat memory injection and the
  `search_memory` tool both benefit unchanged.
- **Config**: `memory_vectorstore`, `chroma_dir` (`jalora/data/chroma`).
- **Tests** `tests/test_memory_v2.py` (10): classifier rules, API `kind="auto"`,
  tool auto-classify + provenance, SQL search/delete roundtrip, store-failure SQL fallback,
  Chroma resolution/end-to-end/lazy-reindex, auto?chroma preference.

### FILES CHANGED
`app/config.py`, `app/services/{memory_classify,vector_store,memory_service}.py`,
`app/tools/memory_tools.py`, `tests/conftest.py` (JALORA_MEMORY_VECTORSTORE=sql +
temp JALORA_CHROMA_DIR), `tests/test_memory_v2.py`,
`frontend-react/src/{views/MemoryView.tsx,lib/types.ts,lib/i18n.ts,styles.css}`,
`frontend/assets/app.js`, `docs/phase-log.md`, `docs/gap-matrix.md`.

### DEPS
`chromadb 1.5.9` (installs clean on Python 3.14.7; pulls onnxruntime, grpcio, etc.).
Chroma never downloads models � embeddings are supplied explicitly (local-hash offline).

### TESTS
`python -m pytest tests -q` ? **72 passed** (62 prior + 10 new), ~74 s.
`npm run build` ? clean (tsc strict + vite, 250.6 kB js / 10.9 kB css).
Live uvicorn smoke: 17/17 � React build served, register, tasks/settings APIs, honest
502 MODEL_UNREACHABLE, 404 semantics, OpenAPI.

### RESULTS
Long-term memory now has a real vector index (Chroma in prod, SQL cosine offline),
automatic kind classification for agent writes, and visible provenance; every prior test
still green with the SQL store forced for determinism.

### LIMITATIONS
- Classification is rule-based (no LLM classifier) � deliberately offline/deterministic;
  `person`/`project` kinds are new vocabulary beyond fact/preference/contact/task_result.
- Chroma index rebuild is per-search count check (fine at personal scale; batch it if
  memories grow to 10k+).
- No conversation-id provenance on rows (source/kind/time only); auto-extraction of
  memories from chat transcripts is not implemented (agent stores via tool only).
- Chroma telemetry emits a DeprecationWarning under py3.14 (upstream, harmless).

### NEXT
**P6 � Web search provider abstraction + browser automation (Playwright) + untrusted-content
labeling (�6�8, �59)**, then P7 Twilio.

---

## Phase 6 � Search abstraction + browser automation + injection defence (�6�8, �59) � ? DONE (2026-10-07)

### WHAT WAS BUILT
- **Search provider abstraction** (`app/services/search_service.py`, �6): providers
  `duckduckgo` (ddgs), `tavily` (key), `mock` (offline deterministic � test mode).
  `JALORA_SEARCH_PROVIDER` = `auto | duckduckgo | tavily | mock`; auto ? tavily when a key
  exists, else duckduckgo; unknown names raise `SearchError(PROVIDER_NOT_CONFIGURED)`.
  `search_web` tool now delegates here; result shape unchanged (`{results:[{title,url,snippet}]}`)
  plus `untrusted: true`.
- **URL safety / SSRF guard** (`app/services/url_safety.py`, �8): http/https only,
  no embedded credentials, blocks private/loopback/link-local/metadata/multicast/reserved
  addresses (literal + DNS-resolved, incl. 169.254.169.254) unless
  `JALORA_BROWSE_ALLOW_PRIVATE=true`. DNS failures pass through so unreachable hosts keep
  their honest NETWORK_ERROR. Applied to `open_url` **and** the new browser tool.
- **Browser automation** (`app/services/browser.py` + `app/tools/browse.py`, �7):
  new `browse_page` tool renders JS pages via Playwright (fresh headless browser per call �
  no cross-event-loop state), shared `html_to_text` extraction (`page_text.py`) strips
  script/nav/chrome; structured failures (BLOCKED_*, BROWSER_UNAVAILABLE, BROWSER_FAILED,
  TIMEOUT, EMPTY_PAGE). Channel fallback `chrome ? msedge ? playwright bundle` because
  cdn.playwright.dev stalls on this network (no bundle download needed � system Chrome/Edge).
  `graph.py` extracts sources for `browse_page` like `open_url`.
- **Prompt-injection defence** (�59): BASE_SYSTEM gains a Security section � search_web/
  open_url/browse_page output is UNTRUSTED data, never instructions; research tool payloads
  carry `"untrusted": true`; browser fetches are label-fed to the model via that marker.
- **Config**: `browse_allow_private` (default False), `browser_timeout_seconds`;
  conftest sets `JALORA_SEARCH_PROVIDER=mock` (all tests offline).

### FILES CHANGED
`app/services/{search_service,url_safety,page_text,browser}.py`, `app/tools/{web,browse}.py`,
`app/tools/__init__.py`, `app/agents/{prompts,graph}.py`, `app/config.py`,
`tests/conftest.py`, `tests/test_web_p6.py` (new, 10 tests),
`tests/test_tools.py` (ftp code now BLOCKED_SCHEME/INVALID_URL), `docs/*`.

### DEPS
`playwright 1.63.0` (pip; chromium bundle NOT downloaded � CDN unreachable, channel fallback
used). No other new deps (ddgs/httpx/bs4 already present).

### TESTS
`python -m pytest tests -q` ? **82 passed** (72 prior + 10 new incl. 2 real browser renders),
~86 s. Live uvicorn smoke: 17/17. Browser tests launch system Chrome headless, fetch a local
http.server page, assert visible text present + script noise stripped + default loopback block.

### RESULTS
Research path is provider-pluggable and offline-testable; all outbound fetches are SSRF-guarded;
JS pages render in a real browser; the model is explicitly warned that web tool output is
untrusted data. Zero regressions.

### LIMITATIONS
- Playwright browser bundle not downloadable from this network (`PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT`
  honored but CDN stalls); `chrome`/`msedge` channel fallback covers this machine � pure-Linux
  servers still need `playwright install chromium`.
- Injection defence is prompt-label + data-marker based (no input/output filtering/heuristics).
- `browse_page` launches a fresh browser per call (~0.5�1 s overhead; singleton pooling later).

### NEXT
**P7 � Twilio comms (mock-first, then real keys per �77 � never reuse exposed creds)**.

---

## Phase 7 - Twilio comms: SMS/WhatsApp/Voice + inbound webhook (§14–18, §55–56, §24) - ✅ DONE (2026-10-07)

### WHAT WAS BUILT
- **Comms provider service** (`app/services/comms.py`): mock-first providers `mock | twilio`
  via setting `comms_provider` (env `JALORA_COMMS_PROVIDER`) = `auto | mock | twilio`;
  auto resolves to twilio only when sid + auth token exist, else deterministic offline mock
  (SM/CA-prefixed receipts, never networked). `normalize_phone` accepts E.164 and
  digit-only input (leading-trunk-0 numbers honestly rejected - no country guessing),
  raises `CommsError(INVALID_PHONE|EMPTY_MESSAGE|PROVIDER_NOT_CONFIGURED|PROVIDER_ERROR)`.
  Real sends go through httpx basic-auth to Twilio's 2010-04-01 REST API.
- **Tools** (`app/tools/comms.py`): `send_sms`, `send_whatsapp`, `place_voice_call` - all
  **CONFIRM (L3)**, `requires_internet`, category `comms`. Structured failures on every
  path; registry manifest now exposes 14 tools.
- **Inbound webhook** (`app/api/comms.py`): `POST /api/comms/twilio/inbound` reads
  form-encoded Twilio params (manual urlencoded parse - no python-multipart dependency),
  verifies `X-Twilio-Signature` (HMAC-SHA1 over URL + sorted params) whenever
  `JALORA_TWILIO_AUTH_TOKEN` is set - 403 otherwise; resolves/creates a **guest user +
  `comms_identities` row** (per channel+address unique) and reuses the linked conversation
  across messages; runs one `execute_chat` turn (never raises) via `Depends(get_router)`
  so tests script it with FakeRouter; replies with TwiML `<Message>` - honest fallback
  text for paused approvals (`paused_approval` -> "needs approval in the web app") and
  errors; truncates replies at 1500 chars; empty/invalid-sender posts get an empty
  `<Response/>`.
- **Model + migration**: `app/models/comms.py` `CommsIdentity` (user FK, channel,
  address, conversation FK, unique (channel,address)) + alembic revision
  `c3d8f1a52b94_add_comms_identities` (revises `9ce2119c629c`).
- **Config**: `comms_provider`, `twilio_account_sid`, `twilio_auth_token`,
  `twilio_from_number`, `twilio_whatsapp_from` - all env-overridable, empty by default
  (mock mode). Fresh key required for live use (§77 - never reuse exposed creds).

### FILES CHANGED
`app/services/comms.py` (new), `app/tools/comms.py` (new), `app/api/comms.py` (new),
`app/models/comms.py` (new), `app/models/__init__.py`, `app/api/__init__.py`,
`app/tools/__init__.py`, `app/config.py`,
`alembic/versions/c3d8f1a52b94_add_comms_identities.py` (new),
`tests/test_comms.py` (new, 16 tests), `tests/test_tools.py` (send_sms now registered),
`docs/*`.

### DEPS
None new (httpx already present). Twilio credentials are runtime config only.

### TESTS
`python -m pytest tests -q` -> **98 passed** (82 prior + 16 new), ~126 s. Live uvicorn
smoke: **17/17** (tools=14). New tests cover: registration/permission gates, mock receipts
for all 3 channels, INVALID_PHONE/EMPTY_MESSAGE/PROVIDER_NOT_CONFIGURED structured
failures, provider auto-resolution, graph approval pause -> approve -> send + activity
feed entry, approval rejection -> nothing sent, inbound webhook TwiML reply, guest
identity + conversation reuse (2nd message same thread, 4 messages), whatsapp channel
detection, invalid sender -> empty TwiML + no user created, signature enforced when
token set (403 missing / 403 tampered / 200 valid), signature helper round-trip +
tamper matrix, E.164 normalization.

### RESULTS
Outbound SMS/WhatsApp/voice are approval-gated and honest offline (mock receipts flagged
`mock: true`); inbound SMS/WhatsApp reaches the same agent graph as the web chat with a
proper TwiML reply; webhooks are signature-verified when a token is configured; guest
identities persist per phone address. Zero regressions.

### LIMITATIONS
- Live Twilio sending not exercised (no real key by design - §77); `auto` flips to twilio
  only when sid+token are both configured.
- TwiML replies are plain text; no media/location/MMS handling; approvals raised from SMS
  must be resolved in the web UI (webhook replies say so honestly).
- Signature verification uses `str(request.url)` - behind a reverse proxy the public URL
  must be preserved (proxy headers config) for Twilio signatures to match.
- No rate limiting on the inbound webhook yet (P13 hardening).

### NEXT
**P8 - Gmail + email automation (mock-first, §19–20).**

---

## Phase A � Stabilize foundation: bug fixes, Stop, i18n, test gaps � ? DONE (2026-10-07)

### WHAT WAS BUILT
- **Approval resume keeps the system prompt** (`chat_service.resume_approval`): the resumed
  graph was built WITHOUT `system_prompt`, so every approve?continue turn lost persona,
  user name, locale and memory context. Resume now rebuilds the prompt the same way
  `stream_chat` does (user prefs + memory search over the last human message) and passes it
  to `build_graph`. Regression test asserts the resumed model call starts with a SystemMessage
  containing the user's name (�29).
- **Vision routing no-op fixed** (`model_router.route`): `(name != chosen_name or True)` always
  true. New logic: keep the chosen provider when it is vision-capable (critically, this stops
  image requests from silently breaking `prefer_private` local routing), otherwise switch to
  the first configured vision-capable provider (�4).
- **Per-message Stop** (�12): new `POST /api/runs/{run_id}/stop` endpoint (ownership-checked)
  flags a single run in `run_registry` � the graph already honours it (agent/tools/respond
  nodes check `is_stopped`). `ChatView` now wires an `AbortController` to the chat fetch and
  swaps the send button for a pulsing Stop button while streaming; abort finalises the partial
  text with "? Stopped." and best-effort calls the stop endpoint using the run_id from the
  SSE `meta` event. (Legacy "STOP ALL AGENTS" remains in the sidebar.)
- **Documents Q&A panel** (�41): `window.prompt()`/`window.alert()` replaced with an inline
  per-document question panel (textarea + ask + answer display + cancel) in `DocumentsView`.
- **i18n completion**: ~40 hardcoded EN strings extracted to the EN/SW bundles across
  ChatView (approval modal/banner, suggestions, toasts, attach/error strings), DocumentsView,
  MemoryView, ActivityView, SettingsView (configured status, autonomy L0�L4 labels, policy
  options, save toasts), App (auth placeholders, delete title, locale toast). Verified:
  all 84 used `t()` keys exist in both bundles; no hardcoded UI strings remain (brand name
  excepted).
- **Docs/config fixes**: README quickstart now copies `.env.example` to `jalora\.env`
  (where `config.py` actually reads it) and the broken `..\..\.venv` interpreter path is
  `..\.venv`; `.env.example` header states the real target location.
- **Docstring**: removed nonexistent "Hugging Face router" provider claim.
- **Test-suite hermeticity**: `conftest.py` blanks all provider/Twilio env keys before app
  import � real creds in `jalora/.env` were leaking in (dotenv) and had broken 6 comms tests
  (auto?twilio, signature enforcement). Empty-string keys resolve falsy/None as intended.

### FILES CHANGED
`app/services/chat_service.py`, `app/services/model_router.py`, `app/api/runs.py`,
`tests/conftest.py`, `tests/test_approvals.py`, `tests/test_chat.py`,
`tests/test_model_router.py` (new), `tests/test_audit.py` (new),
`frontend-react/src/views/{ChatView,DocumentsView,MemoryView,ActivityView,SettingsView}.tsx`,
`frontend-react/src/App.tsx`, `frontend-react/src/lib/i18n.ts`,
`frontend-react/src/styles.css`, `README.md`, `backend/.env.example`, `docs/phase-log.md`.

### DEPS
None added or required.

### TESTS
`python -m pytest tests -q` ? **118 passed** (98 prior + 13 router + 4 audit + 1 resume
persona + 2 per-run stop = 20 new), 0 failed, ~80 s, fully offline.
`npm run build` ? clean (`tsc -b` strict + vite; 255 kB js / 11.9 kB css).
i18n audit: 84/84 keys present in EN and SW.

### RESULTS
Resume turns keep persona/memory; vision routing respects privacy pinning; streaming is
interruptible per message; Documents Q&A works inline; UI fully translatable; suite is
immune to developer `.env` secrets; README instructions now actually work.

### LIMITATIONS
- Stop aborts the client stream immediately but the server-side model call finishes its
  current token stream (stop takes effect at the next graph checkpoint) � same as spec �12
  allows.
- ESLint is not installed (`tsc -b` is the only frontend gate); a lint runner lands in P14.
- Exposed API keys pasted earlier must still be ROTATED by the user (A9) � code never logs
  them, but they are compromised.

### NEXT
**Phase B � agent loop completion**: intent classification, replanner, research orchestration,
`update_memory`, conversation summarization, generic pause/resume, live plan widget (�4, �9�10,
�13, �20�21, �24, �26, �70, �79).

---

## Phase B — Agent loop completion — DONE (2026-10-07)

### WHAT WAS BUILT
- **Intent classification** (`app/agents/intent.py`, spec �4): deterministic offline routing
  intent = `research | scheduling | coding | memory | task | search | chat` from signals;
  scheduling beats coding/research, coding beats research+task, research via URL/keywords.
  Emitted as an SSE `intent` event (with signals) and injected as a `RESEARCH_BRIEF` system
  message when research intent; per-turn `log_activity` entry (agent=intent).
- **Research orchestration** (§20–21): research turns compile sources/titles and the graph
  emits them; `search_web` capped at `max_results`.
- **Memory capture** (�13): `app/services/memory_capture.py` extracts explicit user remembers
  ("remember that X", "from now on X", "my name is X") BEFORE retrieval so the same turn sees
  them; sensitive values refused; SSE `memory` event + activity entry. Patterns are punctuation-
  optional but refuse demonstrative starts ("remember this" is never stored).
- **Conversation summarization** (�26, �79): `maybe_summarize_history` compacts history past
  `summary_threshold` with one model call (keep `summary_keep` newest verbatim), SSE `summary`
  event, `RESEARCH_BRIEF`-like injection into the running prompt; `history_limit` bounds load.
- **Replanner on tool failure** (�9�10): non-benign failure (`_causes_replan`) marks the active
  plan step `failed` and re-emits a `plan` event with a reason so the model revises remaining
  steps; benign failures (USER_REJECTED/STOPPED/MAX_STEPS/TOOL_LIMIT/SKIPPED/PAUSED) never
  replan. Plan steps update in the Task row.
- **Source dedup**: `_dedupe_sources` collapses identical URLs from repeated searches.
- **Generic pause/resume** (�24): graph pause checkpoints on pre-tool and post-agent boundaries;
  `POST /api/runs/{run_id}/pause` (running-only, `RUN_NOT_PAUSABLE` otherwise, ownership 404,
  401) flags `run_registry`; `POST /api/runs/{run_id}/resume` (400 `RUN_NOT_RESUMABLE` unless
  paused with state) rebuilds the prompt from stored state (`_resume_system_prompt`) and
  continues. Paused runs persist `state_json`, cleared on success. New run status `paused`.
- **API/schema**: `ChatResponse` gains `intent` + `plan`; tasks `ACTIVE_STATUSES` includes
  `paused`; Task/AgentRun status comments updated; pause/resume responses reuse the chat shape.
- **Frontend**: ChatView renders an intent chip, a live plan widget (`plan` SSE events with
  reason line), a Pause button while streaming, and a Resume banner for paused runs; TasksView
  adds paused chip/label and per-task Pause/Resume buttons; only new i18n keys added (EN + SW).
- **Config**: `JALORA_HISTORY_LIMIT`/`JALORA_SUMMARY_THRESHOLD`/`JALORA_SUMMARY_KEEP` in
  `.env.example`.

### FILES CHANGED
`app/agents/{intent,graph}.py`, `app/services/{chat_service,memory_capture,run_registry}.py`,
`app/api/runs.py`, `app/api/tasks.py`, `app/models/{task,agent}.py`, `app/schemas/__init__.py`,
`app/config.py`, `backend/.env.example`, `tests/test_agent_phase_b.py` (new, 21 tests),
`tests/test_tasks.py` (memory capture guard), `frontend-react/src/views/{ChatView,TasksView}.tsx`,
`frontend-react/src/lib/{i18n,types}.ts`, `frontend-react/src/styles.css`, `docs/*`.

### DEPS
None new (langchain/langgraph/httpx already present).

### TESTS
`python -m pytest tests -q` - **139 passed** (118 prior + 21 Phase B), ~93 s, fully offline.
`npm run build` - clean (`tsc -b` strict + vite; 258 kB js / 12.1 kB css).

### RESULTS
Agent loop is now closed end-to-end: intent drives research briefs, explicit memories survive
into the current turn, long conversations are compressed before the next model call, a failed
tool marks the plan honestly and lets the model replan, repeated search URLs are deduped, and
runs can be paused/resumed durably with the persona intact. All prior tests green; i18n parity.

### LIMITATIONS
- Summarization fires one model call per oversize turn (no persistent summary rollup across
  turns); recovery falls back to full history on failure.
- Generic pause only lands at graph checkpoints (pre-tool / post-agent), not mid-token.
- Research is orchestrator-level (intent -> brief -> search/summarize loop); no autonomous
  multi-round research planner yet.
- Memory capture is rule-based and English-only; no LLM extraction of implied facts.

### NEXT
**Phase C — browser session persistence + multi-tab research orchestration + task routing**.

---

## Phase C — Persistent browser sessions, secure credentials, artifacts — DONE (2026-10-08)

### WHAT WAS BUILT
- **Persistent per-user browser session** (`app/services/browser_session.py`): one Playwright
  Chromium (channel fallback chrome → msedge → default) per user, kept across turns, with a
  back/forward history stack (`can_back`/`can_forward` preview flags), stale-loop rebind,
  closed on terminal run status (`completed|stopped|error`), on `browser_close`, and by an idle
  safety-net sweep (`JALORA_BROWSER_SESSION_IDLE_SECONDS`, default 900 s).
- **14-tool browser suite** (`app/tools/browser.py`): open/navigate/back/forward/close/click/
  type/fill/select/scroll/upload/download/screenshot/extract, registered with Levels
  (navigation+extract+screenshot AUTO; close/click/type/select/scroll NOTIFY; fill/upload/
  download CONFIRM, `requires_internet` False only for close). Extract marks output
  `untrusted: True` and truncates at `MAX_PAGE_CHARS`. Screenshot/download persist PNG/files
  into the user's artifact folder via `app/services/artifacts.py`.
- **SSRF guard on every URL tool**: `browser_open`/navigate/download validate via
  `url_safety.validate_external_url` (private/reserved ranges, scheme allow-list, embedded
  credentials rejected) before the driver ever touches the address.
- **Login credentials (§18, encrypted)**: `app/models/credential.py` + `user_credentials`
  migration (alembic `a1b2c3d4e5f6`) + `app/services/credential_store.py` (domain
  normalization, Fernet encryption at rest via `JALORA_CREDENTIALS_ENCRYPTION_KEY`).
  `browser_fill` with a `@credential:username|email|password|pass|secret` marker resolves the
  domain from the session and either injects the stored username/password straight into the
  driver or fails structured `CREDENTIALS_NEEDED {domain, field}`. The graph then pauses for
  a `kind="credentials"` approval **without appending a ToolMessage**; the fill re-runs on
  resume. The secret is never included in prompts, tool args, audit logs, approvals, SSE, or
  responses; the raw row is verified to never contain the plaintext in tests.
- **Approval modal integration**: marker fills show a credential sub-form (`credential:
  {domain}` + `field` in the SSE approval payload); the first approval can carry
  `{credentials}` (one-step UX) or be approved empty to reach a dedicated credentials
  approval (two-step). Empty credentials are schema-rejected (422), so no infinite loop.
- **Artifacts + SSE**: `artifact` and `browser_status` SSE events stream screenshots and the
  live page (url/title/can_back/can_forward/session_open) as tools run; run/chat/subscribe
  responses carry `artifacts`. `GET /api/artifacts/{user_id}/{path}` serves files owner-only
  (403 otherwise) with a traversal-safe resolved-path check (404 outside the upload root).
- **Frontend**: ChatView renders inline screenshots (`artifact` events), a live browser status
  strip above the composer, and a credential sub-form in the approval modal (domain badge,
  username + password, "Save & approve", Enter submits); i18n keys EN + SW (parity kept).
- **Tests** (`tests/test_browser_phase_c.py`, 28 tests, all offline on `FakeDriver`):
  registry + permission gating, back/forward history across loop-free tool calls, SSRF-blocks,
  interaction recording, marker→CREDENTIALS_NEEDED + secret injection + “no plaintext
  anywhere” invariants, decrypt-failure structured error, domain normalization, screenshot/
  download artifacts + endpoint access-control/traversal, idle sweep, and two full E2E login
  flows through the real graph/approvals (two-step and one-step credential capture) plus a
  stream test asserting `browser_status` event emission.

### FILES CHANGED
`app/services/browser_driver.py` (real + fake drivers, `create_driver` factory),
`app/services/browser_session.py`, `app/services/artifacts.py` (new),
`app/services/credential_store.py`, `app/tools/browser.py`, `app/tools/__init__.py`,
`app/agents/{graph,prompts}.py`, `app/services/chat_service.py`, `app/api/{runs,artifacts,chat}.py`,
`app/api/__init__.py`, `app/errors.py`, `app/schemas/__init__.py`,
`app/models/{credential,agent}.py`, `alembic/versions/a1b2c3d4e5f6_add_user_credentials.py`,
`backend/requirements.txt` (+`playwright`, `cryptography`), `backend/.env.example`,
`tests/conftest.py`, `tests/test_browser_phase_c.py` (new),
`frontend-react/src/lib/{types,i18n}.ts`, `frontend-react/src/views/ChatView.tsx`,
`frontend-react/src/styles.css`, `docs/*`.

### DEPS
`playwright` (drivers), `cryptography` (Fernet for credential-at-rest encryption).

### TESTS
`python -m pytest tests -q` — **167 passed** (139 prior + 28 Phase C + browser_fill username/
password field fix), ~2 min, fully offline (fake driver + mock providers).
`npm run build` — clean (`tsc -b` strict + vite; EN/SW i18n parity 120/120).

### RESULTS
The browser is now a first-class, persisted, per-user resource with a complete automation
toolset, hardened URL handling, artifacts users can open in the running app, and a secure
login flow: credentials are requested through the approval UI, encrypted at rest, injected
directly into the browser, and verified never to leak into the conversation, logs, audit
trail, or SSE. E2E flows cover both the two-step (approve → enter credentials) and one-step
(credentials on first approval) paths.

### LIMITATIONS
- Real-browser tests need Chrome/Edge or `playwright install` (offline CI runs fake driver);
  `browser_available()` degrades auto→fake with a warning.
- Only one page per session (no multi-tab research orchestration yet — Phase D).
- No per-field partial credentials (username and password share one domain record).
- Idle sweep runs on session access paths; a long-running server with a fully silent user will
  only close sessions on the next activity/driver call.
- Credential marker only matches text fields named/placeholder `username|email|password|pass|secret`.
- `JALORA_CREDENTIALS_ENCRYPTION_KEY` derives from the JWT secret when unset (dev only —
  production requires an explicit key; generated with `Fernet.generate_key()`).

### NEXT
**Phase D — multi-tab research orchestration + task routing**.

---

## Phase D — Documents beyond PDF, media analysis, generated files — DONE (2026-10-08)

### WHAT WAS BUILT
- **Office/spreadsheet ingestion** (`app/services/extractors.py`, `document_service.py`):
  DOCX (python-docx), XLSX (openpyxl), PPTX (python-pptx) alongside PDF/TXT/MD/CSV/JSON.
  `extract_to()` returns text or `{"dataframes": [...], "tables": [...]}` where a sheet/range
  exists. Upload caps 25 MB general / 60 MB PDF, MIME sniffing with spoof/MIME-mismatch guard
  (40x), per-user workspace. OCR (`pytesseract`/`pdf2image`) degrades gracefully to a
  `"[scanned page - OCR not available]"` note when tooling is absent. Document rows now carry
  `progress` + `mime_type`.
- **`search_files` tool + search API** (`document_tools.py`, `GET /api/documents/search?q=`):
  filename substring search over the user's files (case-insensitive LIKE / ILIKE by dialect),
  returns `[{path, name, filesize, mtime, description, confidence_label}]`.
- **Media endpoints + storage** (`models/media.py`, `api/media.py`, `api/files.py`): one
  `POST /api/media` sniffs content to decide kind (image 15 MB / audio 150 MB / video 800 MB;
  anything else → honest `UNSUPPORTED_FORMAT` 400). Media rows are tagged
  `scope="media"|"attachment"` with `kind`, `uploader_id`, `provider`, `url`, `mime_type`,
  `size_bytes`, `media_note`; previews stream via the owner-only, traversal-safe
  `/api/artifacts/{user_id}/{path}` (nested `media/...` paths supported); transcribe/analyze
  results overwrite the `media_note` and are re-usable by later ingest calls.
- **Media analysis tools** (`media_tools.py`): `analyze_image` (data-URL vision embed via
  model routing), `transcribe_audio` (Whisper — `openai` endpoint / local whisper.cpp binary /
  deterministic `mock` transcripts), `analyze_video` (ffmpeg frame extraction + vision; honest
  `FFMPEG_UNAVAILABLE` when ffmpeg is missing). Tools return structured `media_id`-based data
  for future ingest.
- **Multimodal model routing** (`model_router.py`): `_to_openai_messages` passes
  `HumanMessage` list content (text + `image_url` blocks) straight through; `_to_anthropic_messages`
  converts LangChain image blocks to Anthropic `image` blocks (`_to_anthropic_image`,
  png/jpeg/webp/gif). `route(..., modality="image")` selects a vision-capable model; chat sends
  `modality` per-attachment type.
- **Chat media attachments**: `ChatRequest.media_ids` validated+sniffed in `api/chat.py`;
  `chat_service` loads rows, injects images as multimodal content and audio/video as a text note
  advertising `transcribe_audio(file_id=...)` / `analyze_video(file_id=...)` for the agent
  (`_load_attached_media`, `_user_message_content`, `_data_url_for`, streaming + non-streaming).
- **Generated files (§55)** (`generation_tools.py`): `create_pdf` / `create_docx` /
  `create_xlsx` / `create_csv` write into the user's artifact folder and return a structured
  artifact `{kind:"file", url:"/api/files/{user_id}/{name}", mime, title, size_bytes}`;
  `api/files.py` serves them owner-only (traversal-safe). Spec tables/data frames travel inside
  the tool message payload (base64 + text table), so models can transform a budget table from a
  DOCX into an XLSX.
- **Generic artifact capture** (`agents/graph.py`): any successful tool result carrying a
  `data.artifact` dict with a URL is appended to graph state and emitted as an `artifact` SSE
  event tagged with the tool name (`browser_status` emission still gated on `browser_*` tools).
- **Knowledge-base ingest (§69)** (`services/knowledge.py`, `document_tools.py`): pdf-reader
  gained `ingest_source` / `ingest_file` (Memory created behind `PROCESSING_ALWAYS_ALLOWED`
  policy), and `search_knowledge` now unions top-K Memory + DocumentChunk rows.
- **Frontend**: DocumentsView — drag-and-drop upload zone (office extensions added), per-item
  progress bars + mime badge, live cross-document search box; ChatView — paperclip classifies
  attachments: office/text files keep the classic document flow while image/audio/video go to
  `/api/media`, shown as preview chips (thumbnail / 🎵 / 🎬) with per-item remove, blocked send
  while a file still uploads, and `media_ids` sent with the message; generated-file artifacts
  render as download links; full EN/SW i18n parity (new keys: dropHere, or, docSearchPlaceholder,
  docSearchEmpty, mediaUploading, mediaOnlyPrompt, remove) + styles.

### FILES CHANGED
`app/services/extractors.py`, `app/services/document_service.py`, `app/services/media_service.py` (new),
`app/services/generation.py` (new), `app/models/media.py` (new), `app/api/media.py` (new),
`app/api/files.py` (new), `app/api/documents.py` (+search), `app/api/chat.py` (+`media_ids`),
`app/config.py` (+`outputs_dir`, whisper/ffmpeg flags), `app/schemas/__init__.py`,
`app/tools/{media_tools,generation_tools,document_tools}.py` (new), `app/tools/__init__.py`,
`app/services/{model_router,chat_service,knowledge}.py`, `app/agents/graph.py`,
`backend/requirements.txt` (+python-docx, openpyxl, python-pptx, reportlab),
`backend/.env.example`, `tests/conftest.py` (+JALORA_OUTPUTS_DIR),
`tests/test_phase_d_media.py`, `tests/test_phase_d_generation.py` (new),
`tests/test_documents.py` (unsupported-format test now uses a real unsupported ext),
`frontend-react/src/{lib/{types,api,i18n}.ts,views/{DocumentsView,ChatView}.tsx,styles.css}`,
`docs/*`.

### DEPS
`python-docx`, `openpyxl`, `python-pptx`, `reportlab` (office/spreadsheet/PDF generation).
Optional: `pytesseract`+`pdf2image` (OCR scans), ffmpeg (video frames; needs to be on PATH).

### TESTS
`python -m pytest tests -q` — **185 passed** (~2 min, fully offline). Phase D suite (18 tests)
covers: docx/xlsx/pptx ingest + DataFrame extraction + honest unsupported-format errors; MIME
sniff caps and spoof rejection; `/api/media` kind sniffing + caps; artifacts preview endpoint;
search_files + `/api/documents/search`; whisper openai/mock transcripts; analyze_image
monkeypatched-vision flow; ffmpeg-missing → `FFMPEG_UNAVAILABLE`; create_pdf/docx/xlsx/csv +
artifact capture + `/api/files` download access control; **full acceptance flow:
DOCX budget table → XLSX summary via the real graph** (event asserts `kind:file` artifact);
multimodal `media_ids` chat (vision agent + LLM text message) end-to-end.
`npm run build` — clean (`tsc -b` strict + vite), EN/SW parity maintained.

### RESULTS
Any uploaded document type an office suite produces can now be read, searched, and answered
from; the agent can analyze images, transcribe audio, and analyze video frames through the same
approval/policy machinery as other tools; and it can **produce** PDF/DOCX/XLSX/CSV outputs that
are surfaced as inline, downloadable artifacts. The acceptance flow (“read this DOCX, find the
budget table, make me an XLSX summary”) passes end-to-end through the real graph.

### LIMITATIONS
- OCR and video frame extraction need optional system tooling (pytesseract/pdf2image, ffmpeg);
  absent tooling yields honest fallback messages instead of errors.
- Whisper defaults to a deterministic offline mock unless an OpenAI key or whisper.cpp binary
  is configured; real audio transcription requires one of those.
- Audio/video chat attachments are presented to the agent as a text note + tool call rather than
  synthesized into a transcript at attachment time.
- Search is filename-level only (no full-text/chunk search in the UI yet — RAG query already
  exists per document).

### NEXT
**Phase E — voice assistant (§26, browser STT/TTS) + scheduling/notifications (§29–30) + coding
agent sandbox (§31) + integrations page (§44).**

---

## P8 - Phase E v1.: Email + integrations (A44, A19-20, A38, A60, A76)

### WHAT WAS BUILT
- **Data model** (models/integration.py, models/autoreply.py): `Integration` (id, user_id,
  provider, status, scopes JSON, config JSON, error, timestamps; unique `uq_integration_user_provider`)
  and `AutoReplyRule` (name, enabled, sender/topic patterns, time window, tone, template,
  `max_per_hour`/`max_per_day` with rolling counters `last_hour_bucket`/`hourly_count`/
  `last_day`/`daily_count`, requires_approval, priority). Alembic head
  `e2f4a6c8d0b1_add_integrations_and_autoreply` (down_revision `a1b2c3d4e5f6`).
- **Email provider service** (services/email.py): mock-first resolution (`auto` ? real Gmail only
  when a connected `gmail` Integration row AND `JALORA_GMAIL_CLIENT_ID/SECRET` exist, else a
  deterministic `email_mock` with a singleton mailbox + `seed_demo()` Kenya Commercial Bank
  application emails). Gmail = OAuth2 authorization-code flow (`state` stored as SHA-256 in config,
  callback verifies with `hmac.compare_digest` ? 403 `INVALID_STATE`), Fernet-encrypted
  `access_token`/`refresh_token` (auto-refresh + single 401 retry), unified async API
  `search_emails/read_email/list_threads/compose_draft/send_email/test_integration`, and a
  secret-scrubbing public serializer (strips `token|secret|key|password|state|nonce|auth`).
- **Auto-reply engine (A38)** (services/email_autoreply.py): `select_rule` (sender+topic match,
  time window, highest priority), `build_reply` (template with `{name}/{sender}/{subject}`,
  tone presets professional/friendly/brief), verdicts `no_rule|sent|requires_approval|rate_limited|
  denied|send_error`; every send goes through `policy.decide()` + the `send_email` tool policy
  (autonomy>=3 + automatic ? sends immediately), per-rule hour/day caps enforced by rolling buckets,
  all logged via ActivityLog `tool="auto_reply"`.
- **Inbound email (A60)** (services/email_inbound.py): deterministic keyword `classify_inbound`
  (application/billing/security/newsletter/calendar/general), best-effort LLM `summarize_email`
  with snippet fallback (rejects `Fake`-prefixed output), per-user `"?? Inbound email"`
  conversation message + ActivityLog, then auto-reply; duplicate `message_id` ? `duplicate` skip.
- **Tools** (	ools/email_tools.py): `search_email`/`read_email`/`list_threads`/`compose_email`
  (AUTO, draft-only) + `send_email` (CONFIRM). Registry now exports 41 tools.
- **APIs**: `/api/integrations` CRUD (+ `gmail/start` ? `authorization_url`, `gmail/callback`,
  `{id}/test`, `gmail/inbound` webhook; POST gmail ? `disabled` until OAuth, `email_mock` ?
  auto-connect, unknown ? 400). `/api/emails`: `search`, `threads`, `GET /{email_id}`,
  `compose` (draft-only � never sends), `send` (CONFIRM-gated ? `paused_approval` with
  `Approval(run_id="", tool_name="send_email", kind="tool")` + `send/{approval_id}/approve|reject`),
  auto-reply `rules` CRUD. `/api/health` now reports secret-free `capabilities`.
- **Frontend**: `IntegrationsView` (Gmail connect/reconnect via OAuth redirect, demo mailbox
  connect/test/disconnect, capability badges from health) + `EmailView` (search-inbox/read/compose,
  draft save, approval-paused send with inline approve/deny, auto-reply rules editor with tone/templates
  and per-day/hour caps); nav entries, EN/SW i18n keys, styles.

### FILES CHANGED
`app/models/{integration,autoreply}.py` (new), `app/models/__init__.py`,
`alembic/versions/e2f4a6c8d0b1_add_integrations_and_autoreply.py` (new),
`app/services/{email,email_autoreply,email_inbound}.py` (new), `app/api/{integrations,emails}.py`
(new), `app/api/__init__.py`, `app/api/system.py`, `app/tools/email_tools.py` (new),
`app/tools/__init__.py`, `app/config.py` (+gmail fields + `_empty_str_is_none`),
`backend/.env.example`, `app/schemas/__init__.py` (+Integration/Rule/Email schemas + health
capabilities), `tests/test_phase_e_email.py` (new), `tests/test_health.py`,
`frontend-react/src/{lib/{types,api,i18n}.ts,views/{IntegrationsView,EmailView}.tsx (new),
App.tsx,styles.css}`, `docs/*`. No new Python deps (httpx already present).

### TESTS
`python -m pytest tests -q` � **202 passed** (~2.4 min, fully offline). Phase E suite (17 tests):
tool permission/registration gates; integration CRUD + `email_mock` auto-connect + unknown-provider
400 + DELETE; `gmail/start` requires server creds; OAuth callback CSRF (tampered state ? 403,
Fernet round-trip of stored tokens); mock search `from:`/AND semantics + read + threads + compose
(never sends); API send ? approval pause ? approve/reject + autonomy-3 direct send; graph `send_email`
approval pause; auto-reply rule matrix (match/template/send, no_rule, rate caps day+hour, hold on
requires_approval); rules CRUD; inbound webhook signature + summarize + duplicate skip; **acceptance:
"find emails from my bank about my application and summarize them" through the real graph** (planner,
search_email, read_email, model summarization). `npm run build` � clean (tsc strict + vite).

### RESULTS
The acceptance flow works end-to-end on the demo mailbox (and against live Gmail once OAuth client
credentials are configured): the agent searches/reads/summarizes bank application emails; the user can
compose drafts, approve/reject any send through the existing approval machinery, and install
auto-reply rules that hold for review, respect per-hour/day caps, and log every action. Tokens are
encrypted at rest and the OAuth callback is CSRF-protected.

### LIMITATIONS
- Gmail live use requires an OAuth client id/secret (never committed) + the redirect URI
  `http://localhost:5173/api/integrations/gmail/callback` allowed in Google Console.
- Inbound webhook works via a signed push token when `JALORA_GMAIL_PUSH_TOKEN` is set; otherwise it
  is callable without auth (dev). Google Pub/Sub push setup is an ops task (P13+).
- Auto-reply dispatches through the same approval/policy machinery � so it can stall for approval or
  be rate-capped by design, not silent background magic.

### NEXT
**P9+**: voice assistant (A26), scheduling/notifications (A29-30), coding sandbox (A31), browser e2e
(P14), deploy targets.
---

## P9 - Phase F - Real phone calls & comms depth (§31-§33, §57)

### WHAT WAS BUILT
- **Models** (`models/call.py`): `Call` (user_id FK, nullable run/task/conversation FKs + indexes, to_phone, provider `mock|twilio`, purpose, status `dialing|in_progress|completed|failed|cancelled|no_answer`, sid, `script`/`answers`/`transcript` JSON, `summary`, `max_turns=20`, `max_duration_seconds=600`, start/end timestamps) and `CallTurn` (call_id FK index, role agent|person, kind say|speech|dtmf|system, text, Float `ts`). Alembic migration `f3a9c7d5e6f1_add_calls_and_turns.py` (new head, down_revision `e2f4a6c8d0b1`).
- **Tool identity plumbing**: `ToolContext` gained `task_id`/`conversation_id` (frozen dataclass); both `chat_service` set sites (fresh `execute_chat` and approval resume) bind it, so tools know which task/run/conversation they run for (resume uses `state["task_id"]` because AgentRun has no task_id column). Health capability `voice` = twilio creds present.
- **§33 script engine** (`services/call_script.py`): `ScriptNode`/`CallScript`, `build_script` (default + question flows; `intro` records `approved`, follow-ups record `q1..qN`, branch "yes" drives "if approved, ask ..."), `classify_answer` (DTMF 1/2/0 + word-aware yes/no/done: "no thanks" is a decline), `advance_script`, `validate_script`. Deterministic per-branch mock replies.
- **Voice service** (`services/voice.py`): provider resolution (auto -> twilio iff SID+token, else mock; FROM_NUMBER for real dials), `append_turn`/`finalize`/`_summarize` (§32 format "Person's response: .../Jalora summary: ..." + agreed note), `_conduct_mock` (synchronous, own SessionLocal re-loads the Call by id), `place_call` (mock runs to completion; twilio dials via existing `comms._twilio_request("Calls", ...)` with MachineDetection + TimeLimit + StatusCallbackEvent), TwiML `handle_answer`/`handle_gather`/`handle_status` (replay-based, branch-aware `_next_twiml`), `hangup`, `list_calls`, and a `media_stream` WebSocket loop (JSON transcript/DTMF in, say/summary out). `emit("call")`/`emit("call_turn")` events for UI updates. Approval policy is enforced upstream by the graph/tool layer - `place_call` never dials on its own.
- **Tool** (`tools/voice_tools.py`): `make_call(phone_number, purpose, questions)` CONFIRM/comms, reads `get_tool_context()` (user/task/run/conversation), returns `ToolResult` with the §32 summary + full view; registered (registry count 41 -> 42, verified).
- **API** (`api/calls.py`): auth `POST /api/calls` (201) + `GET /api/calls` (filter task_id/status/limit) + `GET /api/calls/{id}` + `POST /api/calls/{id}/hangup`; Twilio webhooks `answer`/`gather`/`status` (form-encoded, unauthenticated, reuse `comms._read_params`); WS `/api/calls/{id}/media`.
- **Config**: `call_max_turns`, `call_max_duration_seconds`, `calls_base_url` (+ `.env.example`).
- **Frontend (React)**:
  - Tasks view: live call panel on each task card - polls `/api/calls?limit=20` on the 3 s timer, shows status badge, demo tag, purpose, §32 summary, scrolling transcript (agent/person turns, live pulse), and Hang up for dialing/in_progress calls.
  - Chat approval modal: `make_call` renders phone + purpose + follow-up questions instead of raw JSON.
  - `lib/types.ts` `Call`/`CallTurnT`, EN+SW i18n keys (`calls`, `callStatus*`, `callApprovalModal`, `hangup`, ...), `styles.css` call-panel rules.
- **Test hermiticity fix**: `conftest.py` now also blanks `JALORA_GMAIL_PUSH_TOKEN`, `JALORA_GMAIL_CLIENT_ID`/`SECRET` - the developer's real `jalora/.env` was leaking them into the suite, making 4 Phase-E email tests fail with 403 `INVALID_WEBHOOK_SIGNATURE` / 200-vs-400 `PROVIDER_NOT_CONFIGURED`. (Pre-existing env-leak bug, not Phase E logic.)

### FILES CHANGED
`app/models/call.py` (new), `app/models/__init__.py`, `alembic/versions/f3a9c7d5e6f1_add_calls_and_turns.py` (new), `app/context.py`, `app/services/{call_script,voice}.py` (new), `app/services/chat_service.py`, `app/tools/voice_tools.py` (new), `app/tools/__init__.py`, `app/api/calls.py` (new), `app/api/__init__.py`, `app/api/system.py`, `app/config.py`, `backend/.env.example`, `tests/test_phase_f_voice.py` (new), `tests/conftest.py`, `frontend-react/src/{lib/{types,i18n}.ts,views/{ChatView,TasksView}.tsx,styles.css}`, `docs/{phase-log,gap-matrix}.md`.

### DEPS
None added (uses existing twilio-agnostic `comms._twilio_request`, fastapi WebSockets, sqlalchemy).

### TESTS
`python -m pytest tests -q` - **214 passed** (~4 min, fully offline) and `npm run build` clean. Phase F suite (12): registration/permission gates; mock lifecycle via the tool (transcript order, §32 summary shape, rows + Answers written, task/run linkage); invalid phone - structured `INVALID_PHONE` ToolResult; REST create/list/detail + ownership 404 + hangup no-op; script branch flow + `classify_answer` aliases + `validate_script` bad-target detection; **approval-before-dial** (paused approval, ZERO `Call` rows until approved, then complete with summary persisted on the task); rejection never dials; **§57 combined call -> send_whatsapp workflow** (two sequential approval gates, mock call + mock receipt, task summary shows both); health `voice` capability. The 4 previously-failing Phase-E email tests are green after the env-isolation fix.

### RESULTS
Acceptance flow "call +2547..., ask about my application, and if approved send details on WhatsApp" works end-to-end mock-first: planner -> `make_call` (approval) -> mock conductor -> §32 summary -> `send_whatsapp` (approval) -> receipt. Everything is persisted on the task and rendered live on the Tasks view (status, summary, transcript, hangup). The Twilio path is wired (TwiML answer/gather/status, media-stream WS) and dials as soon as real credentials are set - approval before any dial is guaranteed by the policy layer.

### LIMITATIONS
- Real dialing needs `JALORA_TWILIO_ACCOUNT_SID`/`AUTH_TOKEN`/`FROM_NUMBER` (still unset in dev); without them the mock provider completes calls offline the same way.
- Webhooks are unauthenticated in dev (like the SMS inbound hook); sign them push-token-style in production.
- The mock conductor is deterministic canned replies (no LLM on voice); real calls need the media-stream STT/TTS bridge (P10) or Twilio `<Gather>` speech.
- Call answering is driven by keyword classification from the §33 script - free-form follow-ups are recorded but branch on yes/no/done.

### NEXT
Browser STT/TTS + live inbound voice (P10), scheduling/notifications (P11), coding sandbox (P12).

---

## P11 — Scheduling, reminders, notifications & daily briefing

### WHAT WAS BUILT
- **Schedule + Notification models** (`models/schedule.py`, `models/notification.py`): `Schedule` holds
  `user_id`, `kind` (`once | cron | interval`), `spec` JSON, `task_template`, `notify_only`, `enabled`,
  `next_run`, `last_run`; `Notification` holds `text`, `kind`, optional `link`/`run_id`/`task_id`/`schedule_id`
  and `read_at`. Migration **`d1e4f8a2c6b3`** (down_revision `f3a9c7d5e6f1`).
- **`services/scheduler.py`**: `AsyncIOScheduler` + `SQLAlchemyJobStore` (UTC) started/stopped in the
  `main.py` lifespan. `parse_schedule(text, now=...)` is deterministic — ISO one-shot, "every N unit"
  intervals, weekday / daily / weekdays / weekends / period-of-day cron, raw 5-field crontab, one-shot "at HH:MM".
  Interval triggers are **anchored to the 1970 epoch grid** (or a user time-hint) so `next_run` is reproducible
  in a fixed `now`; APScheduler otherwise defaults `start_date` to real clock time. `sync_schedules` /
  `refresh_schedule` mirror enabled rows into jobs; `run_schedule` is the durable fire path (never raises).
- **Fire path**: `notify_only` reminders raise a `Notification`; scheduled tasks run the real pipeline via
  `chat_service.execute_chat` (real `Conversation` / `AgentRun` / `Task`), then notify
  `scheduled_task_completed` (with conversation link + excerpt), `approval_required`, or `scheduled_task_failed`.
  `once` schedules disable after firing; recurring ones recompute `next_run`.
- **L4 is honest**: scheduled runs honour the exact same per-tool policy engine as interactive chats — a
  scheduled CONFIRM tool pauses for approval unless the user already set that tool to `automatic` at L3+/L4.
  No blanket auto-approval exemption for "scheduled".
- **Tools (6, registry now 48)**: `schedule_task` (CONFIRM), `create_automation` (CONFIRM),
  `create_reminder` / `list_schedules` / `daily_briefing` (AUTO), `cancel_schedule` (NOTIFY, tag `destructive`).
- **`services/notifications.py`**: `notify`, `unread_count`, `list_notifications`, `mark_read`, `mark_all_read`.
- **REST**: `GET/POST /api/schedules`, `POST /api/schedules/{id}/toggle`, `DELETE /api/schedules/{id}`
  (422 on parse error, 404 otherwise); `GET /api/notifications`, `GET /api/notifications/unread-count`,
  `POST /api/notifications/{id}/read`, `POST /api/notifications/read-all`.
- **Frontend**: `SchedulesView` (list / create form / enable-disable / delete / next-run), sidebar
  `NotificationBell` (unread badge, polled unread-count, dropdown, mark read / read-all, browser
  `Notification` API permission on first click + push on new items). EN/SW i18n parity, styles, App wiring.
- **Daily briefing (§62)**: `daily_briefing` composes pending reminders + today's tasks + pending approvals
  (email/calendar slots once those sources land).

### FILES CHANGED
- Added: `app/models/schedule.py`, `app/models/notification.py`,
  `alembic/versions/d1e4f8a2c6b3_add_schedules_and_notifications.py`,
  `app/services/scheduler.py`, `app/services/notifications.py`, `app/tools/schedule_tools.py`,
  `app/api/schedules.py`, `app/api/notifications.py`, `tests/test_phase_g_schedules.py`,
  `frontend-react/src/views/SchedulesView.tsx`, `frontend-react/src/components/NotificationBell.tsx`.
- Edited: `app/models/__init__.py`, `app/tools/__init__.py`, `app/api/__init__.py`, `app/main.py`,
  `app/config.py`, `backend/.env.example`, `backend/requirements.txt`, `tests/conftest.py`,
  `frontend-react/src/App.tsx`, `src/lib/types.ts`, `src/lib/i18n.ts`, `src/styles.css`.

### DEPS
- Added `apscheduler==3.11.3` (already installed into the project venv).

### TESTS
- New `tests/test_phase_g_schedules.py` (29): parser determinism (fixed `now`, epoch grid, cron days, ISO,
  crontab, invalid), reminder + once fire, disabled/removed skip, durable task fire (completed),
  L2 approval-pause (no silent execution) vs L4 auto-run, cancel/toggle, schedules + notifications REST,
  daily briefing. Full suite: **243 collected, 240 passed**.

### RESULTS
- Backend 240 green; the 3 non-passing are `test_memory_v2.py` chroma tests blocked by a host
  Application-Control policy on the `grpc` DLL (environment, not code). Frontend `tsc -b && vite build` clean.
- E2E probe on a scratch DB: reminder fire -> notification; scheduled task fire -> completed + Task +
  `scheduled_task_completed` notification carrying the conversation link; `once` disables after firing.

### LIMITATIONS
- Scheduler runs in-process (no distributed worker); the job store is durable but a single process owns jobs.
- Browser Notification push requires the user to grant permission; otherwise the in-app bell is the fallback.
- Daily briefing covers reminders/tasks/approvals; calendar and email digests arrive with those integrations.
- Timezone is UTC by default (`JALORA_SCHEDULER_TIMEZONE`); per-user timezones are not yet modelled.

### NEXT
Coding agent sandbox (P12), then Redis/queue + deploy targets (P14).


## P12 - Voice interface (sections 46-48) - DONE (2026-10-09)

### GOAL
Speak a request and have Jalora answer aloud: composer mic (Web Speech API where
available, server-side STT fallback otherwise), per-message + automatic speaking
of replies, and a hands-free continuous mode with barge-in and mute/stop.

### DONE
- Backend fallback STT: POST /api/voice/transcribe (multipart `file`) with
  magic-byte sniffing and a 25 MB cap, reusing the whisper chain (openai ->
  local -> offline mock); GET /api/voice/config advertises capabilities.
  TTS stays in the browser (speechSynthesis); no backend TTS endpoint.
- Schemas VoiceSettings/TranscribeOut/VoiceConfigOut; ServiceUnavailableError
  (503); voice prefs persisted under user prefs via SettingsOut/SettingsPatch.
- Frontend: lib/voice.ts (SpeechRecognition + MediaRecorder fallback + speech
  synthesis), api.transcribeAudio, voice badges/toggles in ChatView (mic,
  continuous, mute, per-message speak, "Listening..." strip) and SettingsView
  (voice mode / auto-speak / continuous + voice picker). EN + SW i18n keys.

### FILES
- New: jalora/backend/app/api/voice.py; jalora/backend/tests/test_phase_h_voice.py;
  jalora/frontend-react/src/lib/voice.ts
- Edited: app/schemas/__init__.py, app/errors.py, app/api/__init__.py,
  app/api/settings.py; frontend src/lib/api.ts, src/lib/types.ts,
  src/lib/i18n.ts, src/views/ChatView.tsx, src/views/SettingsView.tsx,
  src/styles.css.

### TESTS
- test_phase_h_voice.py: 9 tests (config + auth, empty/non-audio rejection,
  mock transcription, transcript->chat bridge, voice settings defaults/patch).
- Full backend suite: 252 tests, all green on this host (3 chroma tests can be blocked where the host rejects the grpc DLL).
- Frontend: `npm.cmd run build` (tsc -b && vite build) clean.

### NOTES
- Web Speech API is Chrome/Edge/Safari; Firefox uses the MediaRecorder fallback.
- The whisper chain ends in an offline mock, so transcription is always available.
- Voice preferences are persisted server-side in user prefs["voice"].


### MANUAL CHECKLIST (browser voice)
- Settings -> Voice: enable, pick a voice; reload keeps the choice.
- Chat: click the mic, speak, release; the "Listening..." strip shows the partial
  transcript, then the request is sent and answered.
- Assistant messages show a speaker button; auto-speak reads new replies when on.
- Continuous mode: enable, speak, get a spoken answer, speak again; talking over
  the reply stops playback (barge-in). Mute/Stop halts audio immediately.
- Firefox (no Web Speech API): mic falls back to MediaRecorder ->
  POST /api/voice/transcribe and still works via the mock/whisper provider.


## P13 (Phase I) - Personal-assistant integrations (§63-68, §76)

### SUMMARY
- Added calendar (§63), drive (§64), contacts (§65), maps/places (§66),
  weather (§67) and translation (§68) capabilities, all mock-first so the
  assistant works offline and deterministically in tests, upgrading to real
  providers (Google Calendar/Drive, OpenStreetMap/OSRM, Open-Meteo, OpenAI)
  when configured/connected.
- Extended the integration registry (§76): GET /api/integrations/providers
  returns secret-free provider cards; connect/test/disconnect cover the new
  providers; /api/health advertises the new capabilities.

### CAPABILITIES
- Calendar: find_events (AUTO); create_event/update_event/delete_event (CONFIRM).
  create_event detects conflicts and proposes the next free slot so the agent
  can offer an alternative before the approval-gated write. Provider auto ->
  Google when a connected `calendar` integration carries tokens, else the
  local mock store.
- Drive: search_drive/read_drive_file (AUTO); create_drive_file/upload_drive_file
  (NOTIFY). Local-first store under data/drive/<user_id>/; Google adapter optional.
- Contacts: find_contact (AUTO); create_contact/update_contact (NOTIFY). Names
  now resolve to phone numbers so "text John the invite" works; comms tools
  (send_sms/send_whatsapp/place_voice_call) accept a contact name.
- Maps: find_place/get_directions/calculate_distance/find_nearby (AUTO).
  Keyless OSM Nominatim + OSRM with haversine distances; offline gazetteer mock.
- Weather: get_weather (AUTO). Keyless Open-Meteo with WMO code mapping;
  location resolution: request -> contact -> user prefs -> default.
- Translation: translate (AUTO). Provider `auto` -> OpenAI when a key is set,
  else a deterministic offline phrasebook (en<->sw/fr/es + identity fallback).

### REGISTRY (§76)
- app/services/integration_registry.py: provider_registry() renders 12 cards
  (gmail, calendar, drive, contacts, maps, weather, translate, twilio, search,
  ollama, db, redis) with configured/connected/keyless flags; connect() is
  mock-alias aware (calendar_mock -> calendar, drive_mock -> drive);
  test_connection() probes status. GET /api/integrations/providers is the API.

### DATA
- Models/tables: contacts, calendar_events, drive_files.
- Alembic migration e5b7c9d1f3a5 (down_revision d1e4f8a2c6b3) adds the three
  tables; verified `alembic upgrade head` on a clean SQLite database.

### FRONTEND
- IntegrationsView renders the registry as provider cards (name, description,
  status badge, Connect/Test/Disconnect). New ProviderCard type; EN + SW keys.

### TESTS
- test_phase_i_integrations.py: 17 tests - provider registry + auth, tool
  permissions, calendar create/conflict/alternative, contact name resolution
  feeding SMS, drive read/write, haversine + directions math, weather parsing
  and missing-location, translate round-trip, connect/test/disconnect and
  auto->mock fallback, and the acceptance chain (contacts -> calendar -> SMS).
- Full backend suite: all green (269 tests) on this host.
- Frontend: `npm.cmd run build` (tsc -b && vite build) clean.

### FILES
- New: app/models/contact.py, app/models/calendar.py, app/models/drive.py;
  app/services/contacts.py, calendar.py, drive.py, maps.py, weather.py,
  translate.py, integration_registry.py; app/tools/calendar_tools.py,
  drive_tools.py, contact_tools.py, maps_tools.py, weather_tools.py,
  translate_tools.py; tests/test_phase_i_integrations.py;
  alembic/versions/e5b7c9d1f3a5_add_contacts_calendar_drive.py
- Edited: app/config.py, app/models/__init__.py, app/tools/__init__.py,
  app/tools/comms.py, app/api/integrations.py, app/api/system.py,
  tests/conftest.py; frontend src/lib/types.ts, src/lib/i18n.ts,
  src/views/IntegrationsView.tsx.

### NOTES
- All new providers are mock-first; real network is only used when the provider
  is not `mock` and credentials/connection exist. Tests force mock via
  JALORA_*_PROVIDER=mock in conftest.
- Acceptance chain: "Schedule a meeting tomorrow 2 PM with John if he's free,
  and text him the invite" -> contacts resolve John, calendar books/tests
  conflicts, SMS resolves the number; covered end-to-end at the tool layer.

## P12b - Coding agent (sections 53-54) - DONE (2026-10-10)

### GOAL
- A per-user, sandboxed coding workspace owned entirely by Jalora: inspect,
  search, edit, run tests/lint and show the diff, with every mutation/execution
  approval-gated.

### DONE
- Sandbox core (app/services/sandbox.py): per-user root data/workspaces/<user_id>;
  resolve_path rejects .., absolute paths, drive prefixes, null bytes and
  escaping symlinks (PATH_ESCAPE/INVALID_PATH). Allowlisted run_command runs
  python/pytest/read-only git as an argv list WITHOUT a shell, under a
  wall-clock timeout and output cap, with a secret-free child env.
- Edit journal + backups: write_file snapshots the previous version, records a
  journal entry and produces a unified diff; pending_edits()/rollback() drive
  the diff view and undo.
- Code tools (app/tools/coding_tools.py): inspect_code/search_code/analyze_error
  are AUTO; edit_code/run_tests/run_linter are CONFIRM (approval-gated).
  edit_code accepts full content or old_string/new_string replacement.
- API (app/api/workspace.py): GET /api/workspace/tree, /file, /diff and
  POST /api/workspace/rollback, all owner-scoped and escape-guarded.
- Config: sandbox_enabled, sandbox_timeout_seconds, sandbox_max_output_chars,
  workspaces_dir (env override JALORA_WORKSPACES_DIR).
- Frontend: WorkspaceView panel (file tree, read-only viewer, pending-edit diff,
  per-file rollback); workspace types + EN/SW keys; nav entry.

### FILES
- New: app/services/sandbox.py, app/tools/coding_tools.py, app/api/workspace.py,
  tests/test_phase_j_coding.py.
- Changed: app/config.py, app/tools/__init__.py, app/api/__init__.py,
  app/schemas/__init__.py, tests/conftest.py, frontend src/lib/types.ts,
  src/lib/i18n.ts, src/views/WorkspaceView.tsx.

### TESTS
- tests/test_phase_j_coding.py: 19 passing (1 skipped: symlink not permitted on
  this host) - traversal/absolute/null-byte/symlink rejection, command allowlist
  + secret-free env, python execution, edit->diff->rollback (create/overwrite),
  lint/inspect/search/analyze, tool permission levels, /api/workspace auth +
  per-user isolation, and the acceptance chain (create failing test -> run ->
  fix via edit_code -> re-run -> show diff).
- Full backend suite: 371 passed, 3 skipped.
- Frontend: npm run build clean.

### NOTES
- Files change only through the CONFIRM-gated edit_code tool; the API is
  read-only plus rollback (a user-initiated undo). No shell is ever used and the
  child process never receives provider credentials.
- Acceptance "clone/inspect this folder, fix the failing test, run the tests,
  show me the diff" is covered end-to-end at the sandbox/tool layer.

### NEXT
- Phase K (learning & preferences, sections 27, 70-72): feedback pipeline,
  preference extraction, workflow-pattern memory.

---

## Math & Science — Phase 2 (broadened math + SciPy numeric engine) — DONE (2026-10-16)

### WHAT WAS BUILT
- **Numeric engine** (`app/services/math_numeric.py`): import-light SciPy/NumPy wrapper with
  `is_available/has_numpy/has_scipy`, `quad`, `root_brentq`, `solve_ivp`, `minimize_scalar`,
  `polyfit`, `correlation`, `normal_cdf`/`normal_pdf`, `binomial_pmf`/`binomial_cdf`,
  `poisson_pmf`/`poisson_cdf`. Every entry point degrades to `None` when SciPy/NumPy is absent
  (math still works offline on the SymPy path).
- **Broadened `math_engine.solve()`** with new handlers, wired ahead of the calculus/equation
  branches:
  - **Linear algebra** (`_try_linalg`): determinant, inverse (square + singular guard), rank,
    transpose, eigenvalues, matrix power, matrix multiplication of two literals, dot/cross product.
  - **Number theory** (`_try_number_theory`): gcd/hcf/lcm, prime factorisation, divisors/factors,
    primality, Euler totient (`phi`), modular inverse, `mod`, Fibonacci, factorial.
  - **Series** (`_try_series`): finite/infinite `sum ... from ... to ...` (incl. `infinity`) and
    Taylor/Maclaurin/power-series expansion.
  - **ODEs** (`_try_ode`, `_sympify_ode_side`, `_ode_numeric`): symbolic `dsolve` (1st/2nd order,
    `y'`, `y''`, `dy/dx`) and numeric first-order IVPs via `solve_ivp` (RK45).
  - **Complex numbers** (`_try_complex`): modulus, conjugate, argument, real/imaginary part,
    complex arithmetic.
  - **Distributions** (`_try_distribution`): normal (pdf/cdf), binomial, Poisson probabilities.
  - **Regression** (`_try_regression`): least-squares polynomial fit + Pearson correlation
    (compact `_coef` formatting so numerical dust prints as `0`).
- **Classifier** (`classify_math`): new cues (`_MATRIX_LITERAL`, `_LINALG_CALL/_VERB`, `_NT_CALL/_VERB`,
  `_SERIES`, `_ODE_CUE/_ODE_PRIME`, `_COMPLEX_CUE/_LITERAL`, `_DIST_CUE`, `_REG_CUE`), new intents
  (`linalg`, `number_theory`, `series`, `ode`, `complex`, `distribution`, `regression`), and
  expanded `_MATH_WORDS`. Each cue requires corroboration (number/operator/equation) so prose
  ("prime minister", "the matrix is a good movie", "normal day") is never routed.
- **Reused numeric engine**: `_numeric_integral` now calls `math_numeric.quad` (SymPy `N` fallback).

### FILES CHANGED
`app/services/math_engine.py`, `app/services/math_numeric.py` (new), `tests/test_math.py`,
`docs/plan-math-science.md`, `docs/phase-log.md`.

### DEPS
None new — `scipy`/`numpy` were already pinned (scipy==1.18.1, numpy==2.5.3).

### TESTS
`python -m pytest tests/test_math.py -q` → **110 passed** (72 prior + 38 new Phase 2), ~105 s.
New coverage: numeric engine availability + `quad`; determinant/inverse (+singular)/eigenvalues/
transpose/rank/matmul/dot; gcd/lcm/factorisation/divisors/primality/totient/mod-inverse/mod/
fibonacci/factorial; infinite + geometric series and Taylor expansion; symbolic and numeric ODEs;
complex modulus/conjugate/argument/arithmetic/parts; normal/binomial/Poisson probabilities;
regression + correlation; per-intent classifier cues and prose-not-routed guards.

### RESULTS
Jalora now computes (not guesses) matrices, number theory, series, ODEs, complex numbers,
distributions and regression, each with a verified step-by-step result; the existing 72 tests
remain green and prose is still excluded. Fixes found during wiring: matrix-literal row regex
captured the outer bracket; ODE interval indices were applied to the already-shortened body;
`\d`+`\b` classification cue failed on multi-digit operands; "sum of ..." was being swallowed by
the arithmetic word-problem path (series handler now runs first).

### LIMITATIONS
- Wordy Phase 2 prompts ("solve the differential equation y' = y", Taylor "around ... up to ...",
  "line of best fit for ...") are computed but not always `direct`, so they route through the model
  like other narrative prompts; bare forms fast-path directly.
- ODE numeric path supports first-order IVPs over a stated interval; higher-order numeric IVPs and
  systems of ODEs are symbolic-only.
- Matrix input is literal (`[[..],[..]]`); no symbolic matrix variables or general linear systems.
- Correlations/fits are rounded to ~6 significant figures for display.

### NEXT
- **Phase 3** — `services/physics_engine.py` + `data/physics_constants.json`.

---

## Math & Science — Phase 3 (physics engine + constants) — DONE (2026-10-16)

### WHAT WAS BUILT
- **Constants table** (`jalora/data/physics_constants.json`): 25 physical constants
  (CODATA 2018 / SI 2019) with `name`, `symbol`, `value`, `unit`, an `exact` flag and
  alias phrases — `c`, `h`, `hbar`, `e`, `k_B`, `N_A`, `R`, `F`, `G`, `g_0`, `eps_0`,
  `mu_0`, `k_e`, `m_e`, `m_p`, `m_n`, `u`, `alpha`, `R_inf`, `sigma`, `eV`, `au`, `atm`,
  `a_0`, `mu_B`. Loaded once from `settings.data_dir` (falling back to `BASE_DIR/data`).
- **Domain engine** (`app/services/physics_engine.py`), import-light and `pint`-backed:
  - `is_available()`, `all_constants()`, `constant(query)`, `lookup_constant(text)`;
  - `solve(text) -> MathResult | None` — recognises physics problems and computes them,
    returning the same verified `MathResult` contract as `math_engine` (`intent="physics"`,
    `verified=True`). Returns `None` (never fabricates) for anything unrecognised.
  - **Dimensional solver**: extracts `<number> <unit>` quantities, matches each to a
    formula variable **by dimension** (`pint.check`), converts to SI, and evaluates. A
    wrong unit combination is rejected instead of yielding a wrong number.
  - **Formulas**: Newton's gravitation, Coulomb's law, kinetic energy, gravitational
    potential energy, weight, work, mechanical power, electric power, momentum, density,
    pressure, Newton's second law, acceleration, wavelength, wave frequency.
  - **Solve-for-missing relations**: Ohm's law (`V=IR`) and the ideal gas law (`PV=nRT`)
    compute whichever single variable is absent.
  - **Constant lookup**: name/alias phrase matching, plus symbol-only forms guarded behind
    an explicit query frame. Single-word aliases (`gravity`, `planck`, ...) are accepted
    only in a query frame, so prose ("the gravity of the situation") is never read as a
    constant.
- **Wiring** (`math_engine.solve()`): new `_try_physics()` helper runs the physics engine
  *before* the SymPy classifier, since physics prose ("what is the speed of light") is not
  a SymPy expression. It is a strict no-op for non-physics text, so ordinary math is
  unaffected. Full intent-routing/briefs for science text remains Phase 7.

### FILES CHANGED
`jalora/data/physics_constants.json` (new), `app/services/physics_engine.py` (new),
`app/services/math_engine.py` (physics hook), `tests/test_math.py`,
`docs/plan-math-science.md`, `docs/phase-log.md`.

### DEPS
None new — `pint` (0.25.2) was already pinned in Phase 1.

### TESTS
`python -m pytest tests/test_math.py -q` → **126 passed** (110 prior + 16 new Phase 3).
New coverage: engine availability + constants table, constant lookup (result + prose guard),
kinetic/potential energy, weight, work, power, momentum, density (g/cm³ → kg/m³), pressure,
second law, acceleration, wavelength, frequency, Ohm's law (solve for current), gravitational
and Coulomb forces, ideal gas moles, and no-hijack guards for plain math/units/prose.

### RESULTS
Jalora now computes (not guesses) dimensional physics problems and reports verified physical
constants, all offline via `pint`; the 110 prior math tests remain green. A weak-alias guard
prevents common-English constant names from being matched in prose.

### LIMITATIONS
- The formulas are a curated first set; broader coverage (kinematics with displacement,
  energy of springs, magnetic/electrical circuits beyond Ohm's law, thermodynamics) is future
  work.
- Physics text is computed but not yet `direct` in `classify_math`, so science questions route
  through the model like other narrative prompts until Phase 7 adds the `science` intent.
- Requires `pint`; without it `is_available()` is `False` and `solve()` returns `None`
  (the math/SymPy path is unaffected).
- Constant values are rounded to CODATA precision for display; results are shown to ~6
  significant figures.

### NEXT
- **Phase 4** — `services/chemistry_engine.py` + `data/periodic_table.json`.

## Math & Science — Phase 4 (chemistry engine + periodic table) — DONE (2026-10-16)

### WHAT WAS BUILT
- **Periodic table** (`jalora/data/periodic_table.json`): all **118 elements** with `z`,
  `symbol`, `name`, standard atomic weight (`mass`; bracketed/most-stable mass numbers for
  the synthetic elements), `category`, `group` (null for the lanthanide/actinide f-block
  rows) and `period`. Loaded once from `settings.data_dir` (falling back to `BASE_DIR/data`).
- **Domain engine** (`app/services/chemistry_engine.py`), JSON + SymPy, import-light:
  - `is_available()` (always `True`; SymPy only needed for balancing), `all_elements()`,
    `element(query)` — lookup by symbol (case-insensitive), name, common alias
    (`aluminum`→`aluminium`, `cesium`→`caesium`, `sulphur`→`sulfur`, …) or atomic number;
  - `parse_formula(formula) -> {symbol: count} | None` — nested groups (`Ca(OH)2`),
    hydrates (`CuSO4·5H2O`), unicode subscripts (`H₂O`); rejects unknown symbols;
  - `molar_mass(formula) -> float | None` — sums standard atomic weights (g/mol);
  - `balance_equation(equation) -> (lhs, rhs, coeffs) | None` — reduced integer nullspace
    via SymPy; returns `None` for unbalanceable/ambiguous reactions (never guesses);
  - `solve(text) -> MathResult | None` (`intent="chemistry"`, `verified=True`) covering
    molar mass, atomic number/mass, symbol↔name, `element N`, equation balancing, and
    mole↔mass conversions; common compound names (`water`, `glucose`, `table salt`, …)
    resolve to formulas.
- **Wiring** (`math_engine.solve()`): new `_try_chemistry()` helper runs *before*
  `_try_physics()` and the SymPy classifier (a balance equation contains `=`); both are
  strict no-ops for unrelated text, so ordinary math is unaffected. Full `science`
  intent-routing/briefs remains Phase 7.

### FILES CHANGED
`jalora/data/periodic_table.json` (new), `app/services/chemistry_engine.py` (new),
`app/services/math_engine.py` (chemistry hook), `tests/test_math.py`,
`docs/plan-math-science.md`, `docs/phase-log.md`.

### DEPS
None new — pure Python + JSON; SymPy (1.14.0) already present and used only for balancing
(`periodictable`/`chempy` remain intentionally unused with graceful fallback).

### TESTS
`python -m pytest tests/test_math.py tests/test_tools.py -q` → **142 passed**;
`tests/test_math.py -q` → **136 passed** (126 prior + 10 new Phase 4).
New coverage: engine availability + 118-element table, element lookup (symbol/name/alias/
number), formula parsing (groups, hydrates, unicode subscripts, invalid), molar mass
(H2O, C6H12O6, Ca(OH)2, CuSO4·5H2O, unknown), atomic number/mass + symbol/name results,
equation balancing (combustion, iron oxide, methane), unbalanceable rejection, mole↔mass
conversions, and no-hijack guards proving chemistry does not intercept plain math/physics.

### RESULTS
Jalora now computes (not guesses) chemistry: element facts, molar masses, balanced
equations and mole/mass conversions, fully offline; the 126 prior math/physics tests
remain green. `assistant` verifies: glucose 180.156 g/mol, Ca(OH)2 74.092 g/mol,
CuSO4·5H2O 249.677 g/mol, `Fe + O2 = Fe2O3` → `4 Fe + 3 O2 -> 2 Fe2O3`.

### LIMITATIONS
- Balancing requires SymPy; without it `balance_equation` returns `None` while lookup and
  molar-mass still work.
- Reactions with more than one independent balance (ambiguous stoichiometry) are refused
  rather than guessed; half-reactions/redox and limiting-reagent/stoichiometry word
  problems are future work.
- Compound-name resolution is a curated vocabulary, not exhaustive IUPAC naming.
- Chemistry text is computed but not yet `direct` in `classify_math`, so it routes through
  the model like other narrative prompts until Phase 7 adds the `science` intent.

### NEXT
- **Phase 5** — derivations + pedagogical levels (primary…professor).

## Math & Science — Phase 5 (pedagogical levels + deterministic derivations) — DONE (2026-10-16)

### WHAT WAS BUILT
- **Pedagogy engine** (`app/services/pedagogy.py`) — the depth ladder
  `primary → highschool → university → stem → postgrad → professor`:
  - `LEVELS` / `LevelSpec` (per-level `label`, `rank`, model `directive`, offline
    `header`), `LEVEL_ORDER`;
  - `normalize_level(value)` — canonicalises config/arg values (case-, hyphen- and
    alias-insensitive: `kid`→primary, `undergrad`→stem, `PhD`→postgrad, unknown→`auto`);
  - `infer_level(text)` — reads a level from the question ("explain like I'm 5",
    "for a professor", "for a university student"); evaluated highest-rank first so an
    explicit request for rigour is never watered down;
  - `resolve_level(value, question)` — explicit config/arg wins, else inferred from the
    question, else `auto`;
  - `model_directive(level)` — the instruction handed to the active model;
  - `render(result, level, question)` — **deterministic, no-model** explanation: a
    level-appropriate derivation header prepended to the authoritative
    `math_engine.format_reply` block. `auto` (and any non-ok/error result) returns the
    plain block, so the default output is byte-for-byte unchanged.
- **Wiring** (`app/services/science_explain.py`): the level directives moved here from a
  private `_LEVEL_HINTS` dict (single source of truth), the effective level is resolved
  once (`resolve_level(level or settings.math_level, question)`), and the offline fallback
  is now `pedagogy.render(...)` — so "show the working" adapts to the level **even with no
  model configured** (spec item 9). The model still writes the prose prefix and the
  verified block is always appended unchanged.

### FILES CHANGED
`app/services/pedagogy.py` (new), `app/services/science_explain.py` (uses pedagogy),
`tests/test_math.py`, `docs/plan-math-science.md`, `docs/phase-log.md`.

### DEPS
None new — pure Python, reuses the existing `math_engine.format_reply`.

### TESTS
`python -m pytest tests/test_math.py tests/test_tools.py tests/test_chat.py -q` →
**171 passed**; `tests/test_math.py -q` → **146 passed** (136 prior + 10 new Phase 5).
New coverage: level normalization (case/hyphen/alias/unknown), level inference
(primary/highschool/university/postgrad/professor/auto), explicit-over-inferred
resolution, model directives, level-aware offline rendering (header preserved + answer
intact, professor/auto == plain block, failure block never headerised), inference from the
question inside `render`, `explain_math` offline level-awareness, and two end-to-end
chat fast-path tests (configured `JALORA_MATH_LEVEL=highschool` → header on the offline
reply; `auto` → no header).

### RESULTS
The same computed answer is now explained across the full depth ladder, and the working
adapts deterministically when no model is available. Existing behaviour is unchanged at
the default `auto` level (verified by an equality assertion against `format_reply`).

### LIMITATIONS
- `render` frames the engine's existing steps per level rather than re-deriving them; the
  step *content* is level-independent (only the header/framing and the model *directive*
  change). Meaning-sensitive rewriting is the model's job (Phase 7 briefs/i18n).
- Level is currently sourced from config/`JALORA_MATH_LEVEL` (and question inference); a
  per-request/user-selected level selector in the UI is Phase 7.
- `science` intent routing for narrative prose is still Phase 7; only `direct` math uses
  the level-aware fast-path today.

### NEXT
- **Phase 6** — `services/plotting.py` + `tools/plot_tool.py` + frontend rendering.


## Math & Science — Phase 6 (offline plotting: function/series charts + artifacts) — DONE (2026-10-16)

### WHAT WAS BUILT
- **Plotting service** (`app/services/plotting.py`) — headless, offline, compute-first:
  - forces matplotlib onto the `Agg` backend at import (no display/GUI), renders a
    `Figure` to **PNG bytes** (chat artifact, shown inline) **and** an **inline SVG**
    string (crisp vector) via the OO canvas API (no global `pyplot` state);
  - `is_available()` — true only when matplotlib + numpy are importable **and**
    `settings.plots_enabled`; function plots additionally require SymPy;
  - `plot_function(expr, x_min, x_max, points, title)` — parses `y = …` (or a bare
    expression) with the same safe SymPy transforms as `math_engine`, `lambdify`s to
    numpy over `linspace`, and renders; the single free variable must be `x`;
  - `plot_series(x_values, y_values, …)` — explicit (x, y) data series;
  - **bounds/guards:** `MAX_POINTS=4000`, `MAX_SPAN=1e6`, increasing-range check, and a
    module `threading.Lock` (matplotlib is not thread-safe); domain gaps (`log(x)` over
    negatives) render as gaps, not crashes; complex-valued results are refused;
  - **honest failure:** unparseable expressions / bad ranges / extra variables return
    `PlotResult(ok=False, error=…)` — never a fabricated chart;
  - `parse_request(text) → (expression, x_min, x_max) | None` — recognises explicit
    "plot/graph/draw/sketch/chart …" phrasings and "from a to b" / "between a and b"
    bounds (including `pi`/`2pi`), defaulting to `[-10, 10]`.
- **Plot tools** (`app/tools/plot_tool.py`) — `plot_function` and `plot_data`
  (`PermissionLevel.AUTO`, category `math`, tags `plot`): render, write the PNG to
  `data/uploads/<user_id>/plots/`, and return `ToolResult.ok(summary, {"artifact": info})`
  where `info` is `kind="plot"`, `mime="image/png"`. The agent graph already collects any
  tool result carrying `data["artifact"]` and emits an `artifact` event, so the chart
  reaches the chat with **no graph change**. Registered via `app/tools/__init__.py`.
- **Artifact URL fix** (`app/services/artifacts.py`) — `artifact_info` now emits the
  path relative to the user folder as a **POSIX** string (`as_posix()`), so sub-folder
  artifacts (e.g. `plots/plot_*.png`) produce a correct `/api/artifacts/<uid>/plots/…`
  URL on Windows too (previously a backslash leaked in).
- **Frontend** (`frontend-react/src/views/ChatView.tsx`) — a `kind === "plot"` artifact is
  treated as an inline image (it already matched via `image/png`, now explicit).
- **Prompt** (`app/agents/prompts.py`) — `MATH_BRIEF` gains rule 6: for plot/graph
  requests call `plot_function`/`plot_data` and never describe a plot that was not
  rendered.

### FILES CHANGED
`app/services/plotting.py` (new), `app/tools/plot_tool.py` (new), `app/tools/__init__.py`,
`app/services/artifacts.py`, `app/agents/prompts.py`,
`frontend-react/src/views/ChatView.tsx`, `tests/test_plotting.py` (new),
`docs/plan-math-science.md`, `docs/phase-log.md`.

### DEPS
None new — `matplotlib==3.10.8` was already pinned in `requirements.txt`; numpy/SymPy
already present. matplotlib is forced onto the `Agg` headless backend.

### TESTS
`python -m pytest tests/test_math.py tests/test_tools.py tests/test_chat.py tests/test_plotting.py -q`
→ **185 passed** (146 math + 6 tools + 19 chat + 14 new plotting).
New coverage: service availability; PNG (magic bytes) + SVG render; domain-gap handling;
unparseable/inverted/oversized/extra-variable rejections; series shape validation; all
`parse_request` phrasings + non-plot negatives; tool artifact descriptor (kind/mime/POSIX
URL, file on disk); structured failures (`PLOT_ERROR`, `TOOL_EXCEPTION` without context,
`PLOT_UNAVAILABLE` when disabled).

### RESULTS
An explicit plot request or a model-invoked `plot_function`/`plot_data` call now produces a
real PNG chart (and inline SVG) served by the owner-only artifacts endpoint and rendered
inline in chat. Fully offline; a bad request yields an explicit failure, not a guess.

### LIMITATIONS
- Plotting is exposed as **tools** (model-invoked) plus `parse_request` for a future
  fast-path; auto-routing of "plot …" messages and the `science` intent are **Phase 7**
  (currently a plot request reaches the tool only when the active model calls it, or the
  message classifies as `math` and the model follows `MATH_BRIEF` rule 6).
- One function/variable (`x`) per chart; multi-curve overlays, polar/3-D, and data
  ingestion from uploaded files are not yet supported.
- No interactive/zoom UI — the artifact is a static PNG (plus an SVG string that is not
  yet surfaced through the `diagrams` channel).

### NEXT
- **Phase 7** — `science` intent + briefs + i18n + "show working"/level selector; wire the
  plot fast-path and surface the inline SVG.

## Math & Science — Phase 7 (science intent + briefs + i18n + level/show-working) — DONE (2026-10-16)

### WHAT WAS BUILT
- **`science` intent** (`app/agents/intent.py`): new `_SCIENCE` regex (physics/chemistry/
  narrative-science terms — energy, kinetic, gravity, acceleration, ohm, circuit, photon,
  molecule, mole, molar, atom, electron, acid, reaction, `H2O`/`CO2`/`O2`, plus the domain
  words physics/chemistry/science). Priority is now
  `scheduling > coding > research > task > science > math > chat`, so "write an essay about
  chemistry" stays `task`, "latest physics news" stays `research`, a bare `2+2` stays `math`,
  and domain questions (molar mass, Ohm's law, balancing) classify as `science`.
  `IntentResult` docstring updated.
- **Science tool set** (`app/tools/routing.py`): `_SCIENCE = _MATH | {plot_function, plot_data}`
  mapped to the `science` intent; the `chat` intent now binds `_SCIENCE` too, so a "plot …"
  request in the default mode has the plot tools available. `route_request`/docstrings updated.
- **`SCIENCE_BRIEF`** (`app/agents/prompts.py`): compute-first physics/chemistry brief —
  always call `calculate` (auto-detects physics/chemistry) and `verify_math`; show
  formula → substitution → units → `> **Answer:**`; never guess; ask when a value is missing;
  use `plot_function` for graphs; match the user's level. Wired in `chat_service.py`
  (`elif intent.intent == "science": system_prompt += SCIENCE_BRIEF`).
- **Answer-only rendering**: `math_engine.format_answer(result)` — the final
  `> **Answer:**` blockquote only (no numbered steps / LaTeX derivation); error/incomplete
  results fall back to `format_reply` so an honest failure is never hidden.
  `pedagogy.render(..., show_working=False)` returns `format_answer(result)` (no level header,
  no steps); default `True` is unchanged.
- **Per-request `show_working`**: `science_explain.explain_math(..., show_working=None)` — when
  `False` it returns the answer-only block **without any model call**; `None` falls back to the
  new config flag. `app/config.py` gains `math_show_working: bool = True`
  (`JALORA_MATH_SHOW_WORKING`).
- **Per-request `math_level` + `show_working` plumbing**: `ChatRequest` gains
  `math_level: str | None` and `show_working: bool | None`; `api/chat.py` forwards both to
  `stream_chat`/`execute_chat`, which thread them into the math fast-path's `explain_math`
  call. Both default to `None`, so every other caller (workspace, comms, scheduler) is unchanged.
- **Frontend** (`frontend-react/src/views/ChatView.tsx`): a compact `math-controls` row beside
  the mode selector — a level `<select>` (auto/primary/highschool/university/stem/postgrad/
  professor) and a "show working" checkbox, both persisted in `localStorage`
  (`jalora_math_level`, `jalora_show_working`) and sent in the chat body. i18n keys added in
  `lib/i18n.ts` (en + sw): `mathLevel`, `levelAuto`…`levelProfessor`, `showWorking`. CSS in
  `styles.css`.

### FILES CHANGED
`app/agents/intent.py`, `app/tools/routing.py`, `app/agents/prompts.py`,
`app/services/chat_service.py`, `app/services/math_engine.py`, `app/services/pedagogy.py`,
`app/services/science_explain.py`, `app/config.py`, `app/schemas/__init__.py`,
`app/api/chat.py`, `frontend-react/src/views/ChatView.tsx`, `frontend-react/src/lib/i18n.ts`,
`frontend-react/src/styles.css`, `tests/test_math.py`, `docs/plan-math-science.md`,
`docs/phase-log.md`.

### DEPS
None new — pure Python + existing frontend stack.

### TESTS
`python -m pytest tests/test_math.py -q` → **165 passed** (146 prior + 19 new Phase 7);
`tests/test_chat.py tests/test_tools.py -q` → **25 passed**; frontend `tsc -b` clean.
Full suite: **9 failed, 731 passed, 6 skipped, 3 errors** — byte-identical failure set to the
pre-existing baseline (`test_model_router` provider drift, `test_browser_phase_c` approval JSON,
`test_memory_v2` missing `chromadb`), +19 new passes.
New coverage: `science` intent classification (7 domains) + no-hijack guards (bare math stays
math; research/task win by priority); routing binds compute+plot tools for `science` (and chat);
`SCIENCE_BRIEF` compute-first invariants; `format_answer` is answer-only and falls back for
errors; `pedagogy.render(show_working=False)`; `explain_math(show_working=False)` never touches
the model (a router that raises on any attribute access); end-to-end `/api/chat` tests for a
per-request `math_level` override and a per-request `show_working=false` answer-only reply.

### RESULTS
Narrative physics/chemistry questions now route to the compute-first science brief with the
calculation and plot tools bound; the user can pick the explanation depth and turn "show
working" off per request, in which case only the verified final answer is returned (and no model
call is made). Defaults (`auto` level, `show_working` on) leave existing behaviour byte-for-byte
unchanged; the pre-existing suite failure set is untouched.

### LIMITATIONS
- `science` intent only governs the brief + bound tools; physics/chemistry prose is still not
  `direct` in `classify_math`, so it routes through the model (the model must call `calculate`).
- The plot **fast-path** ("plot sin(x)" answered without a model) and surfacing the inline SVG
  through the `diagrams` channel remain future work (tools cover plotting today).
- Level/`show_working` are per-request UI values; they are not yet persisted as user
  preferences server-side (only `localStorage`).
- `show_working=false` returns the answer block only; the model narrative (when enabled) is not
  produced at all — a deliberate "answer only" choice.

### NEXT
- **Phase 8** — golden problem sets per domain/level + a verification harness; then wire the
  plot fast-path and the inline-SVG `diagrams` channel.

## Math & Science — Phase 8 (golden problem sets + verification harness) — DONE (2026-10-16)

### WHAT WAS BUILT
- **Golden dataset** (`data/golden_problems.json`): 64 curated natural-language problems with
  independently-known answers, each `{id, domain, level, query, expect}`. Spans 16 domains
  (arithmetic, algebra, calculus, geometry, trigonometry, units, number_theory, combinatorics,
  linalg, complex, statistics, regression, probability, series, physics, chemistry) and all 6
  pedagogical levels (`primary…professor`). `expect` supports `ok`, `intent`, `exact`,
  `exact_contains`, `numeric`, `numeric_approx` + `numeric_tol`, `units`, `verified`.
- **Verification harness** (`app/services/verification.py`): `load_golden()` resolves the file via
  the data dir then package data (same pattern as the physics/chemistry loaders); `check_problem()`
  runs `math_engine.solve` and checks every declared `expect` field **and** the level-aware render
  (`pedagogy.render` must contain the verified `**Answer:**` block, plus the level header when that
  level defines one; `format_answer` must be answer-only); `run_all()` returns a `Report` with
  totals, `by_domain()`, `by_level()`, `summary()`; `render_report()`; and a CLI
  (`python -m app.services.verification`) that prints the report and exits non-zero on any failure.
  Engine exceptions are captured as failures, never raised.
- **Tests** (`tests/test_verification.py`, 14): dataset integrity (unique ids; every problem
  declares `ok`), coverage (all `pedagogy.LEVEL_ORDER` levels + the required domains), the whole
  set passes end-to-end, report breakdown totals are consistent, and — guarding the guard — the
  comparator actually detects a wrong `exact`, an `ok` mismatch (garbage query the engine
  declines), a `units` mismatch, a `numeric_approx` miss, and an engine exception (a monkeypatched
  `solve` that raises).

### FILES CHANGED
`data/golden_problems.json` (new), `app/services/verification.py` (new),
`tests/test_verification.py` (new), `docs/plan-math-science.md`, `docs/phase-log.md`.

### DEPS
None new — pure Python; imports only the existing compute engines + `pedagogy`.

### TESTS
`python -m pytest tests/test_verification.py -q` → **14 passed**;
`tests/test_math.py tests/test_verification.py -q` → **179 passed** (165 + 14).
CLI: `python -m app.services.verification` → **64/64 (100%)**, every domain and level full.
Full suite: **9 failed, 745 passed, 6 skipped, 3 errors** — byte-identical failure set to the
pre-existing baseline (`test_model_router` provider drift, `test_browser_phase_c` approval JSON,
`test_memory_v2` missing `chromadb`), +14 new passes.

### RESULTS
The compute engine is now regression-locked across domains and levels: any change that alters a
golden result or its level-aware rendering fails a single, readable check (library or CLI). The
harness is offline and model-free, so it runs in CI without provider keys. No production behaviour
changed — the dataset and harness are purely additive.

### LIMITATIONS
- The golden set is hand-curated sample-based evidence, not exhaustive: it covers representative
  cases per domain/level, not the full input space (fuzzing/property tests remain future work).
- `expect` matches the engine's *canonical* output strings (e.g. `x = 3`, `x**3/3`); where output
  formatting is decimal or free-form the entry uses `numeric_approx` / `exact_contains` instead of
  brittle equality.
- Some natural phrasings the engines do **not** yet handle were deliberately excluded from the set
  (e.g. `72 F to C` — needs `celsius`/`fahrenheit`; `solve 2x + 3 = 11` with a space; `factorial of
  5` mis-parses to `factorial(5*f*o)`; `number of moles in 36 g of water`). These are captured here
  as known engine gaps rather than papered over by the golden set.

### NEXT
- Wire the plot **fast-path** ("plot sin(x)" answered without a model) and surface the inline SVG
  through the `diagrams` channel.
- Optionally extend the harness with CLI flags (domain/level filter, JSON output) and address the
  engine gaps listed under LIMITATIONS.


## Math & Science — Phase 9 (professional, downloadable plots) — DONE (2026-10-16)

### WHAT WAS BUILT
- **Professional plotting** (`app/services/plotting.py`, rewritten): a clean modern style applied
  through a temporary `matplotlib.rc_context` (so the process-wide defaults are never mutated),
  a colour palette, `FIG_SIZE=(8.0, 4.6)`, `DPI=150`; every successful plot now renders **three
  formats** — high-resolution **PNG**, **SVG** and **PDF** (`PlotResult.pdf` + `formats()`), via
  `FigureCanvasAgg` / `FigureCanvasSVG` / `FigureCanvasPdf`. New chart kinds: `plot_scatter`,
  `plot_bar`, `plot_histogram` (alongside `plot_function` / `plot_series`). A shared
  `_prepare_pair` validator keeps the series/scatter paths consistent.
- **Downloadable artifacts**: `save_exports(user_id, prefix, title, result)` writes all exports of
  one figure under `data/uploads/<user_id>/plots/`, sharing a single base name so the PNG/SVG/PDF
  are recognisably the same figure (PNG is the inline primary, `kind="plot"`; the vectors are
  `kind="plot-vector"`). `artifacts.artifact_info` now returns `file_name` and
  `download_url` (`url + "?download=1"`); `GET /api/artifacts/...` accepts `?download=1` and
  responds `Content-Disposition: attachment` with the original filename (images stay inline by
  default).
- **Plot tools** (`app/tools/plot_tool.py`): `plot_function` / `plot_data` now return
  `data["artifact"]` **and** `data["artifacts"]` (the full export list); `plot_scatter`,
  `plot_bar`, `plot_histogram` added and registered. `app/agents/graph.py` now also collects the
  `artifacts` list from tool results (deduping the primary by URL), so agent-driven plots
  surface every export.
- **Plot fast-path** (`app/services/chat_service.py`): a `plot/graph/draw/sketch/chart/visualize
  <expr>` turn is rendered **locally** (model `matplotlib`, no provider call), persisted like the
  math fast-path (AgentRun + Task + assistant Message + `log_activity`), and streamed back with an
  `artifact` event per export, a `diagram` event (inline SVG), and a `done` response carrying the
  artifacts + diagrams.
- **Frontend** (`views/ChatView.tsx`, `lib/types.ts`, `lib/i18n.ts`, `styles.css`): artifacts are
  now loaded through the **authenticated** artifacts endpoint (Bearer → blob → object URL) since a
  plain `<img src>` would 401; every artifact gets a **Download** button (uses `download_url` +
  `file_name`); diagrams render with a Download button (SVG/canvas saved client-side). `Artifact`
  gains `file_name`/`download_url`; en/sw string `download`; professional artifact/diagram styles.
- **Tests**: `tests/test_plotting.py` +7 (PNG/SVG/PDF export, all chart kinds, mime map, shared
  basename + download metadata, `render_request`/`svg_diagram`, tools return all formats, chart
  tools registered); `tests/test_plot_downloads.py` (new, 5, e2e): fast-path renders + emits
  artifacts, inline-vs-attachment dispositions, owner-only access, no fast-path hijack of plain
  math, and the agent graph captures all formats from the plot tool.

### FILES CHANGED
`app/services/plotting.py`, `app/services/artifacts.py`, `app/api/artifacts.py`,
`app/tools/plot_tool.py`, `app/agents/graph.py`, `app/services/chat_service.py`,
`tests/test_plotting.py`, `tests/test_plot_downloads.py` (new),
`frontend-react/src/views/ChatView.tsx`, `frontend-react/src/lib/types.ts`,
`frontend-react/src/lib/i18n.ts`, `frontend-react/src/styles.css`,
`docs/plan-math-science.md`, `docs/phase-log.md`.

### DEPS
None new — reuses `matplotlib` (already pinned) and its bundled Agg/SVG/PDF backends.

### TESTS
`pytest tests/test_plotting.py tests/test_plot_downloads.py -q` → **26 passed**.
Frontend: `npx tsc -b` → clean (exit 0).
Full suite: **9 failed, 758 passed, 6 skipped, 3 errors** — same pre-existing failure set
(`test_model_router` provider drift ×7, `test_browser_phase_c` approval JSON ×2, `test_memory_v2`
missing `chromadb` ×3), +13 new passes. `plotting.py` lint-clean apart from the accepted
`# noqa: BLE001` broad-except convention.

### RESULTS
Charts are now professional and genuinely downloadable: a "plot sin(x)" turn is answered offline,
returns a crisp PNG shown inline, and offers SVG/PDF vector downloads (single base name), with the
same inline-SVG diagram surfaced through the existing `diagrams` channel. The chat UI fetches plot
images through the authenticated endpoint (fixing a latent 401 on `<img src>`) and adds Download
buttons. No existing behaviour changed — the math fast-path still wins for non-plot math, and the
golden verification set is unaffected.

### LIMITATIONS
- The fast-path handles function plots only (scatter/bar/histogram are tool/skill-driven); the
  parser still recognises plot verbs, so a request like "draw the cubic x³−2x" is plotted, not
  solved.
- SVG/PDF exports are generated server-side per request (no caching); artifact retention follows
  the existing uploads policy (no new cleanup added).
- The `<img src>` 401 fix is applied in `ChatView`; other views that render artifacts directly
  should adopt the same authenticated loader.

### NEXT
- Optionally cache rendered exports and add a "copy LaTeX / export data (CSV)" affordance for
  charts.
- Extend the plot fast-path to data/statistics phrasings ("plot the distribution of …") and add
  golden plot cases to the verification harness.





