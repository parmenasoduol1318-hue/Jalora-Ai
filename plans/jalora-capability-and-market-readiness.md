# JALORA Capability and Market-Readiness Assessment

**Assessment date:** 9 November 2026  
**Project assessed:** this checkout of JALORA  
**Purpose:** describe what the current application can do, what is partial or unsupported, and what should be built to compete as both a capable AI chatbot and a reliable autonomous agent.

**Implementation-plan reference:** Future implementation follows the numbered
[JALORA Master Implementation Roadmap](./JALORA-MASTER-IMPLEMENTATION-ROADMAP.md);
this assessment remains the capability baseline, not a second execution plan.

## Executive summary

JALORA is a substantial, locally runnable AI assistant application with a React web interface, FastAPI backend, a central chat/agent path, a registry of tools, persistent conversations and task/approval records, and integrations for research, email, messaging, calendars, files, voice, and selected device operations.

It is **not yet a fully autonomous, cross-device AI operator**. The strongest evidence is for local chat, the central graph's tool execution, persistent task/approval UI, and a mocked eight-organization email approval workflow. Real provider behavior, source authenticity, broad Windows/Android control, and one consistent task lifecycle across chat, voice, saved workflows, and external coding-agent adapters have not been established.

The right product strategy is to improve the existing central agent and its shared execution infrastructure—not to create a separate AI agent for every feature. Treat provider/device integrations as capability modules, and make every user-visible claim distinguish between *prepared*, *approved*, *submitted*, and *verified*.

## How to read the status labels

- **Fully implemented in a bounded scope:** the feature exists end to end within its stated limits and has local test or smoke-test evidence. This does not mean every provider, account, machine, or use case is supported.
- **Partially implemented:** useful code exists, but important workflow, integration, recovery, platform, or verification pieces are missing.
- **Cannot currently do / unsupported:** the inspected application does not implement the capability, or it is restricted by the operating system/provider and no supported implementation is present.
- **Provider/device dependent:** source code may exist, but real operation requires credentials, account access, network, OS permissions, installed software, or hardware. Do not equate a mock test with live operation.

## I. What JALORA can do fully today (within verified boundaries)

“Fully” here means the bounded local product behavior below is implemented and has evidence. It does not certify production-grade reliability or every external integration.

### 1. Chat through the primary web application

- Accept a user's text in the React web app and submit it to the FastAPI chat endpoint.
- Route requests through the central chat service and LangGraph-based agent path.
- Use a selected model/provider when configured; a prior authenticated shared-localhost smoke test completed a simple arithmetic request using the configured local Ollama model.
- Return an assistant response and persist conversation messages.
- Stream or render task, run, tool, approval, and artifact information through the existing UI paths.

**Evidence:** `backend/app/api/chat.py`, `backend/app/services/chat_service.py`, `backend/app/agents/graph.py`, `frontend-react/src/`.

**Boundary:** Natural-language understanding and tool planning remain model-dependent. A successful response to a simple prompt does not prove broad reasoning accuracy, uptime, or correct execution of every complex request.

### 2. Bounded central tool planning and execution

- Discover available tools from a shared registry and expose an appropriate subset to the central agent.
- Validate plans, tool names, argument schemas, and dependency ordering in the central graph.
- Execute registered tools, process structured results, record outcomes, and prevent malformed tool output from being treated as success.
- Apply central policy/approval handling on the chat execution path.
- Use a shared tool-preflight and invocation/result-decoding helper in the graph and selected secondary paths.

**Evidence:** `backend/app/agents/graph.py`, `backend/app/tools/base.py`, `backend/app/tools/__init__.py`, `backend/app/services/tool_preflight.py`, `backend/app/services/tool_execution.py`.

**Boundary:** Tool availability is not universal or dynamically discovered from every connected app/device. The model still proposes plans and calls; the system is not a deterministic compiler for arbitrary multi-step instructions.

### 3. Persistent conversations, task visibility, and approval interaction

