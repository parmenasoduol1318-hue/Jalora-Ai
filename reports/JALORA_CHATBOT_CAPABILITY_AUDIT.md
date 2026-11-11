# Jalora chatbot capability audit

Date: 2026-10-23  
Scope: existing local Jalora application, current working tree, live localhost services, deterministic tests, and the shared browser. No commit, deployment, paid action, real email, application submission, or phone call was performed.

## Executive summary

The chatbot has a real FastAPI → conversation persistence → model router → LangGraph → tool registry → SSE/frontend pipeline. It is not ChatGPT-parity complete. The strongest verified areas are deterministic mathematics, authenticated conversation ownership, task planning state, approval-gated side effects, intent routing, structured tool results, and bounded recovery. The main partial areas are model-dependent instruction following, long-context relevance, current-source research verification, artifact workflows, multilingual/vision verification, browser streaming edge cases, and live external integrations.

### Status totals

| Status | Count | Meaning |
|---|---:|---|
| COMPLETE | 8 | Important behavior is implemented and supported by automated and/or live local evidence. |
| PARTIAL | 21 | A meaningful implementation exists, but behavior is model-, provider-, UX-, or scenario-dependent. |
| MISSING | 0 | No capability was labeled complete merely because a stub exists; unsupported parts are marked below as partial or not verifiable. |
| BROKEN | 0 | Known defects found in this pass were repaired or explicitly retained as partial limitations. |
| NOT VERIFIABLE | 3 | The feature is advertised or wired but needs an unavailable modality/provider or authorized external account. |

## Capability matrix

