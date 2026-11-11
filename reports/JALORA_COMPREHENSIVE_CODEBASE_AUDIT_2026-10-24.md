# Jalora AI comprehensive codebase audit — 2026-10-24

## Gate 0 baseline

This is the pre-implementation audit for the milestone work order. The worktree
was already dirty before this audit (`jalora-optimization`); no existing changes
were reset or overwritten.

### Active application and startup

| Area | Verified implementation |
|---|---|
| Backend | FastAPI app factory in `backend/app/main.py`; the active development command uses the project `.venv` and Uvicorn on port 8000. |
| Frontend | `frontend-react/` is the active Vite/React application. Vite proxies `/api` to `http://127.0.0.1:8000`; the older `frontend/` tree is not the active dev UI. |
| Database | SQLAlchemy models/session with SQLite in the current local environment; Alembic migrations exist. The backend health endpoint reports the database as healthy. |
| Model providers | Ollama (`11434`) and LM Studio OpenAI-compatible API (`1234`) are configured as local providers; the model router also contains cloud adapters and fallback logic. |
| Agent pipeline | `/api/chat` → `ChatService` → LangGraph agent graph → model router/tool registry → result validation/persistence/SSE. `Task`, `TaskStep`, `AgentRun`, and `Approval` models/services provide execution state. |
| Memory | Conversation rows are separate from `Memory` rows. Memory has SQL source-of-truth storage, optional vector indexing, explicit API controls, sensitive-content filtering, and user ownership filters. |
| Integrations | Gmail/Google OAuth, Calendar, documents/artifacts, email approval, schedules, voice, calls, and travel adapters are registered. Live external verification remains credential/authorization dependent. |

### Local service verification

Verified on 2026-10-24 without exposing secret values or performing consequential
external actions:

- `http://127.0.0.1:8000/api/health` → HTTP 200; database healthy.
- `http://127.0.0.1:5173/` → HTTP 200 after starting the missing Vite dev server.
- `http://127.0.0.1:11434/api/tags` → HTTP 200; Ollama is reachable.
- `http://127.0.0.1:1234/v1/models` → HTTP 200; LM Studio is reachable.
- The backend health response is currently `env=test`, so the normal local startup
  configuration is test-like and must be reviewed before production deployment.

The frontend was started with `npm run dev -- --host 0.0.0.0 --port 5173` from
`frontend-react/`. The backend and both local model services were already running;
they were not restarted.

### Capability status at Gate 0

Status is based on code inspection and the baseline evidence available so far;
`COMPLETE` is reserved for capabilities that are also verified through the real
application. This table will be updated after each gate.

| Capability | Status | Evidence / current limitation |
|---|---|---|
| Authentication and object ownership | PARTIAL | JWT/Argon2 and user filters exist; full browser isolation and refresh/sign-out acceptance still need execution. |
| Persistent conversations | PARTIAL | Conversation/message persistence, loading, new chat, delete, and continue exist. Rename, archive, search, long-history pagination, and complete UI acceptance are not yet demonstrated. |
| Message timestamps/timezone | COMPLETE (local scope) | UTC storage, validated IANA timezone preferences, `Africa/Nairobi` default, rendered message timestamps/date separators, and focused/browser verification are complete; external calendar live verification remains separate. |
| User-controlled long-term memory | PARTIAL | Explicit memory API, retrieval, deletion, provenance fields, filtering, and user scoping exist. Cross-conversation acceptance, deletion policy for source conversations, and failure recovery need verification. |
| Shared agentic execution | PARTIAL | LangGraph, typed registry, task state, approvals, provider fallback, and recovery code exist. Full dependency/resume/idempotency/review behavior needs integrated acceptance evidence. |
| Tool discovery and routing | PARTIAL | Registry manifests and intent-based binding exist. The full registry needs a schema/permission/timeout/output-contract audit and live routing checks. |
| Instruction following/output validation | PARTIAL | Structured schemas and content validation exist; broad constraint coverage is not yet demonstrated in the live UI. |
| Mathematics and deterministic reasoning | PARTIAL | A math engine and regression tests exist; real chat rendering and fallback behavior need current localhost verification. |
| Research/current information | PARTIAL | Search tools and source validation exist; source quality, freshness, citations, and multi-step report workflows need live/mock evidence separation. |
| Email/Gmail | PARTIAL | Draft validation, editable approval, attachments, TLS CA handling, and provider-confirmed status safeguards exist. Live OAuth/send remains unverified and must remain approval-gated. |
| Google Calendar | PARTIAL | Calendar API/UI, mock provider, OAuth boundaries, persisted event timezones, conflict/reminder handling, and browser/API tests pass. Live Google authorization and external reconciliation are not verified. |
| Voice chat | PARTIAL | Transcription and live WebSocket/session paths exist with local/mock provider chains. Browser microphone, interruption, cancellation, and text/voice continuity are not yet verified here. |
| Telephony | NOT VERIFIABLE | Provider adapters and safety gates/tests exist; no authorized live call is permitted for this audit. |
| Travel/booking | NOT VERIFIABLE | Interfaces/mock/sandbox-oriented adapters exist; no live inventory or purchase is authorized. |
| Files/documents/artifacts | PARTIAL | Generation, readability and placeholder validation exist; upload, long-file parsing, artifact isolation, and all frontend download paths need acceptance checks. |
| Frontend streaming/rendering | PARTIAL | React chat, SSE, plans, approvals, Markdown/MathJax and error states exist; timestamp rendering, reconnect/cancel, and all incomplete-plan states need verification. |
| Security/operations | PARTIAL | CORS, auth, rate limiting, redacted audit, TLS verification, and provider status checks exist. The current test environment and stale documentation require cleanup/documentation; a full security audit remains. |

