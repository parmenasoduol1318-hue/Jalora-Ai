# Jalora AI full application audit

Date: 2026-10-23  
Scope: local development environment only; no production deployment, commit, or push performed.

## Architecture inventory

- Frontend: React/Vite application in `frontend-react/`; the browser entry point is `src/main.tsx`, with views under `src/views/`, API/SSE clients under `src/lib/`, and Playwright tests under `e2e/`. A legacy vanilla frontend also exists in `frontend/` and is not the active Vite UI.
- Backend: FastAPI application in `backend/app/main.py`, with routes in `backend/app/api/`, service orchestration in `backend/app/services/`, deterministic intent classification in `backend/app/agents/intent.py`, LangGraph orchestration in `backend/app/agents/graph.py`, and tool registration in `backend/app/tools/`.
- Persistence: SQLAlchemy models derive from `backend/app/database/base.py`; SQLite `dev.db` is used by the local launcher, and Alembic migrations live in `backend/alembic/versions/`.
- Authentication: JWT access/refresh-token flows in the auth API and dependency layer; route ownership checks were exercised through API and browser tests.
- Model providers: Ollama is live at `127.0.0.1:11434`, and LM Studio is reachable at `127.0.0.1:1234/v1` with loaded models. The local launcher pins development chat to Ollama and the installed lightweight model for resource stability.
- Integrations: Gmail/OAuth, calendar, drive, search, communications, browser, creative/media, documents, jobs/CV, payments/travel, and voice providers are represented by routes/services/tools. Provider credentials remain environment-backed and were not printed.
- Deployment: Dockerfile, docker-compose, Makefile, GitHub Actions, Alembic, and local launcher configuration were inspected. No deployment was performed.

## Capability and test inventory

| Capability | Implementation | Verification |
|---|---|---|
| Chat and streaming | `backend/app/api/chat.py`, `backend/app/services/chat_service.py`, `frontend-react/src/views/ChatView.tsx` | PASS: browser chat suite and backend chat/routing tests |
| Intent/routing | `backend/app/agents/intent.py`, `backend/app/tools/routing.py` | PASS: routing, intent, email, math, research, and action tests |
| Agent planning/tool execution | `backend/app/agents/graph.py`, `backend/app/tools/` | PASS: agent, approval, tool, reliability batches |
| Math | `backend/app/services/math_engine.py` | PASS: math suite |
| Documents/files | `backend/app/api/documents.py`, `backend/app/services/documents.py` | PASS: document tests; workspace API repaired and verified |
| Memory/personalization | `backend/app/services/memory.py`, memory APIs | PASS: memory batches |
| Search/research | `backend/app/services/search_service.py`, research tools | PASS in configured mock/public test paths; live provider scope remains environment-dependent |
| Email/calendar/drive | integration APIs/services/tools | PASS for configured/mock contracts; Gmail delivery not performed without an authorized mailbox action |
| Workspace coding | `backend/app/api/workspace.py`, `backend/app/services/sandbox.py`, `frontend-react/src/views/WorkspaceView.tsx` | PASS after schema repair; browser workspace test passed |
| Scheduling/tasks | task/schedule APIs and frontend views | PASS: browser and backend tests |
| Voice processing | voice services/providers and tests | PASS: 57 voice-related tests, including mocked provider flows |
| Twilio live telephony | Twilio provider/call routes | BLOCKED — TWILIO CREDENTIALS REQUIRED; no paid call was attempted |
| Browser automation | browser tools/session service | PASS for fake/isolated browser contracts; live external browsing remains provider/configuration dependent |
| Creative/media | creative APIs/models/services | PASS: relevant automated tests |
| Jobs/CV/application workflow | job tools, models, routes, CV services | NOT TESTED end-to-end: no authorized candidate profile, job-search credential scope, or permission to email real documents was supplied |

## Executed test evidence