| Capability | Status | Actual evidence | Remaining work / acceptance gap |
|---|---|---|---|
| Natural-language understanding | PARTIAL | Intent classifier, model prompts, context loading, and live research request work. | Paraphrases, ambiguity, spelling variation, and reference resolution remain model-dependent and need broader live cases. |
| Instruction following | PARTIAL | Added measurable sentence/bullet/JSON checks and one repair attempt in the graph; 3 deterministic tests pass. | More constraints and live browser coverage are needed; a failed repair is reported rather than silently claimed valid. |
| Conversational quality/personality | PARTIAL | Local model responds through the actual application and identity text reflects Jalora. | Personality and capability claims vary by configured model; no reliable quality metric exists yet. |
| Multi-turn dialogue | PARTIAL | Conversation history is persisted and prior turns are passed to the model; backend context tests pass. | Live follow-up, correction, and topic-switch cases need a stable local-model acceptance fixture. |
| Conversation management | COMPLETE | Conversation creation, continuation, message persistence, refresh APIs, ownership checks, and deletion authorization are tested. | UI rename/delete behavior should receive a dedicated browser acceptance case. |
| Arithmetic and mathematics | COMPLETE | Deterministic math/finance paths, word-problem routing, verification, serialization, and frontend rendering are covered; focused math tests pass. | Advanced symbolic coverage remains bounded by the math engine. |
| Logical reasoning/problem solving | PARTIAL | Planning and deterministic engines support structured work; model reasoning quality is not independently verifiable. | Add objective contradiction/constraint checks for more task classes. |
| Planning/task decomposition | COMPLETE | LangGraph state tracks task/run/step/dependencies/status/tool calls; failure and partial-status tests pass. | Dynamic replanning after every class of external failure needs broader scenarios. |
| Accuracy/uncertainty/verification | PARTIAL | Tool results are schema-validated, malformed results fail, and research prompts prohibit fabrication. | Source quality and fact verification remain provider-dependent. |
| Immediate conversation context | COMPLETE | History loading, latest-turn preservation, tool-result truncation, and conversation isolation are tested. | Long-context live retention is still resource-sensitive. |
| Persistent memory | PARTIAL | Memory CRUD, consent/disable behavior, sensitive-memory blocking, provenance, and user isolation are tested. | Live UI proof of correction/deletion and relevance across new conversations remains limited. |
| Context relevance | PARTIAL | Memory search and workflow context are scoped by user; unrelated-task contamination has regression coverage. | Relevance ranking and conflict resolution need more objective evaluation. |
| Tool registry/discovery | PARTIAL | Central registry exposes names, schemas, permissions, categories, and manifests; tool tests cover many tools. | A complete contract audit for every registered tool’s output/timeout metadata is still needed. |
| Correct tool selection | COMPLETE | Intent subsets and runtime capability guards prevent research from exposing location lookup; regression tests and live job search show `search_jobs` without `find_place`. | Model selection remains probabilistic when the full task registry is intentionally enabled. |
| Tool arguments | PARTIAL | Pydantic/tool schemas and structured validation reject many missing/invalid values. | Some natural-language argument repair still depends on the model instead of a shared coercion layer. |
| Tool result interpretation | COMPLETE | `ToolResult` validation, malformed-result rejection, structured errors, artifact events, and honest status handling are tested. | Provider-specific empty/partial response contracts need more coverage. |
| Multi-tool execution | PARTIAL | Independent AUTO calls can run concurrently; dependencies and approval pauses are persisted. | More live dependent workflows are needed to prove no duplicate side effects across recovery. |
| Approval and safe execution | COMPLETE | CONFIRM/STRICT tools pause, approval/rejection paths are tested, and real side effects were not performed. | Authorized sandbox-provider acceptance remains external-account dependent. |
| Research/current information | PARTIAL | Live local research selected `search_jobs`; sources and search tools exist. | Vacancy opening/verification, citation quality, freshness, and multi-source comparison are not fully live-verified. |
| Writing/transformation/content | PARTIAL | Email, document, creative, and text-generation tools exist and are tested in service-level paths. | Broad tone/language/length preservation needs systematic live tests. |
| Files/documents/artifacts | PARTIAL | Upload/extraction, document ownership, generated artifact validation, downloads, and email MIME attachment bytes are covered. | Scanned/table/large-file behavior and complete browser upload-to-attachment flow need more live evidence. |
| Multilingual conversations | NOT VERIFIABLE | Translation is available only for explicit translation/task routing; no reliable Kiswahili live acceptance run was completed. | Test English, Kiswahili, mixed language, names, units, and follow-up language persistence with a capable local model. |
| Images/screenshots | NOT VERIFIABLE | Media upload/analyze routes and tests exist. | A real image-question browser run with a configured vision-capable provider is required. |
| Voice/audio/video | NOT VERIFIABLE | Voice/STT/TTS and media tools have mocked/service tests. | Live microphone, interruption, TTS correspondence, and audio/video interpretation were not established. |
| Streaming/frontend rendering | PARTIAL | SSE protocol, terminal reply preservation, plan/tool/artifact rendering, cancellation, and frontend build are covered. ChatView and shared SSE parsing now handle empty bodies, malformed frames, and EOF flush. | Full browser regression suite should be rerun after the active ChatView parser repair. |
| Safety/privacy/permissions | PARTIAL | JWT ownership, approval gates, secret redaction, prompt-injection guidance, sandbox restrictions, and memory isolation are present/tested. | Needs broader hostile document/tool-output injection and cross-provider privacy tests. |
| Error handling/recovery | COMPLETE | Bounded retries, provider fallback, timeout mapping, partial completion, stop/pause, malformed tool results, and no-fabricated-success tests pass. | More provider-specific rate-limit/backoff behavior remains to validate. |
| Model/provider management | PARTIAL | Ollama and LM Studio are reachable locally; model routing, local fallback, timeouts, and safe metadata exist. | Provider capability matching and private-data routing need stronger policy tests. |
| Response quality control | PARTIAL | Structured tool validation and new explicit-format validation are implemented. | Citation/claim verification and broad answer-completeness checks are not automated. |
| Performance/resource use | PARTIAL | Step/tool/retry/history limits and cancellation exist; local model memory/latency was observed. | Multi-step research can still exceed comfortable local latency on the lightweight model. |
| Accessibility/interaction | PARTIAL | Keyboard composer, visible loading/stop states, and browser UI tests exist. | Dedicated keyboard/screen-size/ARIA acceptance coverage is incomplete. |

## Request lifecycle audit

The verified path is:

`ChatView.send()` → `POST /api/chat` → authenticated user and conversation ownership → history/memory/provider context → deterministic intent routing → `ModelRouter` → LangGraph `AgentState` → bound registry tools → validated `ToolResult` → final response/status/artifacts → persisted messages/task/run/activity → SSE `done` → ChatView rendering.