- Store chat conversations/messages and show task status, plan/progress, and approval states in the web interface.
- Persist tasks, runs, steps, and approval records in the database.
- Present pending actions for explicit approval or rejection; support task cancellation and shared task controls for supported states.
- Reconstruct the outreach queue from the task plan, run/messages, and approval records when serialized run state is missing, for tested email cases.
- Store provider receipts and keep uncertain send outcomes distinct from confirmed success in supported email flows.

**Evidence:** `backend/app/models/`, `backend/app/api/tasks.py`, `backend/app/api/runs.py`, `backend/app/services/chat_service.py`, `frontend-react/src/views/TasksView.tsx`.

**Boundary:** Persistence and recovery are not uniform for every tool or execution entry point. The separate `TaskEngine` is not wired as the canonical production executor; broader recovery after restart remains unverified.

### 4. Tested sequential email outreach controls—using mocked organizations/providers

- Generate a separate planned email send for each organization in the tested eight-recipient scenario.
- Gate each send on an individual approval; process the queue sequentially.
- Skip a recipient without sending and continue to the next item.
- Edit/save a pending draft, attach/remove files through the queue flow, and explicitly approve or reject.
- Record mock-provider message receipts in the tested approval flow.
- Block an outreach recipient unless the exact address appears in a successful opened-page result linked to that send's plan dependency.
- Also require a conservative organization-name token match in page evidence; expose address presence and organization-name match separately in the queue.
- Keep uncertain send outcomes visible and provide a Gmail-specific reconciliation path that does not retry when status is unresolved.

**Evidence:** `backend/tests/test_approvals.py`, `backend/tests/test_agentic_hardening.py`, `backend/app/agents/graph.py`, `backend/app/api/tasks.py`, `frontend-react/src/views/TasksView.tsx`, `frontend-react/e2e/tasks.spec.ts`.

**Boundary:** Tests use fabricated `.example.org` data and `email_mock`. Name-token matching does not authenticate official-site ownership; retrieved page content is untrusted. No live Gmail delivery or real-world recipient verification is implied.

### 5. User-scoped memory and conversation history (for the central chat path)

- Persist conversations and expose history.
- Store and retrieve user-scoped long-term memory with semantic/lexical retrieval and sensitive-content filters.
- Expose memory controls for listing/deleting stored entries.

**Evidence:** `backend/app/models/conversation.py`, `backend/app/models/memory.py`, `backend/app/services/memory_service.py`, `backend/app/api/conversations.py`.

**Boundary:** Authorized, consistent context sharing across voice, external coding adapters, scheduled runs, and every capability is not established. Retention, provenance, export, and complete vector-index deletion require further verification.

## II. What JALORA can do partially