- Focused regression suite: `79 passed`.
- Capability batch for business/content/fact/feedback/forms/hotel/jobs/meeting/outcomes/preferences/support/workflows/reliability: `206 passed` after repairs.
- Documents, email extraction, math, memory, model router, Ollama memory, search, tasks, tools, payments/travel: `313 passed` after repairs.
- Agent/chat/readiness/provider-disconnect batch: `120 passed`.
- Voice/Twilio/Telnyx/voice-live batch: `57 passed`, one deprecation warning; live Twilio telephony remains blocked as stated above.
- Frontend build: PASS (`npm run build`); only a Vite chunking warning remains.
- Playwright browser suite: `19 passed` with one worker and an extended timeout appropriate for local model inference.
- Broad backend suite excluding the long-running Ollama evaluation file: final post-repair result `1195 passed, 3 skipped, 15 warnings` in 264.97 seconds.

## Defects repaired

1. Missing Alembic coverage for registered capability tables caused runtime `no such table` errors in preferences, support, and workflows. Added `backend/alembic/versions/r1a_add_registered_capability_tables.py` and made test setup create the registered schema before direct service tests.
2. Explicit calls mentioning an application were classified as research, so the action-capability guard blocked approval-gated calling and chained WhatsApp. Added a narrow communications-action precedence rule and regression coverage.
3. Email extraction contained a stale assertion expecting `task` even though the dedicated email route and surrounding tests require `email`. Corrected the inconsistent test expectation.
4. Frontend Playwright auth/approval fixtures waited for obsolete message-input selectors. Updated them to the current composer selector; isolated and full serial browser tests pass.
5. Workspace API schemas no longer matched the sandbox/frontend contract. Restored `files`, `size`, `created`, `ok`, `timed_out`, and list-valued rollback responses.
6. Local development launcher attempted cloud/provider auto-routing and selected an oversized Ollama tier under constrained RAM. It now explicitly uses Ollama and the installed lightweight model for local development. This does not claim LM Studio is running.

## Security and production risks

- Provider status/configuration is partly static: a keyless local provider can appear configured without proving liveness. A live health probe should be surfaced consistently in production UI/status responses, even though both local endpoints were reachable during this audit.
- The local SQLite/dev launcher is suitable for development only. Production requires a managed database, secret management, backups, migration runbook, and production-safe rate limits.
- Cloud provider variables are present in local configuration; values were not exposed. Rotate any credentials that may have been shared outside the environment.
- Local model inference is resource-sensitive. The previous 3.2B selection could remain in “Thinking” for a long time under memory pressure; production should enforce model latency/resource budgets and surface actionable provider errors.
- The full job-search/CV/email scenario requires an authorized profile, live search scope, document review, and explicit mailbox authorization; it was not fabricated or sent.

## Remaining statuses

- PASS: all locally reproducible automated and browser defects repaired so far.
- BLOCKED — TWILIO CREDENTIALS REQUIRED: live Twilio calling/telephony only.
- PASS: Ollama and LM Studio endpoint connectivity were verified locally; Ollama is the active development route and LM Studio remains available as a configured local option.
- NOT TESTED: real Gmail delivery, real job-board coverage, tailored CV delivery to the requested mailbox, and any paid/external action lacking authorization.
- MISSING/INCOMPLETE: a truthful live-provider status contract for LM Studio and a safe fully authorized job-application workflow acceptance run.

## Final broad-suite result

`1195 passed, 3 skipped, 15 warnings` in 264.97 seconds, excluding `tests/test_ollama_eval.py`, which is a separate model-evaluation workload rather than the deterministic regression suite. The skipped tests remain explicitly non-passing and are external/provider-dependent cases.

## Current acceptance verification

- Backend and frontend were restarted from the existing local launch configuration and both returned HTTP 200 health/page responses.
- The shared localhost Jalora browser displayed a completed `hello` response from `ollama:qwen2.5:0.5b`; the response was rendered as ordinary assistant text with `Intent: chat`.
- The live frontend chat smoke suite against the restarted services passed: `2 passed`.
- The full serial Playwright acceptance suite remains `19 passed`.
- The workspace/coding/sandbox regression cluster passed again after the schema repair: `86 passed, 2 skipped`.
- The deterministic backend regression suite therefore has a measured executed-test pass rate of `1195 / 1195 = 100%`; `3` skipped provider/external cases are excluded from that numerator and are not counted as passing.
- A transient browser `500` was reproduced while the backend process was not running. Restarting the existing launcher restored the API; this was a service-lifecycle state, not an Ollama model absence. Ollama was reachable and the configured lightweight model was available.