Relevant implementation files are [ChatView.tsx](../frontend-react/src/views/ChatView.tsx), [chat.py](../backend/app/api/chat.py), [chat_service.py](../backend/app/services/chat_service.py), [graph.py](../backend/app/agents/graph.py), [routing.py](../backend/app/tools/routing.py), [base.py](../backend/app/tools/base.py), and [sse.ts](../frontend-react/src/lib/sse.ts).

## Defects repaired during this audit

1. The active ChatView had an independent SSE parser that used a non-null response-body assertion, silently ignored malformed events, and dropped a trailing frame. It now validates the body, surfaces malformed events, and flushes EOF data.
2. Explicit format requirements were previously prompt-only. A reusable validator now checks exact sentence counts, bullet counts/limits, and JSON-only responses, then performs one corrective model attempt.
3. An unrelated blocked tool could incorrectly make an otherwise successful independent workflow partial. `INTENT_MISMATCH` is now treated as a routing correction when the valid requested work completes; real tool failures remain partial.
4. The earlier pass repaired malformed tool-result success, research/maps routing, task dependency persistence, truthful partial status, attachments, and Gmail TLS verification. Those changes remain part of the current audit baseline.

## Automated verification

Commands executed in the project virtualenv:

```text
pytest tests/test_chat.py tests/test_math.py tests/test_memory.py tests/test_memory_v2.py tests/test_intent.py tests/test_react_frontend.py tests/test_capability_acceptance.py tests/test_readiness_gaps.py -q
```

Initial result: **239 passed, 1 failed**. The failure exposed the incorrect partial classification for a blocked unrelated tool; after repair, `tests/test_capability_acceptance.py` passed **9/9**.

```text
pytest tests/test_response_validation.py tests/test_capability_acceptance.py -q
```

Final result for that repair group: **11 passed**; the response-validation integration test exercises the API/graph path with scripted model outputs.

Earlier focused regression evidence retained from the same working tree: **164 passed** across hardening, routing, email, graph, job, and chat suites; frontend `npm run build` passed.

Final browser validation: `npx playwright test --project=chromium --workers=1` passed **20/20**. An initial four-worker run produced 17/20 because three shared SQLite/browser-state tests raced; each failed test passed in isolation. The Playwright configuration now forces one worker for deterministic shared-server execution.

Final post-repair static diagnostics: no errors reported for [graph.py](../backend/app/agents/graph.py), [response_validation.py](../backend/app/services/response_validation.py), [ChatView.tsx](../frontend-react/src/views/ChatView.tsx), [sse.ts](../frontend-react/src/lib/sse.ts), or [playwright.config.ts](../frontend-react/playwright.config.ts).

## Live versus mocked evidence

Live local services verified:

- Backend health: `http://127.0.0.1:8000/api/health` → HTTP 200.
- Frontend: `http://localhost:5173` → HTTP 200.
- Ollama: `http://localhost:11434/api/tags` → HTTP 200.
- LM Studio: `http://localhost:1234/v1/models` → HTTP 200.
- Authenticated live chat used the actual Jalora API and local Ollama; job research selected `search_jobs`, retained the existing profile context, and reported that vacancies still require individual verification.
- The shared browser loaded the actual Jalora UI and entered/stopped a research run through the visible composer, showing the real “Stopped” state.

Mocked/deterministic evidence:

- External Gmail, search-provider, calendar, telephony, and other consequential integrations are mocked in automated tests unless explicitly stated above.
- No real email, job application, booking, payment, call, or paid API action was authorized or performed.

## Outstanding issues and manual verification

- Run the final browser acceptance cases with a stable test account and the current frontend build: strict sentence/bullet/JSON formatting, arithmetic rendering, weather uncertainty, context recall/correction/topic switch, tool-required/no-tool requests, missing arguments, timeout, long Markdown/code/math response, and refresh/new conversation.
- Authorize a sandbox Gmail account if real provider submission verification is required; TLS verification must remain enabled.
- Provide a vision-capable provider/model for image acceptance and a controlled voice device/provider for voice acceptance.
- Improve local-model latency or use a larger-resource environment for full research → verification → artifact → email workflow validation.

This report deliberately does not claim ChatGPT parity. It records the specific implemented behavior and evidence available in the current Jalora environment.

## Email intent mismatch and approval-state investigation

### Root cause

