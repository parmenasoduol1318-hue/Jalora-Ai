# JALORA Capability and Agentic Architecture Audit

**Scope:** Read-only source audit and focused tests of the checkout at `jalora/`.  
**Implementation boundary:** This audit began as a source-only report. Subsequent incremental work added task controls, deterministic source-evidence checks, a source-aware queue derived from the canonical run, provider receipt persistence, and task-page editing/approval controls backed by the existing Approval rows. The complete target plan remains unfinished.  
**Evidence limits:** Backend and browser tests used isolated databases, fake providers, and isolated local servers. The full Chromium E2E suite passed using managed Playwright Chromium. An existing authenticated shared-localhost session accepted a text smoke test and returned the requested exact reply using its configured local model. No live email, WhatsApp, external search/model provider, or device action was performed. No production conversation trace was available to attribute a past live failure.

## Executive findings

1. JALORA does have a real central chat agent: `/api/chat` enters `chat_service.execute_chat`, which runs the LangGraph supervisor, plans through `update_plan`, selects registered tools, applies policy, persists run state, and pauses for approvals. Schedules also call this entry point.
2. The eight-recipient approval path is implemented and passes its focused mocked test. It verifies one send approval at a time, skips the first organization, edits the second draft, then verifies one mock send for each remaining approval. The task queue exposes the pending recipient, subject, body, and attachments; edits persist through the existing approval API, and explicit approve/reject/skip actions resume the same run. An uncertain-send check is available without retrying. Browser E2E uploads an attachment through the local API and fetches the returned owner-scoped artifact; approval editing/send remains mocked in that browser test. A fresh-process test clears serialized run state, starts a new backend app against the same SQLite database, and successfully skips a pending email with no send. It does **not** verify real organization contact addresses/domains, Gmail delivery/reconciliation, or post-restart approved sending.
3. A previously reproduced single-recipient email regression duplicated the current request in outreach-classification context, making one address look like multiple recipients. The current checkout passes all 41 tests in `test_phase_e_email.py`; the former “shared or separately tailored” clarification regression is no longer present in that focused suite.
4. Public-contact sends now fail closed unless the exact recipient was extracted from a successfully opened page that is linked as a dependency of that send step in the current task plan. A negative end-to-end approval test and the eight-recipient positive mock test cover these paths. This proves page evidence binding, not that a page is genuinely operated by the named organization; search and page content remain untrusted.
5. A durable per-organization queue view derives from the shared Task plan, persisted AgentRun/message history, and Approval rows; approvals are linked to plan steps. Tasks displays recipient order, source page, observed address-match status, delivery status, and provider receipt when available. Pending drafts can be edited, saved without sending, rejected, skipped, or explicitly approved from the queue. If an outreach run has lost `state_json`, its queue can be reconstructed after a fresh backend process starts; tests verify both a post-restart skip and an approved mock-email send with the provider receipt recorded.
6. There are overlapping execution paths: the central chat graph, a `TaskEngine`, a linear `WorkflowEngine`, a voice-specific tool loop, and separate email endpoints. Some reuse the shared registry or persistence; they do not all use the same planner, task state, and approval lifecycle.

## 1. Existing project inventory

The inspected repository root contains:

| Area | Actual contents / role |
|---|---|
| `backend/app/` | FastAPI application: `agents/`, `api/`, `auth/`, `database/`, `models/`, `schemas/`, `services/`, and `tools/`. The inspected tree has 35 model modules, 47 tool modules, and 35 API modules including the creative subpackage. |
| `backend/alembic/` | Database migrations; startup uses the migration system (`backend/app/database/session.py`, `backend/app/main.py`). |
| `backend/tests/` | 83 `test_*.py` modules. `backend/tests/conftest.py` configures test fixtures and mocks. |
| `frontend-react/` | Primary inspected React/TypeScript/Vite UI; views include Chat, Tasks, Email, Calendar, Documents, Integrations, Devices-related controls, Schedules, Memory, People, Activity, and Creative Studio. |
| `frontend/` | Contains a lockfile but no source tree was found in the inspected directory. Its role is unclear. |
| `jalora/frontend-react/` | A nested second frontend tree exists; whether it is active or a copy is not established by this audit. |
| `plans/`, `docs/`, `scripts/`, `config/`, `data/`, and model/training directories | Planning, product documentation, scripts/configuration, application data, and model/training assets. These are not evidence that a feature works. |

Primary entry points are `backend/app/main.py`, `backend/app/api/__init__.py`, `backend/app/api/chat.py`, `backend/app/services/chat_service.py`, `backend/app/agents/graph.py`, `backend/app/tools/__init__.py`, and `frontend-react/src/App.tsx`. The main UI appears to be `frontend-react`; the extra frontend directories should be confirmed before cleanup.

## 2. Current capability matrix

Status labels distinguish implementation from external readiness. “Verified” below means verified in source and/or the listed local mock tests, not in a live provider account.