## Acceptance benchmark limitations

The repository contains a historical 67-case model benchmark at `reports/baseline_benchmarks.md`, but it is not equivalent to the current full acceptance suite and still records a historical 94% result. The current 100% figure above is for executed deterministic regression tests, not a claim that every live external integration is available. Live Twilio telephony remains credential-blocked, and real Gmail/job-search/CV delivery was not authorized or performed.

## Capability-level acceptance benchmark (2026-10-23)

This section is separate from the broad deterministic regression count above. It records capability cases exercised through the shared browser, provider-backed local runtime, and repeatable focused tests. No real email, telephony, job application, or paid action was performed.

| Group | Case(s) | Evidence | Result |
|---|---|---|---|
| Research and citations | Kenya president; public-profile search for Parmenas Oduol | Shared browser + local search/provider path; rendered source anchors inspected | PASS 2/2. Kenya returned William Ruto with clickable official/search links. The profile flow returned public result links and explicitly marked them as search summaries rather than verified identity facts; namesake risk remains. |
| Mathematical correctness and rendering | `(x+2)^3` binomial expansion; KES 5,000,000, 14%, 15-year loan JSON | Shared browser; deterministic math/finance engines; independent amortization formula | PASS 2/2. Binomial result rendered as MathML with `x^3 + 6x^2 + 12x + 8`. Finance returned valid JSON with monthly payment `66587.07` and total interest `6985672.49`. |
| Tool routing | Person research must not expose flight routing; research/email tool subsets remain distinct | Repeatable `backend/tests/test_capability_acceptance.py` checks | PASS 1/1. `search_profiles` is available for person research and `search_flights` is absent from the research subset. |
| Multi-step execution | Deliberately failing `open_url` prerequisite | TestClient with scripted model/tool failure and persisted task plan | PASS 1/1. Failed prerequisite is persisted as `failed`, dependent summary remains `pending`, and no downstream success is claimed. This is mock/model-script evidence, not a live external workflow. |
| Context retention | History compaction with recent-turn preservation | Repeatable focused test | PASS 1/1. Older context is summarized while the two newest turns remain intact. |
| Error recovery | Unavailable `example.invalid` research source | Shared browser + SSE response | PASS 1/1 for honest recovery. Jalora returned no fabricated evidence and asked for accessible content; it did not provide a precise provider error classification, which remains a UX improvement. |
| Existing integration contracts | Research and email capability routing | Repeatable focused test; no external side effect | PASS 1/1 contract check. Live Gmail, calendar/Drive authorization, job-board coverage/CV email delivery, Twilio, and Telnyx were not run. |

### Benchmark totals and boundaries

- Defined focused capability evidence executed: **9/9 PASS** (2 research, 2 math, 1 routing, 1 multi-step, 1 context, 1 recovery, 1 integration-contract case).
- Live/shared-browser cases: **5/5 PASS** for the five cases exercised above (research x2, math x2, unavailable-source recovery x1). The multi-step and context cases are deterministic/mock-backed and are not counted as live-provider proof.
- Repeatable focused tests: **6/6 PASS** in `backend/tests/test_capability_acceptance.py`; frontend chat regression: **3/3 PASS**; frontend production build: **PASS**.
- Live integrations not counted as passing: Gmail delivery, calendar/Drive authorization, job-board coverage/CV email delivery, Twilio telephony, and Telnyx messaging. These are `NOT TESTED` or `BLOCKED` according to the dependency below, not hidden in the pass rate.
- Twilio-only live status: **BLOCKED — TWILIO CREDENTIALS REQUIRED**. No other feature is classified as Twilio-blocked.

### Root causes and repairs from this phase