| Area | What exists now | What keeps it partial |
|---|---|---|
| General chatbot quality | Central chat service, model routing, conversation storage, tool-use, and a React interface. | No benchmark evidence for answer quality, citations, hallucination rate, latency, multilingual coverage, or production-scale reliability. Model quality and availability depend on configuration/provider. |
| Complex task planning | The central graph supports structured plans, dependencies, registered tools, progress, and approvals. | Model-generated plans can omit tasks, misorder steps, or misinterpret conditions. The graph is not the single lifecycle for all entry points. |
| Web research | Search, page opening/extraction, source references, browser session tools, URL safety, and untrusted-result handling. | Search provider configuration and quality are external. Search snippets/pages do not prove source identity. The organization identity check is a heuristic, not official-domain verification or independent corroboration. |
| Email | Email search/read/thread operations, drafts, single/batch send paths, Gmail integration path, mock provider, explicit approval, attachments, receipt storage, and a Gmail Sent reconciliation route. | Live Gmail credentials, scope, quota, network, reconciliation, and delivery are untested. Provider coverage is incomplete. Mock draft persistence is limited. Approval/recovery parity between standalone email and chat is incomplete. |
| WhatsApp/SMS | Messaging send tools, mock/Twilio/Telnyx paths, webhook endpoints, and approval gates for supported sends. | No verified comprehensive personal WhatsApp inbox/history/group search, summaries, unread-state management, or personal account automation. Provider and official API/account policy restrictions apply. |
| Contacts and calling | User-local contacts, phone resolution, call initiation paths, call/task UI, and telephony provider bridges. | No verified universal call history, OS contacts access, or end-to-end live call completion/verification. Call lifecycle differs across paths and relies on provider credentials/webhooks. |
| Voice | Speech transcription/live voice endpoints, voice session, model routing, audio/TTS providers, and a voice confirmation flow. | Voice retains a separate model/tool loop and session lifecycle. It does not yet consistently create/resume the same durable Task/AgentRun/Approval lifecycle as chat. Real microphone, speaker, STT/TTS, and telephony behavior depends on environment/provider. |
| Browser automation | Playwright-based isolated browser sessions, page browsing, and guarded URL operations. | It is not equivalent to controlling the user's existing browser profile or native applications. Reliability depends on browser/runtime installation and page changes. |
| Windows local automation | Allowlisted local app operations such as inspect, launch, close, and supported UI interaction behind user consent. | Limited to the backend host, configured/allowlisted apps, and supported UI/accessibility controls. It is not general unrestricted device control and is not remote PC control. |
| Paired browser companion | Pairing/approval and limited scoped browser commands/message intake exist. | Pairing does not grant remote screen view, native app inventory, filesystem access, arbitrary input, or whole-device permission. |
| Files and documents | Upload/search/query and user-owned document handling; PDF/DOCX/XLSX/CSV generation paths; media analysis tools. | General filesystem search/organization, Recycle Bin operations, large-file cleanup, and verified end-to-end generation quality are not established across environments. Libraries and file permissions matter. |
| Calendar, reminders, scheduling | Calendar tools and APIs, persistent schedule records, scheduler, and notifications. | External calendar OAuth and live provider behavior are unverified. Multi-worker exactly-once execution, timezone/recurrence edge cases, restart behavior, and approval parity need stronger acceptance tests. |
| External coding agents | Copilot/Codex workspace adapters with some shared policy, tool argument validation, and external-agent confirmation. | They retain separate SDK model/tool loops and are not fully integrated with the durable central task/approval lifecycle or per-file preview/approval. SDK/provider availability and credentials are external. |
| Background autonomy | Persisted schedules can dispatch work through the chat service and surface notifications. | No general durable distributed worker/event bus, broad event subscriptions, always-on monitoring, or proven exactly-once side-effect semantics. |
| Security and user control | User authentication, scoped data, policy levels, confirmation/approval, URL safety, owner-scoped attachments, and some prompt-injection defenses. | Enforcement and audit behavior are not identical on every direct API, secondary agent, scheduled path, and provider. A source/name match is not source authenticity. Revocation and device-scope consent are incomplete. |
| Operations and deployment | FastAPI/React app, scripts, Docker configuration, database migrations, logs/metrics/tracing modules, backend tests, and Playwright E2E. | No evidence here of a production-grade installer/update channel, tested backup/restore/rollback, multi-tenant operations, production database scaling, SLOs, on-call, or release security. |

## III. What JALORA cannot currently do (or must not claim it can do)

These are absences or unsupported promises in the inspected project—not statements about what could never be built.

### Autonomous, cross-capability workflows

- Guarantee correct completion of an arbitrary natural-language task without user oversight.
- Use one shared planner, durable task state, approval model, and recovery engine for chat, voice, saved workflows, direct email, schedules, and external coding agents.
- Guarantee safe automatic retries for every external side effect or provide exactly-once delivery across providers.
- Resume every task after a restart from a verified checkpoint; current robust restart evidence is concentrated on email approval cases.
- Maintain a general persistent per-organization outreach ledger with a distinct revision history and complete recovery for every decision type.