| Capability | Actual status | What source implements / evidence | Limitations and dependencies |
|---|---|---|---|
| Central chat agent, planner, and registry | **IMPLEMENTED AND VERIFIED (local path)** | `/api/chat` calls `execute_chat`; `chat_service.py` invokes the graph. The graph validates plans and dispatches tools from the shared registry. The graph, voice, and saved workflows share capability-argument validation through `services/tool_preflight.py` and structured-result decoding through `services/tool_execution.py`; malformed results cannot be treated as voice success. Model selection and policy/approval lifecycle are still not unified across those entry points. The registry ranks capabilities from descriptions, tags, and schemas. | Planner/tool choice still depends on model output. It is not a deterministic workflow compiler. Provider availability and model quality are external. |
| Public organization contact lookup | **PARTIALLY IMPLEMENTED; external search dependent** | `tools/web.py::search_public_contacts` searches organizations concurrently, returns per-organization results/failures and marks results `untrusted`. `open_url` extracts page emails. The graph requires the send recipient to match an address extracted from a successfully opened page linked through that send step's plan dependency. | Only up to three candidate URLs are considered per organization and the plan helper selects the first URL. Page ownership and official-domain identity are not independently verified. Live source quality is unverified. |
| Multiple-organizations outreach | **IMPLEMENTED AND VERIFIED (mocked sequence and task-page controls)** | `test_approvals.py::test_public_organization_outreach_is_approved_one_email_at_a_time` covers eight named organizations, one approval at a time, skipping the first recipient, editing the second body, and one mock send for each remaining approval. A distinct skip test verifies persistence and no send. A fresh-process test with missing serialized run state verifies queue reconstruction and a no-send skip. Tasks shows source-aware entries and allows editing/saving, attaching/removing files, rejecting, skipping, and approving/sending the pending draft. | The tests use `.example.org` addresses, fake search/page results, and `email_mock`; they do not prove real contact verification or delivery. Post-restart approved sending has not been verified. |
| Email inbox, drafts, send | **IMPLEMENTED; live Gmail path not verified** | `tools/email_tools.py` and `services/email.py` implement search/read/thread listing, draft creation, batch drafts, single/batch sends, Gmail and `email_mock`. Sends carry a provider receipt/message ID when available. `/api/emails` provides compose and explicit standalone approval. | Gmail OAuth/configuration, quota, network, and provider responses are external. Mock-provider drafts are not durable across backend restarts. |
| Email editing and attachments | **IMPLEMENTED FOR PENDING CHAT OUTREACH APPROVALS; live delivery unverified** | `ChatView.tsx` and `TasksView.tsx` expose editable recipient, subject, body, CC/BCC in Chat, and attachments; task queue edits are persisted through the same `/api/approvals/{id}` endpoint. Email upload validates MIME and size; attachment resolution is owner-scoped. Queue E2E exercises attachment upload/removal/persistence via the local API and fetches the resulting user-scoped artifact before approval. | Revision requests do not yet have a distinct persisted outcome; save-without-send leaves the approval pending. |
| WhatsApp and SMS | **IMPLEMENTED; external-provider dependent** | `tools/comms.py` registers approval-gated send tools; `services/comms.py` supports mock, Twilio, and Telnyx send paths. Inbound webhook endpoints exist in `api/comms.py`. | Requires provider credentials, sender setup, webhook configuration, and network. Outbound send tools do not constitute a WhatsApp conversation search/read/history API; no general WhatsApp inbox search/summarization path was verified. |
| Contacts | **IMPLEMENTED (local address book)** | `contact_tools.py` and `services/contacts.py` support find/create/update and phone resolution. | Contact data is user-local; provider/device call history integration was not established. Email recipient lookup from contacts should be distinguished from public organization lookup. |
| Calls | **PARTIALLY IMPLEMENTED; provider dependent** | `place_voice_call` uses the comms provider path; `api/calls.py` and the call UI support a separate call/task lifecycle. | There are separate call paths. Live call completion and result verification depend on Twilio/Telnyx. The voice-call tool is not a full contact/call-history system. |
| YouTube and media | **PARTIALLY IMPLEMENTED** | `device_tools.py::open_youtube` opens the YouTube home page in the Windows default browser. Uploaded media has image/audio/video analysis tools. | No verified YouTube search, target-video identification, playback control, or proof that a requested video began playing was found. Opening the home page is not playback verification. |
| Web research and browsing | **IMPLEMENTED; external search/browser dependent** | `search_web`, `search_public_contacts`, `open_url`, `browse_page`, and browser-session tools are registered. Web results/page output are marked untrusted; browser URLs are safety-checked. | Search provider configuration and live pages are external. The public-contact-to-email proof gap above remains. Browser-session actions are not themselves a persistent multi-app task queue. |
| Files and documents | **IMPLEMENTED (source path); runtime coverage varies** | `api/documents.py` supports upload, search, query, delete, and progress. Document tools retrieve user-owned passages. `generation_tools.py` creates PDF/DOCX/XLSX/CSV artifacts; DOCX generation uses `python-docx` and validates the produced file. | Correct output still depends on correct content/tool arguments and installed libraries. This audit did not run a document-generation test. Attachment support is implemented but not part of an outreach queue. |
| Calendar, reminders, tasks | **IMPLEMENTED, with overlapping paths** | Calendar tools support find/create/update/delete; schedules persist and fire reminders or call `chat_service.execute_chat`. `TasksView` shows plan/progress, pauses only running tasks, allows cancellation of queued/running/approval-waiting/paused tasks, and surfaces partial-task errors. Cancellation persistently rejects pending approvals and refuses while an approved action is in flight. Backend lifecycle and state-specific UI regressions pass. | The `TaskEngine` is a second task state machine and no application call site for `get_engine()` was found in the inspected app tree. Direct calendar APIs and chat tools are separate surfaces. Background scheduler reliability was not exercised here. |
| Memory and conversations | **IMPLEMENTED (shared chat memory; limited cross-surface sharing)** | Conversation/messages persist in SQL models; long-term memory uses user-scoped rows, semantic/lexical retrieval, and sensitive-content filters. Chat resume retains the system persona (covered by an approval test). | Voice and external SDK paths have their own conversation/session state. Shared access to the same authorized long-term context across every capability was not established. |
| Voice input/output | **PARTIALLY IMPLEMENTED; provider/device dependent** | `/api/voice` provides transcription and live WebSocket sessions; the session has model routing, audio/TTS, and a voice-specific confirmation flow. | `voice_session.py` runs its own model/tool loop rather than the shared planner/task graph. Confirmation behavior is implemented separately and does not apply the full shared per-user policy decision shown in the graph. |
| Windows/local-device automation | **PARTIALLY IMPLEMENTED; platform/consent dependent** | Local tools are Windows-only and require the exact enabled user-approved app; inspect/click/type/key/close actions use the local-device service and report action results. | Limited to allowlisted apps and supported accessibility/OCR controls. Not general unrestricted computer control. |
| Paired/remote device companion | **PARTIALLY IMPLEMENTED; companion-dependent** | `api/devices.py` has consented pairing, scoped tokens, target approval, remote commands, and inbox/action routes. `request_remote_url` validates public URLs and requires both sender and target consent. | No native Android project (`.kt`, `.java`, Gradle files) was found in this repository. The web/backend pairing protocol does not prove a functioning Android companion or broad Android automation. |
| Other tools | **IMPLEMENTED AS REGISTERED MODULES; per-tool verification varies** | The registry also imports math, maps, weather, translation, coding, payments, travel/hotels, jobs, business planning, creative generation, meeting preparation, verification, support, and workflow tools. | Each depends on its provider/library and has independent test coverage. Presence in the registry is not evidence that every provider is configured or every workflow is reliable. |