1. Finance parsing matched the `15` in “15 years” as the principal because the old expression allowed arbitrary text between `loan` and the first number. `backend/app/services/finance_engine.py` now prefers currency-qualified amounts and constrained loan-amount patterns; the focused finance test and live JSON case pass.
2. Natural-language binomial instructions left “using the Binomial Theorem” in the SymPy expression, creating unrelated symbolic factors. `backend/app/services/math_engine.py` now strips instructional tails before symbolic parsing; the deterministic binomial regression passes.
3. A completed finance SSE payload with an empty plan shape exposed unsafe frontend assumptions about `plan.steps` and array fields, causing a React render crash. `frontend-react/src/views/ChatView.tsx` now guards plan, tool, artifact, source, citation, and diagram collections; the shared browser case and frontend regression suite pass.
4. The authoritative `done.reply` could be overwritten by streaming cleanup in the chat view. `frontend-react/src/views/ChatView.tsx` now preserves the terminal reply, covered by the Playwright done-event regression.

Remaining capability risks are source-quality/identity disambiguation for namesake searches, precise error classification for unreachable research URLs, and unverified live external integrations. The next safe priority is provider-backed integration acceptance with explicitly authorized sandbox accounts, followed by broader held-out browser benchmark cases.

## Career workflow failure investigation (2026-10-23)

### Root cause

The supplied English-language career request was deterministically classified as `research`, but the `research` and `web_search` routing subsets advertised `translate`. The model therefore had an unrelated translation tool available and selected it. The translation provider then surfaced an SSL/provider exception, and the model stopped before job search, CV, report, or email stages. This was a tool-selection defect combined with insufficient workflow guidance; it was not evidence that TLS verification should be disabled.

### Repairs

- [routing.py](../backend/app/tools/routing.py): removed `translate` from research and quick-web-search tool subsets; added the approval-gated email tool to research workflows so authorized report delivery is representable.
- [graph.py](../backend/app/agents/graph.py): added a runtime intent guard that rejects translation for research intents before invocation, while allowing it for explicit chat/task translation requests.
- [prompts.py](../backend/app/agents/prompts.py): added a reusable career-research brief covering vacancy verification, truthful CV generation, artifact validation, independent-step recovery, and approval-gated email delivery.
- [chat_service.py](../backend/app/services/chat_service.py): injects the career brief for job/career research signals.
- [search_service.py](../backend/app/services/search_service.py): automatic search now tries a configured real-provider fallback after bounded timeout/provider failure; explicit provider selection remains strict.
- [job_tools.py](../backend/app/tools/job_tools.py): generic search-result pages are excluded from returned vacancies, candidate listings are marked `verified: false` until opened and parsed, and remote status is no longer inferred merely from a URL string.

### Regression and live evidence

- Focused routing, fallback, capability, and search tests: **36/36 PASS**.
- Job, agent, and chat tests: **65/65 PASS**.
- The supplied career request was exercised in the shared browser. Observed live path: `research` intent → `search_jobs` → the UI reported the search tool completed with 10 results. No `translate` call occurred. The run used the pre-search-validation build, so those results are not evidence of verified vacancies. The local model then exceeded a safe observation window while selecting the next step, so the run was stopped in the UI. This is **PARTIALLY COMPLETED**, not a full workflow pass.
- The shared UI remained usable and showed the real completed search tool activity plus a stopped-state indicator. No email was sent and no CV artifact was claimed.

### Remaining blockers

- The local runtime auto-selected `qwen2.5:1.5b` while reporting approximately 1.1 GB free RAM, making multi-step career orchestration too slow for a reliable live acceptance run. This is an environment/model-capacity blocker, not a reason to fabricate completion.
- Search results still require live opening and verification before CV generation. Gmail is not configured in the shared account, so attachment delivery remains `NOT TESTED`.
- The current job handler generates a top-result CV/application sequence but does not yet provide a complete verified multi-vacancy report-and-attachment workflow. That remains implementation work, separate from the repaired translation misrouting.
- TLS verification remains enabled; no `verify=False` or equivalent bypass was introduced. The original SSL failure was avoided for this workflow by preventing unrelated translation invocation, while provider certificate repair remains a separate provider-configuration task if translation is explicitly requested.
## Agentic engine hardening pass (2026-10-23)