### Organization and web verification

- Prove that a public contact page/domain is officially owned by the named organization from a name-token match alone.
- Guarantee that a listed email address is current, monitored, belongs to the intended organization, or will reach a decision-maker.
- Reliably research every organization when search results are missing, ambiguous, blocked, stale, or contradictory; those cases need explicit user-resolution behavior.
- Treat retrieved webpages, emails, documents, or tool outputs as trusted instructions. They must remain untrusted data and never authorize actions.

### Communications, calls, and media

- Search/summarize a user's full WhatsApp history or control all personal WhatsApp features through a verified supported integration.
- Guarantee a message was delivered/read just because an API accepted it; provider receipts can mean only queued/submitted.
- Access universal phone call history or initiate/verify all calls on arbitrary Android/iOS devices.
- Search YouTube for the correct requested video, control playback, and verify it started in a supported, tested workflow. The existing `open_youtube` behavior opens the YouTube home page and is not proof of playback.

### Device and operating-system automation

- Remotely view/control an arbitrary paired Windows PC or Android phone with a native companion.
- Discover all installed applications on a target device and grant per-app or whole-device access through a supported native permission flow.
- Empty Recycle Bin/Trash, find/delete arbitrary large videos, or control every installed app as a verified end-to-end capability.
- Operate arbitrary apps on iOS, macOS, or Linux. No companion implementation was found; OS policy and permissions impose additional constraints.

### Production readiness and external services

- Claim live Gmail, WhatsApp, Twilio, Telnyx, Google Calendar, external search, or other provider success without credentials and a real acceptance test.
- Claim that a mock-provider test proves real delivery, account access, compliance, or production uptime.
- Claim that a frontend/build/test pass proves the application is production-secure or scales to market-level traffic.

## Current architecture: strengths and structural debt

### Strengths to preserve

- A real central chat agent and shared capability registry already exist.
- Capabilities are modules/tools rather than all being separate independent agents.
- Existing approval, task, run, audit, schedule, and integration models are valuable and should be reused.
- The React interface already has separate views for chat, tasks, email, calendar, documents, memory, schedules, people, devices/integrations, and activity.
- Recent work has improved shared tool validation/result handling and safe reporting of uncertain outcomes.

### Convergence work required

| Existing path | Problem | Recommended treatment |
|---|---|---|
| Chat service + LangGraph | Most complete planning/execution path. | Keep as the canonical central agent runtime; make its task/run contract explicit and stable. |
| `TaskEngine` | A second state machine/handler registry exists; no production call site was found in the prior source audit. | Do not delete without a deployment check. Either integrate its useful handlers behind registered capabilities or retire it through a separately tested migration. |
| `WorkflowEngine` | Separate linear execution and history; approval-required tools cannot use the same pause/resume lifecycle. | Preserve saved workflow definitions, but dispatch their steps through the central executor and shared approval/recovery rules. |
| Voice session | Separate model/tool loop and lifecycle despite shared validation/policy pieces. | Keep audio/STT/TTS transport; route interpreted tasks and actions to the central task/approval runtime. |
| Standalone email APIs | Email-specific approval/send result handling overlaps generic run approval. | Keep the email-specific UI/API, but centralize permission decisions, idempotency, claims, receipts, and uncertain-outcome handling. |
| Copilot/Codex adapters | External SDK loops and filesystem effects have a separate lifecycle. | Keep as optional providers/capabilities; require preview/approval and write their task/run/audit outcomes into the shared lifecycle. |
| Direct domain APIs | Some actions bypass the graph/task lifecycle when initiated directly from the UI. | Preserve fast, useful UI operations while using the same policy, audit, and normalized result contracts. |

**Key decision:** the product should have **one central agent**, one planner/orchestrator and one shared execution/approval lifecycle. Email, voice, browser, files, calendar, WhatsApp, calls, and device companions should expose tools/adapters—not autonomous peer agents with duplicated memory and policy.