## 3. Shared agentic architecture assessment

| Component | Assessment | Evidence and gap |
|---|---|---|
| Natural-language understanding | **Implemented, bounded** | `agents/intent.py` deterministically classifies intents/signals; the model still interprets nuanced requests. The classifier can misclassify or trigger false workflow logic when context is assembled incorrectly. |
| Planning | **Implemented, model-generated** | Graph uses structured plans, dependencies, plan validation, progress synchronization, and plan recovery. The eight-recipient path shows the graph can serialize sends. There is no deterministic plan guarantee for arbitrary complex tasks. |
| Dynamic capability discovery | **Implemented, partial** | `ToolRegistry.select_for_task` and routing code discover a bounded tool set; the model selects calls from that set. Capability selection is not equally dynamic in secondary engines. |
| Execution and dependency order | **Implemented in central graph; multiple alternate engines remain** | The graph checks plan readiness and tracks tool results. The task engine and workflow engine execute their own plans; voice has a separate loop. |
| Shared memory/context | **Partial** | Chat and resumed chat use persisted conversation, run state, and memory context. Voice and the workspace SDK do not share the same graph state lifecycle. |
| Permission management | **Central in chat, inconsistent across secondary surfaces** | Chat graph calls `policy.decide`; `send_email` is explicitly forced through approval. Standalone email has a separate approval path. Voice gates by static permission level. Workflow engine separately refuses `CONFIRM` tools rather than using the graph's pause/resume lifecycle. |
| Human approval | **Implemented, generic with outreach skip** | `Approval` rows, atomic pending-to-sending claims, editable arguments, approve/reject/skip routes, and resume exist. No per-organization workflow entity or explicit revision state exists; stop/pause/resume use shared task controls. |
| Verification | **Partial** | Some tools return provider receipts, and plan state is reconciled from successful tool results. Public-contact sends require the exact address to appear on a successful opened page linked by plan dependency. A provider call receipt is not a delivery/read receipt. |
| Recovery | **Partial** | Run state and approval state persist; generic resume and safe no-auto-retry behavior exist for uncertain sends. An authenticated Gmail reconciliation endpoint checks exact sent-message metadata/body and only resolves a single match; zero or multiple matches remain `outcome_unknown`, and retries remain blocked. A fresh backend process can reconstruct and display a pending email queue when serialized run state is missing; post-restart approval execution and recovery for other tools remain unverified. |
| Background autonomy | **Implemented, scheduler-dependent** | Persistent schedule rows fire through APScheduler; scheduled tasks call the central chat pipeline and can notify when approval is required. Scheduler operation across production restarts/multiple workers was not verified. |

**Conclusion:** JALORA is not merely disconnected feature agents: a central LangGraph agent and shared registry are real. It is also not yet one uniform execution system across all surfaces. Several separately managed loops/state machines exist beside the central graph.

## 4. Eight-organization outreach failure analysis

### What passed

Command:

```text
python -m pytest tests\test_approvals.py::test_public_organization_outreach_is_approved_one_email_at_a_time -q
```

Result: **1 passed**. The eight-organization test asserts eight contact-page opens, no sends before approval, one pending `send_email` approval at a time, a persisted skip for the first recipient, edits preserved for the second recipient, and exactly one mock send for each remaining approval. During this check, a skip had left its plan step active and caused the executor to repeat the same tool node instead of presenting the next organization; the graph now marks that step skipped and synchronizes plan progress before continuing.

### What the test does not prove

The test substitutes `.example.org` domains, mocked search results, a fake page reader that returns each address, and `email_mock`. It therefore tests orchestration and approval sequencing, not the real organizations' domains, addresses, contact-page identity, or provider delivery. A separate single-organization test also covers skip and verifies no email is sent. Queue browser tests upload and fetch an attachment through the local API, cover add/remove/save, and expose uncertain-send reconciliation; service/API tests cover exact, missing, ambiguous, and out-of-window Gmail Sent matches. A fresh backend process can reconstruct and skip a pending queue, and can also approve a pending mock email after `state_json` is removed; the approval records the exact mock provider receipt. Live provider behavior and the real Vox Kenya content remain untested.