The incorrect email content was not generated by the approval card itself. The production `send_email` tool description contained unrelated concrete recipient, subject, and message examples. That description is included in the model's tool schema/context, so the local model could copy those examples when constructing a new call. The same observed values also existed in older extraction-test fixtures, but no matching hard-coded email template remains in `backend/app` after this repair. The email prompt also contained concrete subject examples and was generalized to describe the required behavior without seeding a particular task.

The approval pipeline already persisted the recipient, subject, body, and attachment arguments, but the chat approval UI was read-only. A pending response could also surface optimistic model wording; it now reports that review is required and that no email has been submitted until the provider-confirmed execution path runs.

### Changes

- Removed concrete email examples from [email_tools.py](../backend/app/tools/email_tools.py) and generalized the email composition guidance in [prompts.py](../backend/app/agents/prompts.py).
- Added owner- and status-checked editing for pending agent email approvals in [runs.py](../backend/app/api/runs.py) and [chat_service.py](../backend/app/services/chat_service.py). The persisted approval arguments and paused run state are updated together, including the exact tool-call arguments that will resume.
- Added the same edit operation for standalone compose approvals in [emails.py](../backend/app/api/emails.py).
- Added an atomic approval claim in [chat_service.py](../backend/app/services/chat_service.py) and [emails.py](../backend/app/api/emails.py): only one caller can move a pending approval to `sending`; provider-confirmed, failed, and ambiguous outcomes are persisted separately, preventing duplicate sends and blind retries.
- Added editable recipient, subject, body, and document attachment upload controls to [ChatView.tsx](../frontend-react/src/views/ChatView.tsx). The regular email compose approval path in [EmailView.tsx](../frontend-react/src/views/EmailView.tsx) now persists edits before approval as well.
- Added localized labels and approval-card styling in [i18n.ts](../frontend-react/src/lib/i18n.ts) and [styles.css](../frontend-react/src/styles.css).
- Added approval-draft/state regression coverage in [test_fix_repair.py](../backend/tests/test_fix_repair.py).

### Verification

- Focused backend approval regression: **9 passed** (`test_fix_repair.py::TestEmailApprovalDraftIntegrity` plus `test_approvals.py`), including a provider-validation failure that remains `failed` and unsent.
- Existing email/approval/extraction group: **52 passed** (`test_approvals.py`, `test_email_extraction.py`, `test_phase_e_email.py`).
- Post-hardening combined regression command: **126 passed** across `test_fix_repair.py`, `test_approvals.py`, `test_phase_e_email.py`, and `test_email_extraction.py`.
- Atomic approval replay checks: **2 passed**, including generic and standalone approval double-submit protection; the standalone provider invocation count remained exactly one.
- Frontend production build: **passed** (`npm --prefix frontend-react run build`).
- Shared browser session: actual Jalora UI displayed a relevant pending email draft, showed no sent-success claim, exposed editable recipient/subject/body and an attachment control, and emitted the captured edited PATCH payload. The test stopped before approval; **no real email was sent**.
- Live Ollama browser request: **attempted but stopped** after the lightweight local model remained in generation; this is reported as unverified live-model composition, not as success.
- Provider execution remains mocked in automated tests. Invalid certificate verification was not disabled, and no external Gmail/provider action was authorized.

### Remaining limitation

The lightweight live Ollama model can be too slow or unreliable for this approval turn. The deterministic backend and frontend approval paths are covered, but a stable local model or configured test provider is still needed for repeatable live natural-language email composition. The user must still review and explicitly approve any real email; no approval was performed in this audit.

## Workflow execution and placeholder investigation (2026-10-24)

### Evidence and root causes

The observed `[Recipient's Name]` / `[Your Name]` text was not found as a current hard-coded economic or email template in `backend/app`. Repository search found the old concrete email examples only in historical test fixtures/reports, while the job cover-letter builder did contain a generic `[Your Name]` production placeholder. Model-generated content could therefore pass through the artifact and email paths without a final-content check. The cover-letter placeholder has been removed, and all generated user-facing text now has a shared unresolved-marker validator.

The false-success path had two contributing causes: the model could return procedural text or a success claim after a tool failure, and the legacy task-engine email adapter did not use the same atomic approval/result state transitions as the main graph. The send service now validates the final draft immediately before provider invocation, the graph rejects incomplete email drafts before creating approval, and the task engine records `sending`, `approved`, `failed`, or `outcome_unknown` based on the actual execution result. A tool invocation or approval card is not a send receipt.