## What to add and improve to compete in the AI market

### P0 — Trustworthy task completion (highest priority)

1. **Canonical task contract:** every task has stable task/run/step IDs, typed inputs/outputs, dependencies, timestamps, state, evidence, approval references, retry policy, and cancellation state.
2. **Durable execution and recovery:** checkpoint before/after side effects; use idempotency keys; reconcile provider state before retrying; resume only when state is sufficient. Add restart/failure-injection tests for all action classes.
3. **One policy/approval service:** centralize permissions for chat, voice, workflows, direct APIs, schedules, and coding adapters. Support approval scopes, expiration, revocation, per-tool autonomy, and visible audit history.
4. **Outcome truthfulness:** distinguish planned, drafted, awaiting approval, submitted, provider-accepted, delivered (when known), verified, failed, and unknown. Never report success from a tool call alone.
5. **Task controls and observability:** consistent pause/resume/stop, retries with explanation, current step, blockers, evidence, and notification behavior on every surface.
6. **Prompt-injection defense:** treat retrieved data as untrusted across all channels; isolate instructions from data; require user approval for side effects regardless of retrieved content; add adversarial tests.

### P1 — Make core assistant and agent behavior dependable

1. Build a benchmark and regression dataset for core user intents, task decomposition, tool selection, argument correctness, refusal, and recovery; track success rate and regressions by model.
2. Add structured planner validation, step-level completion predicates, dependency checks, and explicit clarification/blocked states for missing information.
3. Add source citations and provenance to research outputs; prefer official sources; cross-check identity and surface confidence/ambiguity. Never turn heuristic matching into an “officially verified” badge.
4. Add human-readable plans and editable task previews before high-impact actions.
5. Add robust cross-surface context management: user-approved memory, source provenance, retention controls, deletion/export, and explicit context boundaries between users/tasks.
6. Add provider contract tests, rate-limit/backoff policies, circuit breakers, webhook signature checks, and operational dashboards.

### P2 — Close the highest-value integration gaps

1. Complete email inbox/thread workflows, attachment summarization, contact verification, draft editing, approval, safe send, and provider reconciliation for supported providers.
2. Decide product scope for WhatsApp: official Business API only versus personal account use; implement only supported official integrations and accurately label unavailable history features.
3. Complete contacts/call task flows using provider capabilities and permissioned mobile/OS access; verify queued, ringing, connected, and ended states separately.
4. Add YouTube/media search, identity matching, playback controls, and player-state verification; expose only capabilities supported by tested interfaces.
5. Improve document workflows with quality checks, citations, editable previews, ownership-safe attachments, and generated-file validation.
6. Finish calendar/scheduling with timezone/recurrence correctness, duplicate-fire prevention, approval-aware jobs, multi-worker behavior, and restart/recovery tests.

### P3 — Device and platform expansion

1. Define a native companion threat model and protocol before broad device automation.
2. Build Windows and Android companions that report an explicit capability inventory, app identity, version, OS grants, target identity, health, and revocation state.
3. Implement least-privilege app/device permissions, time-limited consent, visible action preview, target-side confirmation, and auditable results.
4. Add screenshot/accessibility information and action verification only within platform permissions; do not promise arbitrary control.
5. Add signed installation, secure update/rollback, compatibility checks, device pairing recovery, and a lab acceptance matrix.
6. Treat iOS/macOS/Linux separately; do not imply Windows/Android support generalizes to those operating systems.

### P4 — Market and product quality