### Previously reproduced defect and current verification

The original source audit reproduced **33 passed, 2 failed**:

- `test_user_email_sentence_sends_after_approval_in_graph`: after the send approval, the run paused again for a “shared email or separately tailored email” question instead of completing.
- `test_plain_email_request_rejects_send_application_tool`: after the unsupported tool was correctly rejected, a one-address request still created that same irrelevant clarification approval.

The traced approval response confirms `request_user_input` is the unexpected second action. In `graph.py`, `_task_request` returns the original request from state; then `agent_node` forms `previous_user_request` from user messages in `messages[:-1]` and appends `latest_request` again. On a resumed/tool-call turn, the original user message is already in `messages[:-1]`, so the same address can be counted twice. `intent.py::has_multiple_outreach_recipients` treats more than one email-address occurrence as multiple recipients. A focused runtime check returned:

```text
single_request_multiple=False
duplicated_context_multiple=True
single_request_needs_clarification=False
duplicated_context_needs_clarification=True
```

Relevant source: `backend/app/agents/graph.py` around `_task_request` and `outreach_context` construction; `backend/app/agents/intent.py` around `needs_outreach_style_clarification` and `has_multiple_outreach_recipients`; failing regression tests at `backend/tests/test_phase_e_email.py:919` and `:960`.

The duplication was removed in the current checkout. The latest focused run of `python -m pytest tests\test_phase_e_email.py -q --tb=short` produced **41 passed**. This focused result does not establish the exact cause of a past live eight-organization failure: the corresponding mock acceptance test passes, and no matching live run/provider trace was available.

### Remaining structural cause of unreliable real outreach

`search_public_contacts` marks leads untrusted and returns snippets, URLs, and extracted candidate emails. `open_url` extracts page email addresses. The graph now blocks a public-contact `send_email` unless the address appears in a successful `open_url` result for the URL linked through that send step's plan dependency in the current task. Focused checks passed: `test_agentic_hardening.py` (34), `test_approvals.py` (including one-at-a-time eight-recipient approval and the unverified-recipient rejection), and `test_phase_e_email.py` (35). This deterministic check does not establish official domain ownership or content authenticity; the source remains untrusted and the email still requires user approval. Search failures are reported, but there is no persisted organization-level `needs_user_decision` status.

## 5. Missing or incomplete workflow functionality

| Requirement | Current state |
|---|---|
| One persistent record per organization with source, verification, draft, edits, decision, attachments, and provider receipt | No dedicated outreach table. The authenticated Task detail endpoint now derives a sequential queue from `Task.plan`, run state or persisted run messages, `Approval.plan_step_id`, and approval results. This survives completed-run state cleanup through conversation messages. Attachment IDs and full draft content remain in approval/run records rather than a normalized queue row. |
| Retrieved source shown with recipient before approval | The Tasks view now shows organization, recipient, retrieved source link/title, observed email match, and an explicit caveat that source content and ownership are not independently trusted. The existing Chat approval card displays editable message content before approval. |
| Deterministic check that a recipient occurs on its linked contact page | Implemented in the central graph: public-contact sends fail closed unless the exact address is present in a successful open-page result linked through that planned send's verification dependency. The opened page must also contain at least two meaningful organization-name tokens (or all tokens for a one-token name); email addresses are excluded from this identity signal. The Tasks queue exposes address presence and organization-name match separately. This is a conservative content match, not independent proof of official ownership; retrieved content remains untrusted. |
| Edit recipient/subject/body and add/remove attachments | Implemented in Chat and in the source-aware Tasks queue for a pending outreach approval; the queue persists edits through the shared approval endpoint. Playwright verifies local API upload, artifact fetch, remove, and save; live provider delivery remains unverified. |
| Approve and send one, then continue | Implemented and mock-tested for the eight-recipient scenario; the Tasks queue reports each outcome in plan order. |
| Request revisions | No dedicated revision state. Editing and saving the pending approval leaves it pending for a later approval decision. |
| Skip one organization and continue | Implemented through a distinct skip route/status on graph email approvals; the graph marks the corresponding plan step skipped, emits a no-send result, and advances to the next organization. The eight-organization test covers skip-first followed by seven separately approved mock sends; a separate focused test verifies skip produces no send. |
| Reject one and retain a decision while continuing | Generic rejection and task-queue reject controls are implemented; the eight-organization all-send test does not exercise rejection mid-sequence. |
| Save a draft without sending | Task queue draft edits can be persisted without sending; the approval remains pending. The mock provider's separate draft store remains in-memory. |
| Stop/resume the entire outreach workflow | Generic run stop/pause/resume exists. Tasks exposes shared task controls; workflow-specific stop/resume behavior still needs acceptance testing. |
| Restart/reconnect recovery | Run and approval records persist in SQL; task detail reconstructs queue data from task plan/messages/approval rows. Pending `send_email` approval edit/resume reconstructs missing state only when owner, task, planned step, and call identity match. Subprocess tests start a fresh backend app against the same SQLite database with `state_json` missing and verify both a skip with no send and an explicitly approved `email_mock` send with an exact provider receipt persisted. Recovery for non-email approvals remains unverified/unimplemented. |
| Provider receipt and uncertain send | Successful graph email tool results persist provider, message ID, thread ID, and status in the audit record and matching Approval result by exact tool-call ID. For uncertain results, authenticated `/api/emails/send/{approval_id}/reconcile` checks only Gmail Sent, compares recipient/subject/body and time window, and marks sent only when exactly one match exists. Zero or multiple matches remain unknown and `retry_allowed` is false. |
| Exactly one email per organization, including an audit record of why one was not sent | The derived queue preserves one row per planned send and exposes approval/send/unknown/skipped status. It is not a dedicated mutable ledger; explicit revision and stop decision history remains incomplete. |