### Root causes found

1. Malformed tool JSON was converted into a successful `ToolResult`, allowing failed invocations to look completed downstream.
2. Plan normalization discarded dependency/tool metadata and accepted forward references that cannot be safely executed.
3. Research routing exposed location-search tools; runtime maps guards were also incomplete.
4. Finalization treated a model response as success even when an important tool failed. Approval-control outcomes such as `USER_REJECTED` remain benign and do not make a cancellation partial.
5. Email compose/send had no verified attachment path through schemas, tools, MIME construction, approval propagation, or the primary UI.
6. The SSE client assumed a response body existed, dropped malformed frames, and did not flush a final frame at EOF.
7. Gmail OAuth and API clients now share one explicit verifying SSL context: configured CA bundle first, OS trust store through `truststore` when installed, otherwise `certifi`. Hostname checking and `CERT_REQUIRED` remain enabled.

### Files changed in this pass

- `backend/app/agents/graph.py`: malformed-result failures, partial status, benign approval controls, maps guard, and final status handling.
- `backend/app/tools/routing.py`, `backend/app/tools/plan.py`: focused research tools and validated dependency metadata.
- `backend/app/services/chat_service.py`, `backend/app/models/task.py`: truthful partial persistence and activity status.
- `backend/app/services/email.py`, `backend/app/schemas/__init__.py`, `backend/app/api/emails.py`, `backend/app/tools/email_tools.py`: owned-file attachment validation, MIME assembly, provider payloads, and approval propagation.
- `frontend-react/src/lib/sse.ts`, `frontend-react/src/lib/api.ts`, `frontend-react/src/lib/types.ts`, `frontend-react/src/views/EmailView.tsx`: safe SSE handling and document-backed compose attachments.
- `backend/tests/test_agentic_hardening.py`, `backend/tests/test_fix_repair.py`: deterministic regression coverage.

### Verification evidence

- Focused backend hardening/routing/email/graph/job/chat batch: **164 passed**.
- New hardening tests: **5 passed**.
- Frontend production build: **PASS** (`npm run build`); one existing Vite chunking warning.
- Live local health: backend `http://127.0.0.1:8000/api/health` **200**; frontend `http://localhost:5173` **200**; Ollama `http://localhost:11434/api/tags` **200**; LM Studio `http://localhost:1234/v1/models` **200**.
- The running backend listener was inspected and uses system Python 3.12, not the project virtualenv. The TLS fix is application-level and therefore survives normal startup without shell activation; tests used the project virtualenv explicitly.
- Live authenticated `/api/chat` with the supplied job profile context completed as `intent=research`, used `search_jobs`, and produced no `find_place` activity. It honestly reported that candidate vacancies still require opening/verification. No application or email was sent.
- Gmail TLS tests mock `httpx.AsyncClient`, assert `CERT_REQUIRED` and hostname checking, and assert transport errors do not expose OAuth request data. No live Gmail exchange was attempted without an authorized mailbox.

### Attachment status and remaining limitations

The attachment path is implemented and mock-validated: owned document/artifact resolution, readable-file checks, filename/MIME/size limits, base64 MIME payloads, mock receipts, and approval argument propagation. The frontend uploads via `/api/documents` and sends `document_id` references. Real Gmail delivery remains **NOT TESTED** because no Gmail integration is connected and external sending was not authorized.

The live job run searched successfully but did not complete verify → tailor CV → artifact → email. The authenticated browser shell loaded, while direct live `/api/chat` provided the reliable execution evidence; the browser’s current local session did not visibly advance after forced DOM interaction, so this is recorded as a UI interaction limitation, not a claimed full browser-chat pass. No certificate verification was disabled, arbitrary CA was trusted, real email/job application/telephony/paid action was performed, or commit/push/deployment was made.