- **Differentiation:** reliable human-in-the-loop workflows, transparent evidence, and a privacy-forward personal/business assistant are stronger differentiators than claiming unlimited autonomy.
- **User experience:** provide a task inbox, approvals center, evidence/source panel, saved task templates, interruption recovery, and clear action history.
- **Customization:** allow user-defined autonomy levels and reusable workflow templates without allowing templates to bypass policy.
- **Accessibility/localization:** test keyboard/screen-reader usability, low-bandwidth behavior, language quality, and voice alternatives.
- **Privacy controls:** explain what data goes to each provider; offer retention controls, export/deletion, and per-integration permissions.
- **Reliability:** publish a capability matrix by provider/OS/version and measurable service objectives; track task completion, safe-abort, duplicate-action, and recovery rates.
- **Commercial readiness:** add installation/onboarding, secure credential management, support/diagnostics, usage/cost limits, tenant isolation, billing only if needed, and clear terms around external-provider limitations.
- **Evaluation:** compare with market alternatives on measurable end-to-end scenarios, not feature-count claims. Run blinded evaluations for answer quality, latency, citation accuracy, tool success, and human correction effort.

## Recommended implementation order

1. Freeze a source-backed capability inventory and make this report the baseline.
2. Define the shared task/run/step/result/approval contract and pick the canonical engine (the existing central graph is the strongest candidate).
3. Converge voice, saved workflows, direct email, scheduler, and coding adapters incrementally—one path at a time—with parity tests.
4. Complete safe side-effect semantics: idempotency, uncertain-outcome reconciliation, durable audit, cancellation races, and process-restart recovery.
5. Complete high-value web/email/communications workflows and validate with safe sandboxes, not real unsolicited recipients.
6. Upgrade research evidence and source identity; ask the user when evidence is ambiguous.
7. Improve answer/agent benchmarks, observability, security testing, deployment, and backup/restore.
8. Start native Windows/Android companion work after permission and protocol design; test on actual supported devices.
9. Expand capabilities only through the shared registry and lifecycle; do not add another planner per feature.

## Verification snapshot and caveats

The source-backed audit and preceding implementation work recorded:

- Full backend test run: **1,632 passed, 4 skipped** (14 warnings).
- Full isolated Chromium browser run: **40 passed**.
- Frontend production build: passed, with existing dynamic-import and large-chunk warnings.
- Most recent focused outreach regressions: **38 agentic-hardening tests passed**, **12 approval/recovery tests passed**, the eight-organization sequential approval test passed, and the focused outreach queue browser test passed.
- Ruff was not available via the active Python environment during the most recent focused check; changed backend modules compiled successfully.

These tests establish behavior for the tested checkout and mock/local environment. They do **not** establish live provider delivery, verified official organization domains, real-device automation, production readiness, or complete cross-entrypoint task convergence. Provider credentials and secrets were not inspected for this report.

## Key source and audit references

- Detailed source audit: [jalora-capability-audit.md](./jalora-capability-audit.md)
- Product/device requirements and platform matrix: [full-device-automation-plan.md](./full-device-automation-plan.md)
- Central graph: `backend/app/agents/graph.py`
- Chat orchestration: `backend/app/services/chat_service.py`
- Tool registry: `backend/app/tools/base.py`, `backend/app/tools/__init__.py`
- Task and approval APIs: `backend/app/api/tasks.py`, `backend/app/api/runs.py`
- Voice loop: `backend/app/services/voice_session.py`
- Saved workflow executor: `backend/app/services/workflow_automation.py`
- Task engine: `backend/app/services/task_engine.py`
- Device API and UI: `backend/app/api/devices.py`, `frontend-react/src/components/RemoteDeviceCompanion.tsx`
- Main task UI: `frontend-react/src/views/TasksView.tsx`

## Bottom line

JALORA already has the foundation of a useful AI chatbot and a bounded autonomous agent. It can converse, plan and invoke registered capabilities on the central path, persist tasks and approvals, and run a tested mocked sequential outreach workflow. Its strongest gap is not a lack of feature modules; it is **uneven reliability, verification, and lifecycle integration across those modules**.

To compete credibly, prioritize dependable completion, clear evidence, safe recovery, provider-specific integrations, privacy, and measurable evaluations. Market it today as a capable assistant with selected approval-gated actions—not as a fully autonomous agent that can control every app, device, and account.