## 6. Architectural duplication and consolidation proposal

| Existing path | Why it is separate/overlapping | Preserve and proposed disposition |
|---|---|---|
| `services/chat_service.py` + `agents/graph.py` | Primary planner, orchestration, tools, approval pause, and resume path. | **Keep as canonical central agent.** Add durable workflow state through this path rather than another agent. |
| `services/task_engine.py` | Has a second task state machine, registry of action-type handlers, and persistence schema. No `get_engine()` call site was found in the inspected application tree. | **Do not delete blindly.** Confirm deployment/use first; either adapt handlers behind graph capabilities or retire in a separately approved migration. |
| `services/workflow_automation.py` | Own workflow-pattern records and linear execution history; tool-backed and accessible through `workflow_tools`. It reuses the tool registry/policy but cannot pause/resume a `CONFIRM` tool in its own queue. Timeouts/execution exceptions persist `outcome_unknown` with the tool error code; cooperative cancellation persists `OUTCOME_UNKNOWN` before propagating cancellation. The tool warns users to check status before retrying; provider reconciliation/resume is not yet implemented. | Keep as a reusable, explicitly bounded workflow capability; delegate approval-required steps to the central graph instead of maintaining a second approval system. |
| `services/voice_session.py` | Own model loop and spoken confirmation; shares tool registry but not graph plans/run/approval objects. | Keep audio transport/TTS; route task/action decisions through shared policy and, where possible, the central execution service. |
| `api/emails.py` standalone send approval vs `api/runs.py` generic approval | Different send/approval handlers and result processing around the same `Approval` model/provider. | Keep the email UX; consolidate claim, edit, send receipt, unknown-outcome, and audit behavior in a shared approval/execution service. |
| `services/workspace_agents.py` Copilot/Codex adapters | The active `POST /api/workspace/chat` route invokes these SDK adapters when `model_provider` is `copilot` or `codex`; these remain separate model/tool loops. Copilot exposes only tools allowed by `services/policy.py` without an additional approval; whole-turn confirmation does not override that requirement. Its handler also runs Jalora's shared `ToolSpec` argument validation and structured-result decoder. Codex is blocked if the shared policy denies or requires approval for any registered file-mutating capability because its staged filesystem edits bypass individual Jalora tool handlers. The service independently requires the endpoint's explicit external-agent confirmation. | Preserve as optional external coding adapters, not autonomous planner peers. Per-file approval previews and durable shared `AgentRun`/`Task` lifecycle are not unified; the ordinary provider path remains the central graph. |
| Direct API surfaces (calendar, documents, emails, devices) | UI-initiated APIs coexist with agent tools; some share services, but they do not all originate from a graph task. | Preserve direct UI actions; share validation, permissions, audit and result contracts with tool execution. |

These are multiple execution systems, not evidence that every capability is an independent autonomous AI agent. The main architectural issue is inconsistent lifecycle/state/approval integration, not that modular tools themselves are separate.

## 7. Recommended target architecture

```text
React UI / voice / scheduled trigger / direct integration
                         |
               Central JALORA task API
                         |
        Shared planner + LangGraph orchestrator
                         |
   Durable task/workflow state + approvals + audit log
                         |
            Shared capability registry
          /            |                \
   research/web      email/comms      files/calendar/device
          \            |                /
       provider adapters and consented companions
```

Keep the current graph, registry, permission policy, run/approval persistence, schedule runner, and capability services where they work. Add the outreach queue as **data and UI managed by the central run**, not as another AI agent. Make secondary interfaces (voice, workflow patterns, direct email) call shared policy/execution helpers. Require a machine-checkable evidence object for public contact addresses; when evidence is missing or ambiguous, persist `needs_user_decision` and wait.

## 8. Implementation plan and progress

The initial proposal has been implemented incrementally without adding a second
agent or an independent outreach database. On 2026-11-08, the complete backend
suite passed (**1,618 passed, 7 skipped, 14 warnings**) after the graph began
executing normalized supplied arguments without injecting omitted schema defaults;
the React production build passed,
Alembic upgraded an isolated SQLite database to `v1f_approval_plan_step`, and
the complete Playwright suite passed (**40 tests**) against isolated local
frontend/backend servers using managed Chromium. The authenticated shared-localhost
chat smoke test also received its requested exact reply. No live provider or
real-device action was performed.

