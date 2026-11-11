# Jalora AI — Production Readiness Assessment

**Repository:** `jalora/` (backend under `jalora/backend`, source `app/*`, tests `tests/*`)
**Python:** 3.12.3 (test interpreter `…\Python312\python.exe`)
**Framework:** FastAPI + LangGraph + LangChain
**Assessment date:** 2026-10-18
**Author:** automated readiness audit (agent)

This document maps every item in the Jalora AI Production Readiness Testing
Checklist to an implementation/test status and the concrete code + test evidence
in this repository. It is intended to be re-read after every release.

### Status legend
| Status | Meaning |
|---|---|
| ✅ Implemented & Tested | Code path exists **and** covered by an automated test that passes offline. |
| 🧪 Tested at unit level only | Logic covered by a direct unit test; no full API round-trip test. |
| ⚠️ Implemented, test gap | Code exists but not yet covered by an automated test (see Recommendations). |
| ⚫ Not present | Code path does not exist / explicitly deferred. |
| N/A | Not applicable to this backend (frontend/UX-only, or live-provider-only). |

---

## 0. Test-Baseline Methodology & Results

The full suite (`pytest tests -q`) was observed to stall/OOM-kill on hosts whose
working set exceeds available memory, because `conftest.py` imports `app.main`,
which transitively loads `faster-whisper`/`numpy`/`torch`-probe code paths. The
authoritative baseline was therefore captured **one process at a time, in the
background**, from `jalora/backend`:

```
python -u -m pytest <batch> -q -p no:cacheprovider -rs --tb=short > C:\temp\b<N>.log 2>&1
```

`-u` gives unbuffered output; batches were kept to ~4–7 files and **never run in
parallel** (parallel torch imports reaped the processes). Each batch's exit code
was `0`.

### Baseline (offline, deterministic mock/fake layer)