## Baseline test status

The complete backend baseline was started with:

```powershell
cd jalora\backend
& ..\.venv\Scripts\python.exe -m pytest tests -q
```

The completed backend baseline is **1,239 passed, 1 failed, 4 skipped, and 15
warnings** in 8m47s. The single failure was a real routing regression: a
scheduled reminder containing a communication verb was classified as an
immediate task. The classifier precedence was corrected and the focused intent
regression now passes **20/20**.

Additional verification in this session:

- Gate 1 execution/recovery suites: **59 passed**.
- Conversation, ownership, migration, readiness, and timezone suites: **22
  passed** in the focused chat run.
- Frontend production build: passed; Vite emitted one non-blocking dynamic-import
  chunking warning.
- Repository Playwright against the running localhost: **22 passed** after
  starting the active frontend and running the backend with a local deterministic
  embedding fallback.

## Initial root-cause candidates requiring implementation

1. The active settings defaulted to `UTC`; this is now `Africa/Nairobi`, while
   stored timestamps remain UTC and user-selected IANA zones are validated.
2. Conversation APIs lacked management operations; rename, archive, search, and
   ownership-checked PATCH behavior are now present and browser-tested.
3. `ChatView` dropped persisted `created_at`; it now retains and renders message
   timestamps and date separators in the configured timezone.
4. The original full-suite routing failure came from communication intent being
   checked before scheduling intent. Scheduling now takes precedence only when a
   time expression is present; immediate calls remain task actions.
5. The configured root `.env` selects Ollama embeddings, but the running Ollama
   instance does not have `nomic-embed-text`, causing document uploads to fail.
   Local acceptance was rerun with the explicit `JALORA_EMBEDDING_PROVIDER=local`
   startup override. Operators must either install the configured embedding model
   or choose the documented local/automatic provider before relying on uploads.
6. Many capabilities have code and tests, but live-provider evidence remains
   unavailable without credentials and explicit authorization; the implementation
   preserves that distinction.

## Next gate

Gate 1 through Gate 4 are complete for their focused deterministic/API acceptance
scope. Calendar and voice are next for deeper browser and provider-boundary
verification; live Google authorization remains credential- and approval-dependent.

## Gate 0-3 completion evidence

The active local services were verified at `127.0.0.1:8000` (backend),
`127.0.0.1:5173` (frontend), `127.0.0.1:11434` (Ollama), and
`127.0.0.1:1234` (LM Studio). The frontend E2E suite exercised the real browser
application. No real email, call, booking, payment, or calendar mutation was
authorized. External providers remain mocked or read-only unless explicitly
noted.

## Gate 4 memory verification

The existing memory service was audited rather than replaced. It stores approved
memory separately from conversation history, applies user ownership filters,
rejects sensitive values, supports explicit capture and search, records a source
field, reconciles contradictory preferences, and removes vector-index entries on
deletion. The focused memory/preferences suite passed **48/48**. The policy for
conversation deletion is that deliberately user-approved long-term memories are
independent records and are retained until the user deletes them; transient
conversation text is not automatically promoted to long-term memory. This keeps
deleting a chat from silently deleting an explicitly approved preference. Live
model memory extraction remains provider-dependent, while API and deterministic
service behavior are covered.

Calendar OAuth/mock boundaries, voice, telephony adapters, and travel/payment
safety tests were also exercised: **81 passed, 1 warning**. These are mocked or
contract-level checks; no live calendar mutation, phone call, booking, payment,
or email was performed. The live integration portions remain partial or not
verifiable rather than being marked complete.

## Calendar milestone progress

Calendar OAuth state binding, encrypted token storage, invalid-state rejection,
mock event creation/reminders/deletion, Google-event merging, and timezone
validation and ownership-checked PATCH update passed **10/10** focused tests after adding the event `timezone` field
and migration `t1a_calendar_event_timezone`. The calendar UI now reads the
configured user timezone and displays/group events using the event or user IANA
zone. Google credentials were not available, so live OAuth and remote mutation
remain unverified; the UI continues to disclose mock mode and does not claim a
Google event was created when it was not.

After restarting the active backend, a safe localhost-only authenticated API
check created one mock event with `Africa/Nairobi` and read it back successfully.
The calendar browser suite passed **2/2**. The test used a synthetic local account
and did not contact Google.

## Voice milestone progress

The live in-app voice session now accepts an authenticated conversation ID and
persists user/assistant voice turns into that owned conversation in addition to
the call transcript. The offline live-voice suite passed **19/19** after this
repair, and the frontend build passed. The existing Chat microphone path already
uses the active chat conversation and retains duplicate-send protection through
the shared busy guard. Browser microphone permission, real STT/TTS latency,
interruption, and continuous voice remain unverified in this environment.

## Security and full-regression evidence

Authentication, ownership, approval, rate-limit, telephony webhook, travel,
hotel, and payment-boundary tests passed **92/92** with one dependency warning.
The complete backend suite subsequently passed **1,246/1,246**, with **4 skipped**
and **15 warnings**. The frontend production build passed, and the complete
browser suite passed **22/22** on the rerun. No real email, phone call, booking,
payment, or Google Calendar mutation was performed. Live integrations remain
credential- and authorization-dependent.

The first full browser run had one Workspace timing failure while the local model
was under resource contention; the same test passed in isolation and the complete
suite passed on rerun. This is retained as an operational limitation rather than
hidden as a skipped test.

Telephony and travel are complete only for their provider-independent readiness
scope: normalized contracts, permission levels, ownership checks, provider status
handling, and deterministic/mock failure paths are covered by the passing tests.
They are not live capabilities in this environment; no call, reservation,
payment, or external inventory request was authorized.