| Work item | Files / implementation | Contract and tests | Remaining limitation |
|---|---|---|---|
| Fix duplicated-request classification | `backend/app/agents/graph.py`; `backend/tests/test_phase_e_email.py`. | The prior one-recipient clarification regression is no longer reproduced; `test_phase_e_email.py` passes (41 tests). | Continue retaining single-recipient and multi-recipient regression cases. |
| Enforce public-contact source and name evidence | `backend/app/agents/graph.py`; `backend/app/api/tasks.py`; `backend/tests/test_agentic_hardening.py`; `backend/tests/test_approvals.py`; `frontend-react/src/views/TasksView.tsx`; `frontend-react/src/lib/types.ts`; `frontend-react/src/lib/i18n.ts`. | A send must match an extracted address from a successfully opened page linked by the send step's plan dependency, and the page evidence must include a conservative match against the planned organization name. A new regression proves that an address on an unrelated page is blocked even when that address is present. The persisted queue exposes name-match status separately from address presence, and the UI explicitly cautions that page ownership is not independently confirmed. | Name matching is a heuristic against untrusted page content, not independent authentication of official-site ownership. Real organization sources still require live research/user review. |
| Persist per-send approval linkage and receipt | `backend/app/models/agent.py`, `backend/alembic/versions/v1f_approval_plan_step.py`, `backend/app/agents/graph.py`, `backend/app/services/chat_service.py`. | `Approval.plan_step_id` links a decision to its planned send. Tool audit records include exact call ID and successful provider receipt; approval outcomes are matched by exact call ID, preventing a later pending approval from being attributed to an earlier send. Gmail Sent reconciliation now confirms one exact recipient/subject/body match within a bounded time window without retrying. | No separate normalized outreach table; reconciliation is Gmail-specific and cannot verify delivery/read status. |
| Expose, recover, and control the durable queue | `backend/app/api/tasks.py`, `backend/app/api/runs.py`, `backend/app/services/chat_service.py`, `backend/app/agents/graph.py`, `backend/app/models/agent.py`, `frontend-react/src/views/TasksView.tsx`, `frontend-react/src/lib/types.ts`, `frontend-react/src/lib/i18n.ts`, `frontend-react/e2e/tasks.spec.ts`, and `backend/tests/test_approvals.py`. | Task detail derives ordered entries from shared Task/Run/Message/Approval records. Individual and batch email approvals can be reconstructed only after task ownership, exact active planned step, original call ID, and capability name are verified; run messages retain a sequence index to avoid random UUID tie ordering. Structured `request_user_input` approvals recover only when the exact call ID and capability name remain in run-tagged messages; a mismatched persisted name fails closed. Tests cover the eight-organization sequence, skip behavior, source evidence, provider receipts, individual-email recovery across a fresh process, approved mock-email send recovery with receipt persistence, and missing-state batch/clarification edit-and-approve recovery. Playwright covers queue editing/save, attachment upload/removal through the local API, approval, skip, and uncertain-send reconciliation. | Recovery for other transient-state-dependent/non-email actions remains unverified. Revision has no distinct persisted outcome. |
| Make E2E launcher portable and stable | `frontend-react/vite.config.ts`, `frontend-react/playwright.config.ts`, `frontend-react/e2e/chat.spec.ts`, and `frontend-react/e2e/regression.spec.ts`. | E2E uses managed Chromium by default, optionally selects installed Chrome/Edge, supports isolated port overrides, and waits for the settings response/navigation readiness in flaky test paths. The current full suite passes (40 tests), including uncertain-send reconciliation. | Existing bundle-size and import warnings remain unrelated. |
| Reconcile uncertain email outcomes without retry | `backend/app/api/emails.py`, `backend/app/services/email.py`, `backend/tests/test_phase_e_email.py`, `frontend-react/src/views/TasksView.tsx`, `frontend-react/src/lib/i18n.ts`, and `frontend-react/e2e/tasks.spec.ts`. | The authenticated reconciliation endpoint searches Gmail Sent and confirms only one exact recipient/subject/body match within a bounded time window. Unresolved outcomes explicitly disallow retry. Tasks offers a check-status action only for `outcome_unknown`; Playwright verifies unresolved results stay blocked and a confirmed result removes the control. The focused email suite passes (41 tests); task E2E passes (7 tests). | Live Gmail behavior and real provider timeouts are unverified. Zero/multiple-match user remediation and provider coverage beyond Gmail remain limited. |
| Verify pending outreach recovery across a backend restart | `backend/tests/test_approvals.py` and existing task/run/message/approval persistence paths. | Subprocess tests boot a fresh FastAPI app against the same SQLite database after `state_json` is cleared. They verify pending-approval reconstruction and skip without sending, plus approval of a mock email after restart with an exact provider receipt stored against the approval. The suite also verifies structured user-input recovery. | Recovery for other transient-state-dependent actions remains open. |
| Apply shared action policy to voice dispatch | `backend/app/services/voice_session.py`, `backend/tests/test_voice_live.py`, and `backend/app/services/policy.py`. | Voice snapshots user autonomy/tool policies, validates tool arguments, applies `policy.decide`, reports denied calls, and asks before tools whose effective policy requires approval. The focused voice/telephony suite passed (40 tests), the latest `test_voice_live.py` run passed (22), and the complete backend suite passed (1,618 passed, 7 skipped, 14 warnings) after final graph argument normalization. | Voice still has an independent model/tool loop and lacks shared `Task`/`AgentRun` persistence. Workflow patterns use the same policy function but keep a separate executor; unify lifecycle/execution only after the remaining call-graph and compatibility tests. |
| Validate and normalize saved-workflow capabilities | `backend/app/services/workflow_automation.py` and `backend/tests/test_workflows.py`. | `WorkflowEngine.create_pattern` rejects unregistered tool names and stores the validated trimmed name, preventing a whitespace-padded registered name from becoming unusable at execution; focused workflow suite passed (16 tests). | This validates registry membership at creation only; saved workflows still use a separate linear executor and lifecycle. |

No new autonomous agent is proposed.

## 9. Prioritized development roadmap