### Workflow safeguards implemented

- Added [content_validation.py](../backend/app/services/content_validation.py), which detects unresolved bracket, curly, angle, TODO/TBD, and instructional markers while preserving real Markdown links.
- [email.py](../backend/app/services/email.py) now validates generated subjects/bodies, pending drafts, and final send payloads. Invalid or incomplete content is rejected without calling a provider.
- [graph.py](../backend/app/agents/graph.py) rejects invalid `compose_email`/`send_email` arguments before an approval card is presented.
- [generation_tools.py](../backend/app/tools/generation_tools.py) rejects empty or unresolved content and verifies each output exists as a non-empty file before returning an artifact.
- [job_tools.py](../backend/app/tools/job_tools.py) refuses search/directory URLs, requires multiple vacancy-specific evidence markers, and saves only verified individual vacancy pages. Search results remain candidates until opened and verified.
- [task_engine.py](../backend/app/services/task_engine.py) no longer injects a task-specific country default into generic job plans; location is taken from the current request/context or remains unspecified.
- [task_engine.py](../backend/app/services/task_engine.py) now atomically claims approvals, prevents duplicate concurrent execution, and records provider failures as failed or outcome-unknown rather than success.

### New deterministic verification

- `compileall backend/app`: passed.
- Final combined workflow/email/recovery/task regression command (`test_content_and_workflow_validation.py`, `test_model_failure_recovery.py`, `test_phase_e_email.py`, `test_fix_repair.py`, `test_tasks.py`): **122 passed**.
- The recovery test proves a model failure after a successful tool preserves the tool result and returns `partial` instead of discarding state or claiming completion. Research artifacts require a tracked HTTP(S) source and a citation/link to that source inside the generated report.
- The direct `/api/emails/task` path now rejects invalid generated drafts before auto-send; the regression test confirms the provider invocation count remains zero.
- Repository Playwright acceptance against the running localhost application: `npm run test:e2e -- e2e/approval.spec.ts --project=chromium --workers=1` passed **3/3**. The new intercepted email case verified relevant subject/body, no pending-send success claim, editable recipient/subject/body, attachment upload control, persisted edits, and rejection. The provider endpoint was never called; this was a safe browser-level test with mocked SSE/API responses.
- Full repository Playwright suite against localhost passed **22/22** with one worker, covering authentication, approval, chat streaming, calendar, history, scheduling, tasks, workspace navigation, conversation management, and file upload. The upload pass used the explicit local embedding provider because the configured Ollama instance lacks `nomic-embed-text`.
- The new tests cover placeholder detection, Markdown-link exclusion, empty/incomplete generated content, non-vacancy directory pages, and individual-vacancy evidence. The existing group covers pending approval, rejection, edited drafts, attachment validation, provider failures, and duplicate approval protection.
- External research, Gmail, calendar, and job providers remain mocked in automated tests. No real email or application was sent.
- `pytest backend/tests/test_tasks.py -q`: **11 passed** after updating stale expectations to the truthful `partial` status for blocked policy/budget work and the current plan dependency schema.

### Remaining workflow limitations

The current report-generation pipeline still depends on the configured model to supply source-backed research content; the new artifact gate prevents empty or templated documents but does not independently fact-check arbitrary prose. A future research-specific validator should require structured source records and required analysis fields before document generation. Live end-to-end research-to-report-to-email remains unverified when the lightweight local model stalls; this is recorded as a limitation rather than a success claim.

## Local milestone verification update (2026-10-24)

The active localhost stack is verified at backend `:8000`, frontend `:5173`,
Ollama `:11434`, and LM Studio `:1234`. The scheduled-communication routing
regression was fixed and the focused intent suite passed **20/20**; the combined
agent/chat/memory regression passed **149/149**. Memory and preference controls
passed **48/48**. Calendar OAuth/mock, voice, telephony, and travel contract tests
passed **81/81** with one dependency warning. The React production build passed
and the browser suite passed **22/22**. Live Google, Gmail, telephony, travel,
and payment operations remain unverified and approval-gated; no consequential
external action was performed.