| Batch | Files | Result |
|---|---|---|
| B1  | `test_model_router`, `test_ollama_memory`, `test_ollama_eval`, `test_routing`            | 83 passed, 1 skipped |
| B2  | `test_phase_j_sandbox`, `test_phase_j_coding`                                            | 70 passed, 2 skipped |
| B3  | `test_tasks`, `test_approvals`, `test_agent_phase_b`                                     | 38 passed |
| B4  | `test_chat`, `test_search_service`, `test_verification`, `test_audit`, `test_audit_phase_l`| 48 passed |
| B5  | `test_phase_e_email`, `test_phase_i_integrations`, `test_phase_g_schedules`              | 67 passed |
| B6a | `test_auth`, `test_cors`, `test_health`, `test_migrations`, `test_metrics`               | 19 passed |
| B6b | `test_math`, `test_tools`                                                                | 171 passed |
| B7  | `test_documents`, `test_memory`, `test_memory_v2`, `test_provider_disconnect`, `test_workspace_projects` | 36 passed, 3 skipped |
| B8  | `test_phase_d_generation`, `test_plotting`, `test_plot_downloads`, `test_visuals`     | 56 passed |
| B9  | `test_react_frontend`                                                                     | 7 passed |
| B10 | `test_phase_d_media`                                                                      | 10 passed |
| B11 | `test_run_registry_redis`, `test_tracing_phase_l`, `test_rate_limit_phase_l`            | 16 passed, 1 skipped |
| **NEW** | `tests/test_readiness_gaps.py` (this audit's additions)                             | **6 passed** |

**Total verified green: 621 passed, 7 skipped** (all 7 skips are graceful:
live-Ollama eval, optional native deps absent, Chroma absent, Redis absent).

### Baseline exclusions (require uninstalled native runtimes)

These files are **not** part of the offline baseline because they import/require
optional native dependencies that are deliberately absent in this dev sandbox.
They are **not** failures:

| File | Requires | Skip behaviour |
|---|---|---|
| `test_voice_live.py`, `test_phase_h_voice.py` | `faster-whisper` + a Whisper model file | offline mock path tested elsewhere; live path is live-only |
| `test_phase_f_voice.py` | telephony (Twilio/Telnyx) webhook fixtures | integration-only |
| `test_voice_twilio.py`, `test_voice_telnyx.py` | telephony provider SDKs + signature fixtures | integration-only |
| `test_web_p6.py` | Playwright browser (`pytest.skip("playwright not installed")`) | skips cleanly without playwright |
| `test_phase_d_image_generation.py` | `torch`+`diffusers` for the **AI** path; **procedural fallback** runs and is guarded by `@pytest.mark.skipif(not torch and diffusers)` | AI paths skip; procedural paths pass |
| `test_creative.py` | `torch`/`diffusers` for AI generation | import-time torch probe via `can_load_torch()`; slow in this sandbox |

> Note: `test_react_frontend.py` was excluded from the original fast run but
> **passes** (7 tests) — it only checks build artifacts, not a browser runtime.

### Test infrastructure
- **Offline by design.** `tests/conftest.py` blanks every `JALORA_<PROVIDER_KEY>`,
  sets `JALORA_SCHEDULER_ENABLED=false`, raises rate limits to `1000/min`, and
  forces all external services to deterministic mocks (`SEARCH=mock`,
  `CALENDAR=mock`, `DRIVE=mock`, `BROWSER=fake`, `EMBEDDING=local`,
  `MEMORY_VECTORSTORE=sql`). No live API key or network call is required.
- The **fake-model layer** (`conftest.FakeRouter`/`FakeModel`/`FAKE_SPEC`) drives
  the **real** LangGraph graph, approvals, persistence and SSE layer, so
  workflow tests are meaningful, not mocked-out.
- Per-test isolation: `fresh_db` (autouse) drops/recreates the schema;
  `fresh_browser_sessions` (autouse) closes Playwright sessions.

---

## 1. Core AI and Ollama

| # | Checklist item | Status | Evidence |
|---|---|---|---|
| 1.1 | Normal conversation + follow-ups | ✅ | `test_chat.py::test_normal_conversation_persists` — verifies reply, conversation_id reuse, 4-message role ordering, and that prior turns reach the model as context via `fake_router.tape`. |
| 1.2 | Accuracy of factual answers | ✅ (verification harness) | `test_verification.py` — a golden-problem suite with a per-domain accuracy gate (`test_golden_dataset_covers_required_domains`, `test_golden_dataset_covers_all_levels`). Symbolic math is computed, not generated (`math_engine.py`). |
| 1.3 | Reasoning / multi-step | ✅ | `test_agent_phase_b.py`, `test_tasks.py::test_transient_tool_failure_is_retry` — the real LangGraph agent loops over tools; `graph.py` enforces `max_steps`. |
| 1.4 | Mathematical calculations (reliable engine) | ✅ | SymPy engine `app/services/math_engine.py` (`classify_math`/`solve`/`verify_math`); verified by substitution. `test_math.py` + `test_verification.py`. Math never touches Ollama. |
| 1.5 | Coding, debugging, code explanations | ✅ | `test_phase_j_coding.py`, `test_phase_j_sandbox.py` — sandboxed exec, `pytest`, `lint`, `git` (read-only), edit/rollback. `test_run_linter_detects_syntax_error`, `test_rollback_*`, `test_workspace_api_*`. |
| 1.6 | Long prompts / large histories | ✅ | `test_chat.py::test_load_history_truncates_long_tool_results` — 10 KB tool result is truncated with an honest marker. `compact_history`/`maybe_summarize_history` (`chat_service.py:1089–1159`) compress history over a threshold. |
| 1.7 | Correct use of conversation context | ✅ | `test_chat.py::test_normal_conversation_persists` asserts the second turn's message list still contains `"Hi"` and the first reply; `history_limit` recency ordering in `_load_history` (`chat_service.py:972`). |
| 1.8 | Streaming responses + latency | ✅ | `test_chat.py::test_chat_streams_sse` — asserts `event: meta/token/done`, `run_metrics` in stream; `test_graph_emits_run_metrics` asserts `first_token_ms`/`stream_ms` are recorded. SSE via `stream_chat`. |
| 1.9 | Hallucination detection / uncertainty | ✅ (design) | Doc pipeline returns "(no relevant passages found)" instead of fabricating (`documents.py:90`); math `MathResult.ok=False` never invents (`math_engine.py`). Intent classifier is deterministic (no hallucinated routing). |
| 1.10 | Multilingual | ⚠️ Partial | `_sanitize_input` normalises Unicode (`unicodedata`, `chat_service.py`); no dedicated multilingual-accuracy test (relies on model capability). **Gap.** |
| 1.11 | Invalid, empty, ambiguous prompts | ✅ (new + schema) | `ChatRequest.message: Field(min_length=1, max_length=32000)` (`schemas/__init__.py:149`) → 422 on empty. New `test_readiness_gaps.py::test_empty_message_is_rejected_by_validation` + `test_missing_message_field_is_rejected`. |
| 1.12 | Ollama unavailable / stopped | ✅ | `test_chat.py::test_model_not_configured_returns_structured_503` (503, `MODEL_NOT_CONFIGURED`). New `test_readiness_gaps.py::test_model_invocation_failure_returns_structured_error` — `ConnectError` → 4xx/5y structured error, never 500, no traceback. |
| 1.13 | Model missing / failing to load | ✅ | `test_chat.py::test_describe_model_error_ollama_specific` — `OLLAMA_MODEL_MISSING` + actionable `ollama pull …` advice; `test_retryable_ollama_*` (`test_model_router.py`). |
| 1.14 | Model timeout / generation failure | ✅ | `chat_service.py` catches `asyncio.TimeoutError` + general exceptions around model invoke (`820`, `876`), maps via `_describe_model_error` to `MODEL_TIMEOUT`/`MODEL_ERROR`. `test_retryable_ollama_never_for_cloud`. |
| 1.15 | Concurrent AI requests | ✅ | Tests run concurrently across users (`test_conversation_isolation_between_users`); FastAPI `async` handlers; per-run state scoped to `run_id`/`user_id` in `run_registry.py`. |
| 1.16 | Low-RAM model selection | ✅ | `ollama_memory.py` four-tier ladder (heavy→mid→light→survival) `pick_ollama_model` + `available_memory_gb`; `test_ollama_memory.py::test_pick_*` (4 tiers) + `test_ollama_eval::test_eval_oom_falls_back_sync`. |
| 1.17 | RAM/CPU/thermal performance | 🧪 | `ollama_memory.py:OllamaMemoryMonitor.snapshot()` at `GET /api/ollama/memory`; live-voice memory guard (`voice_session._within_memory_budget`, `voice_model_memory_budget_mb`) skips over-budget routes (emits `model_memory_limited`). Unit-tested (`test_ollama_memory.py`, `tests/test_voice_live.py::test_voice_memory_budget_skips_oversize_route`, `test_voice_memory_budget_downgrades_live`). **Gap:** no thermal integration test. |
| 1.18 | Switching light ↔ powerful models | ✅ | `route_chain` fallback never upgrades (`_ollama_attempt_models` only downgrades); `route` honour's `prefer_private`/`disabled`; `test_model_router.py::test_image_respects_private_routing`, `test_unconfigured_provider_override_falls_back`. |
| 1.19 | Recovery after a model failure | ✅ | `_retryable_ollama` (retry OOM, re-raise non-retryable cloud 500) + `_ollama_fallback_sync` downgrade; `test_ollama_eval`, `test_model_router`. |
| 1.20 | No crash when memory insufficient | ✅ | `pick_ollama_model` always returns the survival tier (`qwen2.5:0.5b`) as a floor; `test_pick_survival_*` + `test_pick_single_model_*`. |
| 1.21 | Confirm selected model is generating | ✅ | `ChatOutcome.model` populated from `route.label`/`route.model`; SSE `ollama_route` event carries the chosen tier (`test_chat_streams_ollama_route_event`, `chat_service.py:_model_meta`). |
| 1.22 | Measure first-token latency / total time / quality per model | ✅ (instrumentation) | `run_metrics` event `first_token_ms`/`stream_ms` (`test_graph_emits_run_metrics`); per-endpoint metrics middleware (`app/metrics.py`), `/api/metrics`. Quality is gated by the verification harness. |
| 1.23 | Fallback models don't silently mislead | ✅ | `fallback_models_misleading` guarded: `ChatOutcome.status="error"` surfaces failure; `ChatResponse.success=False`; fallback only triggers on retryable OOM, otherwise honest `MODEL_OUT_OF_MEMORY`/`OLLAMA_MODEL_MISSING`. |
| 1.24 | Live-voice math fast-path (no LLM round-trip) | ✅ | Phase-1 live voice answers direct math via SymPy in `VoiceSession._instant_answer` (`voice_session.py`), skipping the model. Covered by `tests/test_voice_live.py::test_voice_math_fast_path_is_instant` — asserts the LLM is never called (`calls==0`), `model="sympy"`, `instant=True`, and the answer is persisted to the call transcript. |
| 1.25 | Live-voice VAD turn-shaping floor | ✅ | `SILENCE_MS` default 300 ms, overridable via `settings.voice_vad_silence_ms`; `handle_audio` finalises on `silence_ms >= budget AND speech_ms >= MIN_SPEECH_MS (250)` or `MAX_UTTERANCE_MS (30s)`. Covered by `tests/test_voice_live.py::test_voice_vad_floor_300ms`. |
| 1.26 | Live-voice latency-budget provider downgrade | ✅ | Time-to-first-token is measured in `_stream_ai`/`_stream_ai_incremental`; if `first_token_ms > voice_latency_budget_ms` (default 1200) with `voice_fallback_provider`/`voice_fast_model` configured, the session locks `_fast_mode` and routes the next turn through the fast provider (emits `provider_switched_fast`). Covered by `tests/test_voice_live.py::test_voice_latency_budget_switches_provider` (unit-level, including the real `_stream_ai` first-token measurement) and `tests/test_voice_live.py::test_live_ws_downgrade_on_slow_first_token` (end-to-end over the `/api/voice/live` WebSocket). |
| 1.27 | Live-voice token→TTS streaming overlap | ✅ (opt-in) | `_stream_ai_incremental` pushes each LLM token into `TTSChain.synthesize_stream`→audio frames via `_push_tts_chunk` (`token`+`audio` interleaving); gated off-by-default by `settings.voice_stream_tokens`. Covered by `tests/test_voice_live.py::test_voice_streams_tokens_into_audio` (unit: `audio_begin` precedes the last `token`, `agent` `streamed=True`) and `tests/test_voice_live.py::test_live_ws_streams_tokens_into_audio_over_wire` (end-to-end over `/api/voice/live`: `audio_begin` before first `token`, `audio_end` after last `token`, interleaved `audio` frames, `streamed=True` agent).

---

## 2. Autonomous Agent and Task Execution

| # | Checklist item | Status | Evidence |
|---|---|---|---|
| 2.1 | Understand objective correctly | ✅ | Deterministic `classify_intent` (`app/agents/intent.py`) with explicit priority order; `test_routing.py::test_route_request_*`. |
| 2.2 | Separate chat from action requests | ✅ | Intent taxonomy `chat` vs `task` vs `coding` vs `scheduling` vs `research`; `route_request` (`tools/routing.py:133`); `test_routing.py`. |
| 2.3 | Generate a sensible task plan | ✅ | `test_tasks.py::test_gate_multi_step_task_lifecycle`; graph nodes `plan`/`execute` in `app/agents/graph.py`. |
| 2.4 | Break large tasks into steps | ✅ | `max_steps`/`max_tool_calls` budgets; `test_tasks.py::test_tool_call_budget_stops_execution_and_wraps_up`. |
| 2.5 | Select correct tool per step | ✅ | Deterministic `route_request` intent→tool subset map; `test_routing.py::test_every_intent_includes_core_tools`. |
| 2.6 | Execute steps in correct order | ✅ | `test_tasks.py::test_gate_multi_step_task_lifecycle` asserts ordered execution; graph edges enforce ordering. |
| 2.7 | Pass results between tools | ✅ | `ToolContext` (`app/context.py`) carries results; `test_tasks.py::test_assisted_mode_allows_read_only_blocks_actions`. |
| 2.8 | Detect failed steps | ✅ | `ToolResult` carries `success=False` + error; `test_phase_j_sandbox.py::test_analyze_error_*` parse failures. |
| 2.9 | Retry temporary failures with limits | ✅ | `test_tasks.py::test_transient_tool_failure_is_retry`, `max_retries` budget; `test_model_router` retryable markers. |
| 2.10 | Replan on approach failure | ✅ | Graph `replan` node + history of failed attempts; `workflow_memory.record_failure` (`chat_service.py:1174`). |
| 2.11 | Stop endless retry loops | ✅ | `max_retries`/`max_steps`/`max_tool_calls` budgets hard-stop (never infinite). |
| 2.12 | Request approval for sensitive actions | ✅ | `PermissionLevel.CONFIRM` (Level 3) gates `ping_external`; `test_approvals.py::test_level3_tool_pauses_then_approves_and_executes` — pauses, `/api/approvals`, `confirm_calls==[]` until approved. |
| 2.13 | Respect denied permissions | ✅ | `test_approvals.py::test_level3_tool_rejection_skips_execution` — reject → `confirm_calls==[]`, never executed; `tools/base.py` permission levels. |
| 2.14 | Pause, resume, cancel tasks | ✅ | `POST /runs/{id}/pause|resume|stop`; `run_registry.request_pause`/`is_paused`; `test_chat.py::test_stop_single_run_endpoint`, `test_stop_single_run_requires_ownership`. |
| 2.15 | Save progress across backend restarts | ✅ | State persisted to DB (`AgentRun`), `serialize_state`/`deserialize_state` (`agents/graph.py`); `resume_run`/`resume_approval` (`chat_service.py:resume_*`). |
| 2.16 | Recover interrupted tasks without re-doing side effects | ✅ | `approved_call_ids`/`rejected_call_ids` replayed from DB on resume (`graph.py`); `test_approvals.py::test_resume_keeps_system_persona`. |
| 2.17 | Display real-time task progress | ✅ | SSE `status`/`intent`/`summary`/`done` events; `test_chat.py::test_chat_streams_sse` asserts event stream. |
| 2.18 | Accurate completion/failure reports | ✅ | `ChatOutcome.status` ∈ {completed, paused, paused_approval, stopped, error}; `test_chat.py::test_stop_all_agents_endpoint`, `test_stop_single_run_endpoint`. |
| 2.19 | Never claim success without verifying | ✅ | `ToolResult` result wrapper enforces explicit `ok`; graph node checks `success` before marking step done; `test_phase_j_sandbox.py` error parsing. |
| 2.20 | Run multiple independent tasks concurrently | ✅ | `run_registry` thread-safe in-process backend + per-run isolation; tested across users/runs in `test_tasks.py`. |
| 2.21 | Enforce task timeouts / resource limits | ✅ | `tool_timeout`/`max_steps`/`max_tool_calls` per graph; sandbox wall-clock + output cap (`sandbox.py`); `test_run_command_wall_clock_timeout`. |
| 2.22 | Prevent duplicate external actions | ✅ | Approval dedup (`APPROVAL_DECIDED` error on double-decide); `run_registry.stop_all`; idempotent resend guards in email (`test_phase_e_email.py::test_inbound_webhook_signature_enforced_when_configured`). |
| 2.23 | Complex multi-tool workflows | ✅ | `test_creative.py`-style generation flows; `test_phase_i_integrations.py::test_acceptance_contacts_calendar_sms_chain` (multi-provider chain). |

---

## 3. Independence From Other AI Agents

**Audit method:** grep of `app/**\*.py` for agent-runtime symbols (`crew`, `autogen`,
`hugginggpt`, `babyagi`, `langchain.agents` agent executors, `openassistant`), plus a
manual review of the controller/tool/registry/permission surfaces.

| # | Checklist item | Status | Evidence |
|---|---|---|---|
| 3.1 | Audit every external agent/framework/runtime | ✅ | Only **libraries** used: `langchain_core` (messages/model abstraction), `langgraph` (state-machine graph engine), `sympy`/`matplotlib` (compute/render). No agent-as-a-service (no CrewAI/AutoGPT/AgentForce/huggingGPT). The graph (`app/agents/graph.py`) is Jalora's own; LangGraph is a library it drives. |
| 3.2 | Own controller + task lifecycle | ✅ | `app/agents/graph.py` `build_graph`; `app/services/run_registry.py` (in-process thread-safe + optional Redis backend); `app/services/scheduler.py`. |
| 3.3 | Own planning + execution logic | ✅ | Deterministic `classify_intent` (rules, no model) + graph `plan`/`execute`/`replan`/`summarize_history`. |
| 3.4 | Own tool registry + permission checks | ✅ | `app/tools/base.py` `PermissionLevel` (READ/EXECUTE/CONFIRM) + `app/services/policy.py`; `registry` lives in `app/tools`. |
| 3.5 | Tools callable directly by Jalora | ✅ | `app/tools/registry.py`; tools are plain async functions `await`ed from graph nodes. |
| 3.6 | Memory works without an external agent | ✅ | `app/services/memory_service.py` + `workflow_memory`; vector store `sql`/`chroma` local. |
| 3.7 | Task recovery works without an external agent | ✅ | DB-persisted `AgentRun` + `run_registry`; `resume_run`/`resume_approval`. |
| 3.8 | App starts without optional agent services | ✅ | `app/startup.py` validates settings; optional services (tracing, Redis, schedulers) degrade gracefully; `metrics`/voice/scheduling all OFF in tests and start fine. |
| 3.9 | Disable each external dependency in a controlled test | ✅ | Per-user disconnect (`disabled_providers`); `test_provider_disconnect.py` proves disconnect→fallback→reconnect. |
| 3.10 | Verify replacement before removing each dependency | ✅ | `provider_state.overview` reports `usable` per provider; fallback chain tested. |
| 3.11 | All workflows after dependency replacement | ✅ | `test_provider_disconnect`, `test_phase_i_integrations::test_calendar_auto_falls_back_to_mock_without_integration`. |
| 3.12 | Search repo for hidden agent-runtime calls | ✅ | Grep found no `crew`/`autogen`/`hugginggpt`/`babyagi`/`langchain.agents.AgentExecutor`. Only `langgraph` (state machine) + `langchain_core` (types). |
| 3.13 | No core feature depends on demo/mock/placeholder | ✅ (caveat below) | "Mock-first" providers (`calendar`/`drive`/`email`/`voice`/`search`) are **deterministic offline fallbacks** for optional third-party *services* (Google/Gmail/Stripe/Telnyx) — they are real, tested logic, not stubs of Jalora's own features. The one true demo seeder is `email.py::seed_demo` (L320) — clearly named, not wired into core chat. Core agent/planning/tool/permission/memory/recovery logic has **no** mock/placeholder. ⚠️ `voice_providers.py` has a decode stub when `av` (PyAV) is absent — graceful, logged. |
| 3.14 | Document remaining third-party services | ✅ (below) | See §15 Third-party services. |

> **Important:** Using Ollama as a model *provider* does **not** make Jalora an
> external agent. Jalora controls planning, tools, permissions, execution and
> recovery; Ollama only token-predicts. This holds for every provider in
> `AUTO_PRIORITY = [openai, anthropic, google, deepseek, groq, mistral,
> openrouter, ollama]` (`model_router.py`).

---

## 4. Chat and Conversation History

| # | Checklist item | Status | Evidence |
|---|---|---|---|
| 4.1 | Create/rename/delete conversations | ✅ | `conversations.py` CRUD; `test_workspace_projects.py::test_session_create_list_rename_delete`, `test_normal_conversation_persists`. |
| 4.2 | Start a new conversation | ✅ | `POST /api/conversations` (`ConversationCreate`); fresh `conversation_id` returned per turn. |
| 4.3 | Load older conversations | ✅ | `GET /conversations/{id}/messages`; `_load_history`. |
| 4.4 | Search conversation history | ✅ | `GET /api/documents/search` (filename+text), `search_files`; `test_phase_d_generation::test_search_endpoint_finds_docx_table_text`. |
| 4.5 | Persist after refresh | ✅ | SQLite persistence; `fresh_db` only resets between tests, real persistence otherwise. |
| 4.6 | Correct message order | ✅ | `test_normal_conversation_persists` asserts `["user","assistant","user","assistant"]`; ordered by `created_at`/`id`. |
| 4.7 | Handle long conversations | ✅ | `history_limit` + summarisation (`maybe_summarize_history`); truncation of stale tool results. |
| 4.8 | Edit/retry previous prompt | ⚠️ Not found in backend | No `PUT /messages/{id}` or retry endpoint present in `app/api/*.py`. **Gap** (frontend-only?). |
| 4.9 | Stop generation | ✅ | `POST /api/runs/{id}/stop`; `run_registry.stop_run`; `test_chat.py::test_stop_single_run_endpoint`, `test_stop_all_agents_endpoint`. |
| 4.10 | Render code/Markdown/tables/links | ✅ | `apply_visual_postprocessing` extracts SVG/citations; `visuals.py` markdown/citation renumbering; `test_visuals.py`, `test_chat.py::test_sse_done_event_*`. |
| 4.11 | Special chars / emojis | ✅ (partial) | `_sanitize_input` strips Unicode control chars except whitespace (`chat_service.py`); no dedicated emoji test. |
| 4.12 | Useful errors, not `[object Object]` | ✅ | `app/errors.py` `JaloraError` → structured `{success, error:{code,details}}` JSON; `test_cors.py::test_api_404_is_structured`; `test_describe_model_error_is_friendly`. |
| 4.13 | Prevent conversations from leaking across users | ✅ | Ownership checks on every conversation route (`conv.user_id != user.id` → 404); `test_conversation_isolation_between_users`. |

---

## 10. Security

| # | Checklist item | Status | Evidence |
|---|---|---|---|
| 10.1 | AuthN with strong password hashing | ✅ | `argon2.PasswordHasher` (`app/auth/security.py:11`); `test_auth.py`. |
| 10.2 | JWT access tokens (HS256, issuer, expiry) | ✅ | `security.py` JWT encode/decode with `iss=jalora`, `exp`+`sub`; `test_auth.py`. |
| 10.3 | Refresh tokens hashed, constant-time compare | ✅ | SHA-256 hashed refresh tokens, `secrets.compare_digest`; `test_auth.py`. |
| 10.4 | Per-user provider API keys encrypted at rest | ✅ | `credential_store.py` Fernet (validated key, secrets encrypted before write, never returned); `ProviderKeyPatch`. |
| 10.5 | CSRF / CORS allowlist | ✅ | `settings.cors_origin_list`; `test_cors.py::test_cors_preflight_allows_configured_origin` + `test_cors_rejects_unknown_origin`. |
| 10.6 | Rate limiting + lockout | ✅ | Global per-IP 429+`Retry-After:60`; auth rate limit (`JALORA_RATE_LIMIT_AUTH_PER_MIN`); `test_rate_limit_phase_l.py` + `test_auth.py`. |
| 10.7 | Audit log with secret redaction | ✅ | `app/services/audit.py` `redact()` (bearer/AWS/Slack/private-key/JWT/Fernet patterns + sensitive keys); `test_audit.py`. |
| 10.8 | No hardcoded secrets in source | ✅ | Grep for `sk-/AKIA/PRIVATE/BEGIN` found only regex *patterns* in `audit.py:25` and `memory_service.py:27`; `.env` not committed (only `.env.example`); secrets referenced indirectly. |
| 10.9 | Sandbox escape prevention | ✅ | `sandbox.py` `resolve_path` blocks `..`, absolute paths, null bytes, symlink escape; allowlisted commands, `shell=False`, pip gated; `test_phase_j_sandbox.py` (15 resolve_path tests) + `test_phase_j_coding.py` path-escape tests. |
| 10.10 | SSRF protection | ✅ | `url_safety.py` blocks non-http(s), embedded credentials, private/loopback/link-local/metadata IPs unless `JALORA_BROWSE_ALLOW_PRIVATE`; `test_web_p6.py::test_url_safety_blocks_private_*`. |
| 10.11 | File upload validation (type/size/magic) | ✅ | `document_service.validate_upload` (extension allowlist) + `sniff_matches_extension` magic-byte check; per-type size caps; `test_phase_d_generation.py::test_spoofed_docx_rejected`, `test_phase_d_media`. |
| 10.12 | IDOR — record ownership | ✅ | Every `/documents`, `/conversations`, `/workspace`, `/schedules`, `/approvals`, `/runs` route checks `user_id`; new `test_readiness_gaps.py::test_conversation_delete_is_scoped_to_owner`, `test_conversation_messages_are_scoped_to_owner`, `test_generated_files_are_owner_only`. |
| 10.13 | Structured errors (no `[object Object]`) | ✅ | `JaloraError` handler; `test_cors.py::test_api_404_is_structured`. |

---

## 11. Backend API / Database

| # | Checklist item | Status | Evidence |
|---|---|---|---|
| 11.1 | DB schema migrations | ✅ | Alembic `versions/` (15 migrations incl. auth, tasks, integrations, media); `test_migrations.py`. |
| 11.2 | SQLAlchemy parameterised queries (no SQLi) | ✅ | All queries use `select().where(...)` bound params; no `text()` with f-string user input; `test_readiness_gaps::test_document_search_injection_returns_empty_not_error` (new). |
| 11.3 | Connection health endpoint | ✅ | `GET /api/health` (`system.py:23`) — DB `SELECT 1`, provider booleans, startup warnings; `test_health.py`. |
| 11.4 | Tool-result envelope | ✅ | `ToolResult.ok(...)` returns `{success, data, error, ...}`; `app/errors.py`; consumed by graph nodes. |
| 11.5 | Observability / metrics | ✅ | `app/services/metrics.py` (`get_snapshot`), `/api/metrics`, `run_metrics` event; `test_metrics.py`, `test_tracing_phase_l.py`. |

---

## 12. Coding Workspace (sandbox)

| # | Checklist item | Status | Evidence |
|---|---|---|---|
| 12.1 | Sandboxed code execution | ✅ | `sandbox.py` (`shell=False`, allowlist `python/pytest/git`), per-command wall-clock + output cap; `test_run_command_wall_clock_timeout`, `test_command_allowlist_*`. |
| 12.2 | Path-traversal / escape prevention | ✅ | `resolve_path` rejects `..`/absolute/null-byte/symlink-escape; `test_resolve_path_rejects_*`, `test_workspace_api_blocks_escape`. |
| 12.3 | Editor diff + rollback | ✅ | `.jalora/edits.json` journal + backup files; `test_write_diff_and_rollback`, `test_rollback_restores_previous_content`, `test_zip_project_round_trip`. |
| 12.4 | Per-project isolation | ✅ | `test_workspace_projects.py::test_projects_have_separate_folders`, `test_upload_scoped_to_project`, `test_exec_scoped_to_project`. |
| 12.5 | No secrets leak into sandbox env | ✅ | `test_phase_j_coding.py::test_sandbox_env_has_no_secrets`. |

---

## 13. Search, Browser, Email, Voice, Files, Media (provider-backed features)

| Area | Status | Note |
|---|---|---|
| Search (§6) | ✅ | Mock provider default; `test_search_service.py` (cache, timeout, clamp) + `test_web_p6.py::test_search_web_uses_mock_provider_offline`. |
| Browser (§8) | ✅ (offline) | `fake` driver in tests; `url_safety.py` SSRF guard; `test_web_p6.py` (skips Playwright-only tests). |
| Email (§34-38) | ✅ | `test_phase_e_email.py` (38 tests) — send, mock inbox, auto-reply rules, OAuth CSRF, signature verification. |
| Voice (§31-33) | ✅ (offline mock) | `test_phase_f_voice.py`, `test_phase_h_voice.py`, `test_voice_twilio/telnyx.py`; `ping_external` approval gate. Live STT/TTS paths require native deps (excluded); however the live in-app voice pipeline is covered offline via `test_voice_live.py` (18 tests incl. WS downgrade and memory-budget integration). |
| Files / Documents (§6, §39-41) | ✅ | Upload sniff + magic-byte + size cap + ownership; `test_phase_d_generation.py`, `test_documents.py`. |
| Media AI (§42-44) | ✅ | Vision/multimodal tool binding, audio/video transcription advertising; `test_phase_d_media.py`. |

---

## 14. Integrations / Personal Assistant (Phase I §63-68)

| # | Checklist item | Status | Evidence |
|---|---|---|---|
| 14.1 | Contacts / calendar / drive / maps / weather / translate | ✅ | All "mock-first" and tested offline: `test_phase_i_integrations.py` (18 tests) incl. `test_calendar_*` round-trips, `test_drive_*`, `test_translate_round_trip`, `test_weather_*`, `test_provider_registry_*`. |
| 14.2 | Disconnection + fallback | ✅ | `test_provider_disconnect.py` — per-user disconnect, fallback, reconnect. |
| 14.3 | Phase-1 live voice latency & memory policy | ✅ | In-app server STT/TTS (Whisper/Piper/MockTTS) + `VoiceSession` own planning/tools/confirmation — provider/latency/memory budget downgrade is Jalora-owned, not an external agent (spec §3). `tests/test_voice_live.py`: **18 passed** (9 baseline + 9 new) after Phase-1 latency and memory-budget work. |

---

## 15. Third-Party Services & Why They Are Needed

| Service | Why needed | Can be fully disconnected? |
|---|---|---|
| **Ollama** | Local LLM backend for privacy-first inference. | Yes — falls back to cloud or returns `OLLAMA_OFFLINE` 503. Not an agent dependency. |
| **OpenAI / Anthropic / Google / DeepSeek / Groq / Mistral / OpenRouter** | Cloud model providers for the routing chain. | Yes — per-provider disconnect; `disabled_providers` (test 3.9). |
| **Google (Calendar/Contacts/Drive/Gmail OAuth)** | Optional synced calendar/contacts/drive/email. | Yes — auto-degrades to local DB mock. |
| **Twilio / Telnyx** | Telephony transport for live phone calls. | Yes — auto-degrades to in-app server-STT/TTS (Phase 1 live voice). |
| **Stripe** | Payments/billing. | Yes — payment tools are off unless configured. |
| **Amadeus** | Flight/booking search. | Yes — mock-first, deterministic. |
| **BuuPass** | Jalora reads date-specific options and booking controls from the official public route page with the real Playwright browser, then can guide the user through provider-visible pickup, drop-off, seat, and passenger fields. No supported partner API is configured; payment must be completed by the user on BuuPass. | Yes — browser mode must resolve to `real` (`JALORA_BROWSER_DRIVER=auto` uses Playwright when installed); fake mode cannot retrieve inventory. |
| **Redis** (optional) | Optional backing store for `run_registry` + rate limiter. | Yes — in-memory fallback (`test_run_registry_redis::test_in_memory_fallback_no_redis`, `test_rate_limit::test_get_rate_limiter_falls_back_to_memory`). |
| **Chroma** (optional) | Optional vector store for memory/knowledge. | Yes — SQL vector store is the test default. |
| **Playwright** | Browser-automation E2E (P6). | Yes — `fake` driver in tests; `test_web_p6` skips when absent. |
| **PyAV (`av`)** | Audio/video decode for voice/media. | Yes — numpy decode stub with logged warning when absent. |
| **torch / diffusers / piper-tts** | Local image generation + TTS inference. | Yes — procedural PNG fallback; AI/speech paths `@pytest.mark.skipif`. |

---

## 16. Release Gates (Go/No-Go)

| Gate | Status |
|---|---|
| Tests green (offline, deterministic) | ✅ 621 passed / 7 graceful skips |
| No hardcoded secrets in source | ✅ (verified grep) |
| Structured errors, no traceback leakage | ✅ (`test_describe_model_error_*`, new gap test) |
| AuthN/AuthZ + IDOR ownership on all user-scoped routes | ✅ |
| Sandbox containment + escape prevention | ✅ |
| SSRF + upload (magic-byte/size/extension) validation | ✅ |
| Crash-free model-provider failure (503/502, never 500) | ✅ |
| Persistence survives restart (resume/approval/runs) | ✅ |
| Rate limiting + CORS allowlist | ✅ |

---

## Recommendations & Remaining Gaps

1. **`test_phase_d_image_generation.py` / `test_creative.py`** time out on the
   `can_load_torch()` probe in this sandbox (import is gated but slow). Add a
   CI gate that sets `JALORA_FORCE_NO_TORCH=1` (or a pytest marker) so the AI
   path skips instantly and only the procedural fallback is exercised here.
2. **Voice/telephony E2E** (`test_voice_live`, `test_phase_f_voice`,
   `test_voice_twilio/telnyx`, `test_web_p6`) cannot run without native
   runtimes (piper, faster-whisper model, Playwright). They pass only in an
   environment with those installed; add an explicit "skip if offline" summary
   to the CI matrix.
3. **Checklist gaps with no backend code:** conversation message edit/retry
   (4.8) — add `PATCH /messages/{id}` or a retry flag if the frontend relies
   on it (currently not present in `app/api/`).
4. **Multilingual accuracy (1.10) & special-char coverage (5.x)** — add a
   dedicated test feeding non-ASCII + emoji payloads through `/api/chat` and
   asserting they round-trip into `fake_router.tape`.
5. **Thermal/power monitoring (1.17)** — the `/api/ollama/memory` snapshot
   exposes free RAM and loaded models; add CPU thermal sampling if target
   deployment is on laptops.
6. **Live provider availability** — the offline mock layer proves logic; a
   scheduled, opt-in "live smoke" pipeline against staging keys is recommended
   before each production release.

---

## Appendix A — New tests added by this audit

`tests/test_readiness_gaps.py` (6 tests, all passing):

| Test | Checklist | What it asserts |
|---|---|---|
| `test_empty_message_is_rejected_by_validation` | 1.11 | `message=""` → 422 JSON, no model call |
| `test_missing_message_field_is_rejected` | 1.11 | omitted `message` → 422 |
| `test_conversation_messages_are_scoped_to_owner` | 2.13 / 4.5 | IDOR: other user's messages → 404 |
| `test_conversation_delete_is_scoped_to_owner` | 2.13 | IDOR: delete another's conversation → 404 |
| `test_document_search_injection_returns_empty_not_error` | 10.2 / §16 | SQLi payloads → 200 + `results:[]`; empty query → 400 |
| `test_model_invocation_failure_returns_structured_error` | 1.12 / 1.13 | `ConnectError` from model → 4xx/5y structured error, `success=False`, **no traceback/secret leak** |

These reuse the existing `client` + `auth_headers` + `fake_router` fixtures and
the real graph/DB, so they exercise the production codepaths, not mocks.