1. **Keep request classification regressions covered.** The reproduced single-recipient issue passes focused tests; continue guarding it as routing changes.
2. **Extend research evidence safely.** Exact page-email matching is enforced; verify organization/page ownership or request explicit user resolution without trusting search snippets.
3. **Complete workflow persistence.** Pending outreach can rebuild from durable records if serialized state is missing, but add an actual backend process-restart acceptance test and persist explicit per-organization revision outcomes; broaden recovery only where the state is sufficient to replay safely.
4. **Finish safe-send outcomes.** Gmail reconciliation is implemented for a unique exact Sent match and never retries; validate live provider behavior safely and define user handling for unresolved/ambiguous results.
5. **Finish the outreach review UX.** Source-aware edit/save/reject/skip/approve and uncertain-send reconciliation controls are on the task queue; stop/pause/resume remain shared task controls. Browser E2E verifies local attachment upload and owner-scoped artifact retrieval, but its approval/send API is mocked. Add distinct revision semantics and live Gmail/provider acceptance.
6. **Consolidate alternate orchestration surfaces.** Trace and converge voice, workflow automation, legacy task engine, and coding SDK adapters without deleting working modules.
7. **Connect and verify remaining capabilities.** Test WhatsApp, calling, YouTube/device, files, calendar, and browser paths against configured providers and permissions.
8. **Expand scheduled/event-driven work.** Add multi-worker scheduler/restart tests, notifications, cancellation, and user-defined autonomy only after the shared lifecycle is reliable.

## 10. Testing and acceptance plan

### Eight-organization Vox Kenya outreach acceptance test

Use the eight organizations named in the request and a deterministic fake search/page provider with separate per-organization source evidence. Assert:

1. One parent task is created with eight organization records and explicit research dependencies.
2. Every organization has a source URL, extracted address, and verification status; a missing/ambiguous source is never guessed and requires a user decision.
3. Exactly eight recipient-specific drafts are produced; no combined recipient list is sent.
4. The first approval card shows organization, recipient, source, verification, full subject/body and attachments.
5. Editing recipient/subject/body and adding/removing attachments updates the durable draft before approval.
6. No send occurs before explicit approval. One approval results in at most one send; the next organization is shown only after the current decision is recorded.
7. Approve, edit, revise, skip, reject, save, stop, resume, and re-open after backend restart each retain the exact queue position and outcomes.
8. Provider receipt/message ID is recorded. Simulated timeout after provider acceptance produces `outcome_unknown`; reconciliation prevents a duplicate.
9. After eight decisions, the parent task has a truthful terminal/partial state and per-organization audit history.
10. Repeat with a real Gmail sandbox only after mock acceptance passes; do not use live nonprofit addresses for automated test sends.

### Other tests

- Single-recipient regression coverage in `test_phase_e_email.py` passes with the related task and safety suites.
- Latest focused backend runs: `test_approvals.py` — **11 passed**; `test_phase_e_email.py` — **41 passed**; `test_workflows.py` — **16 passed**; `test_email_inbound.py` — **1 passed**. The complete backend suite passed with **1,612 passed, 7 skipped, 14 warnings** before the inbound-email trust-boundary change; a full rerun is pending. Tasks Playwright — **7 passed**, including edit/save, actual local API attachment upload/fetch, mocked approval/send, skip, and uncertain-send reconciliation. All Chromium E2E — **40 passed**; frontend production build and isolated Alembic upgrade to head also passed.
- Continue adding coverage for queue revisions, zero/ambiguous provider reconciliation, and source mismatch.
- Email: Gmail mock HTTP responses, provider failure/timeout, edit races, duplicate approval requests, attachment MIME/size/ownership, standalone/chat parity.
- WhatsApp/calls: mock provider and webhook signature tests; require approval; distinguish queued provider receipt from confirmed result.
- Browser/research: SSRF/URL-safety, untrusted prompt-injection content, official-source mismatch, missing source and search timeout.
- Files/documents: DOCX/XLSX/PDF generation and validation, user ownership, invalid/missing dependencies, restart artifact access.
- Calendar/scheduler: permission policy, persistent schedule reload, notification and approval-required schedule, cancellation and repeated fire.
- Voice/device: confirmation policy parity with chat, target-device consent, expired/replayed pairing, and honest action verification.
- Cross-cutting: task graph dependency order, failed-step recovery, cancellation, database restart, per-user isolation, audit completeness, and status consistency between API and UI.

## Security and user-control observations

- **Present:** Chat tools are selected from the registry, arguments are schema-validated, and the graph applies `policy.decide`. `send_email` and structured user input are forced to approval in the graph. Approval claims are atomic. Email attachment ownership/size/MIME are checked. Local Windows apps are allowlisted; remote URL actions require sender and target approval. Web/page results and retrieved document text have explicit untrusted-content instructions.
- **Incomplete/inconsistent:** The email reply-draft endpoint already tells its system prompt to treat the quoted message as untrusted. The central chat prompt now marks search/read/thread/unread email tool results as untrusted; inbound email summaries pass the message separately as human content and prohibit following embedded instructions. Focused regressions cover both boundaries. Auto-replies are deterministic templates, not model-generated, and still use the send policy. Voice loads the user's autonomy/tool-policy snapshot and calls the shared `policy.decide`; voice and saved workflows share schema validation through `services/tool_preflight.py`, while `services/tool_execution.py` provides common invocation/result normalization to voice, saved workflows, and Copilot workspace tools, with graph result decoding using the same contract. These entry points still retain separate model/task lifecycles: voice has its own model/tool loop, saved workflows retain a sequential executor, and workspace adapters have their own external-agent loop. Public-contact page evidence is tied to the planned send dependency but does not authenticate page ownership. Approval outcomes still need the same audit contract across standalone and graph paths.
- **Credentials:** Provider settings are handled via configuration/credential services; this audit did not read `.env` files or expose credentials. No secrets are included in this report.
- **User control:** Generic stop/pause/resume and approval/reject exist. Queue-level draft editing, save-without-send, reject, skip, and approve/send now use the same durable approval/run lifecycle. Distinct revision/revoke semantics and background-run parity remain open.

## Validation performed

| Command | Result |
|---|---|
| `python -m pytest -q` | 1,632 passed, 4 skipped, 14 warnings after shared invocation normalization, uncertain workflow outcome persistence, and approved mock-email recovery across a backend process restart |
| `python -m pytest tests -q` | 1,624 passed, 7 skipped, 14 warnings on 2026-11-09 after shared graph/voice/workflow result decoding, durable approval recovery, external coding-agent policy enforcement, and Copilot shared argument preflight |
| `python -m pytest tests/test_agentic_hardening.py tests/test_workflows.py tests/test_voice_live.py -q` | 74 passed; structured result models, mappings, and JSON decode consistently, and voice rejects unstructured output |
| `python -m pytest tests/test_workflows.py tests/test_agentic_hardening.py tests/test_voice_live.py tests/test_workspace_projects.py -q` | 101 passed before the latest Copilot argument-preflight regression; current workspace suite passes 26 tests with invalid arguments rejected before SDK tool execution |
| `python -m pytest tests/test_workspace_projects.py -q` | 26 passed after policy filtering, Codex fail-closed behavior, the service-level confirmation contract, and malformed-output rejection |
| `python -m pytest tests/test_tool_preflight.py tests/test_approvals.py tests/test_fix_repair.py::TestEmailApprovalDraftIntegrity::test_edit_pending_email_and_approve_uses_edited_version -q` | 17 passed after excluding omitted Pydantic defaults from normalized values |
| `python -m pytest tests/test_capability_acceptance.py tests/test_tool_preflight.py tests/test_workflows.py tests/test_voice_live.py -q` | 66 passed after graph, voice, and saved workflows were switched to shared argument-schema validation |
| `python -m pytest tests/test_creative.py -q` | 1 passed; the API test now stubs the AI-edit planner and unavailable image provider instead of loading a locally cached diffusion pipeline |
| `python -m pytest tests/test_phase_d_image_generation.py::TestLocalImageProvider tests/test_phase_d_image_generation.py::TestImageQuality -q` | 18 passed; procedural-path tests force the procedural provider and AI-path tests use a mocked pipeline, independent of installed model weights |
| `python -m pytest tests/test_phase_e_email.py -q` | 41 passed, including exact, zero, ambiguous, and out-of-window Gmail reconciliation cases |
| `python -m pytest tests/test_approvals.py -q` | 11 passed, including fresh-process email reconstruction/skip and structured-input/batch-email edit/approve with missing serialized run state |
| `python -m pytest tests/test_workflows.py -q` | 21 passed after cooperative cancellation support, including rejection of unregistered workflow capabilities, malformed output, persisted uncertain exception/timeout/cancellation outcomes, and user-facing retry warning |
| `python -m pytest tests/test_agentic_hardening.py tests/test_workflows.py tests/test_voice_live.py tests/test_workspace_projects.py -q` | 101 passed after shared invocation was wired into workflows, voice, and Copilot workspace tools; helper tests cover valid, malformed, exception, and timeout results. The later workflow outcome-unknown behavior is covered by the final full-suite run and 19-test workflow suite. |
| `npm run build` | Passed on 2026-11-09; existing dynamic-import and large-chunk warnings remain |
| `npm run test:e2e -- e2e/tasks.spec.ts` | 7 passed on 2026-11-09 against isolated local backend/frontend servers on ports 8362/5518; task list, controls, outreach queue, reconciliation, approval editing, and skip behaviors rendered and worked. Playwright now binds Vite to IPv4 to match its `127.0.0.1` base URL. |
| `python -m pytest tests/test_approvals.py -q` | 12 passed, including post-process-restart approval of a pending mock email and persistence of the provider receipt |
| `npm run test:e2e -- --project=chromium --reporter=list e2e/tasks.spec.ts` | 7 passed against the isolated localhost backend/frontend on ports 8366/5522 after shared invocation and saved-workflow outcome handling changes; isolated E2E data was removed afterward. |
| `npm run test:e2e -- --project=chromium --reporter=list` | 40 passed in the final isolated localhost run on ports 8371/5527 after rerunning the full suite without the concurrent backend test load; isolated E2E data was removed afterward. |
| `npm run test:e2e -- --project=chromium --reporter=list` | 40 passed against isolated local servers using managed Playwright Chromium; API proxy matched the backend port |
| Authenticated shared localhost chat smoke test | The final graph build answered `What is 2 + 2?` with `Answer: 4` through `ollama:qwen2.5:1.5b`; an earlier smoke prompt also received its exact requested token. No external side effect was requested. |
| Alembic isolated SQLite upgrade/current check | Reached `v1f_approval_plan_step` head |
| `git diff --check` on the task-scoped changes | Passed for this implementation slice; unrelated pre-existing whitespace warnings remain elsewhere in the working tree |

**Remaining verification boundary:** The localhost task suite covers the authenticated task/approval queue with mocked provider behavior, not live delivery. No live email provider send/reconciliation, physical-device action, or full cross-entrypoint task lifecycle was completed. Real provider/device acceptance remains external and unverified.
