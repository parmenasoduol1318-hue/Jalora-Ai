# Jalora Full-Device Automation Plan

> **Subplan:** Device automation is governed by numbered Stage 12 of the
> [JALORA Master Implementation Roadmap](./JALORA-MASTER-IMPLEMENTATION-ROADMAP.md).
> This file supplies detailed device requirements; it does not supersede the
> master execution order or assert that native companions are implemented.

## Purpose

Deliver consent-based, remote and local device automation so a user can tell
Jalora what to do on a selected computer or phone, see what Jalora sees, review
important actions, and verify what actually happened.

This plan is the target product, not a claim that the current browser companion
already has these capabilities. Browser pairing today authorizes approved
public-link opening only. It does not expose a screen, installed-app inventory,
native apps, files, or device-wide input.

## Product vision and core design rule

Jalora is one multimodal personal and business AI operator, not a collection of
separate bots for messaging, email, calling, media, files, or individual apps.
One agent runtime uses a shared planner, task state, user-authorized memory,
permission and policy engine, audit trail, and reusable tools. Integration
adapters expose capabilities to that shared agent; they do not create their own
independent planner, memory, or approval model.

The agent should progress from answering requests to completing authorized
digital work across applications and devices. It may operate in chat, voice,
scheduled, or event-triggered contexts, but every entry point dispatches into
the same capability-driven planning and execution system. Platform companions
provide device capabilities; application integrations provide service
capabilities. Neither is a separate task bot.

## Product requirements

1. Make **this device** and **another paired device** visibly separate choices.
   The selected target must always be named in the task and execution view.
2. Provide an expandable, searchable inventory of apps on a target laptop or
   phone. Users can select individual apps or choose **Entire device**.
3. Show what each permission enables before requesting the operating-system
   grant. Pairing alone is never treated as screen, input, filesystem, or
   messaging authorization.
4. Support remote screen viewing, accessibility-based app understanding,
   verified actions, app launch/close where the OS allows it, and task
   checkpoints, retry, pause, cancellation, and revocation.
5. Accept natural-language instructions through chat or voice, including
    follow-ups, and execute multi-application and multi-device goals through one
    shared agent. Derive plans at runtime from the user's objective, live device
    state, connected integrations, and registered capabilities. Do not implement
    separate service bots, hardcoded app-name branches, or canned workflows.
6. Support personal and business work across communications, calendar, contacts,
    calls, files, browser/media, and user-selected apps. Examples include:
    - WhatsApp unread/message/group search, summaries, attention/deadline
      extraction, reply drafts, follow-up suggestions, and policy-gated replies.
    - Thread-aware email search and classification for school, work, personal,
      financial, promotions, and urgent correspondence; extract deadlines,
      summarize attachments, identify messages needing a response, and draft
      replies.
    - Contact/call-history lookup, missed/recent-call summaries, ambiguity
      handling, and user-confirmed call initiation where supported.
    - Search and control media using the requested title/creator rather than
      blindly selecting the first result.
    - Persistent authorized tasks, schedules, event triggers, notifications, and
      bounded follow-up work when the user is not actively chatting.
    - Editable long-term preferences and project/routine memory, with user
      inspection and deletion controls.
7. Support the requested workflows as named, testable products—not as a promise
   that a model can operate every third-party app:
   - Open and empty Recycle Bin / Trash.
   - Find videos above a user-selected size (the initial requested example is
     300 MB), show a review list, and delete only after confirmation.
   - Open YouTube, search for a specified music track, play it, and verify the
     player state where available.
   - Find and launch a selected installed game.
   - WhatsApp: inspect permitted conversations, mark messages unread where
     exposed by supported APIs/UI, summarize group messages, and draft suggested
     replies.
   - TikTok/Instagram: search a specified video/account; following an account
     requires preview and confirmation.
   - Search and navigate a user-selected law app to a requested topic or
     chapter; never fabricate app contents.
8. Clearly report per-step outcomes: completed and verified, completed but
   unverified, blocked by a missing permission, or failed. Never turn a queued
   command or accepted click into a success claim.

## Product model and permission UX

### Device and app picker

Add a first-class **Devices & access** view. Each device card identifies its
role and status, for example **This laptop (Windows, companion running)** or
**Parmenas's phone (Android, last seen ...)**. Do not infer the target from the
browser hosting Jalora.

On a selected device, an expandable app picker must:

- Fetch the installed-app inventory from the native companion, not from the
  Jalora web server or browser.
- Search and group apps by installed, recently used, and system apps where the
  OS makes those categories available.
- Show app name, icon when available, publisher/package identity, and whether
  Jalora currently has OS permission to inspect/control it.
- Allow checking individual apps, reviewing the selected set, and saving or
  revoking that grant.
- Include a distinct **Entire device** option. Explain that it requests the
  broadest permissions supported by that platform; list every OS permission and
  capability that will be enabled. It must not be a hidden blanket grant or
  silently exceed OS capabilities.
- Keep target-device approval explicit even when pairing was initiated by a
  different signed-in Jalora session.

The task composer must require or clearly display its target (this device,
named paired device, or ask each time). Before execution, show the selected app
scope or **Entire device** scope and provide a stop/revoke control.

### Permission tiers and action policy

Use separate, revocable grants for:

- Device pairing and authenticated command transport.
- App inventory.
- Screen capture / screen stream.
- Accessibility-tree or UI Automation inspection.
- Mouse, keyboard, touch, and app launch/close.
- File search and file deletion.
- Notifications and message content.
- Message sending, social actions, and calling.
- Microphone input and voice activation.

Provide a persistent companion indicator when screen or input access is active,
with target name, current task, pause/stop, and revoke controls. Log the grant,
each action, each confirmation, and the result. Store only the screen/action
data needed for the task; do not retain screen recordings by default.

Risk controls:

- Read-only discovery and summaries can run within an explicit user grant.
- Opening an app, navigation, and reversible UI changes follow the user's
  configured approval policy and are individually observable.
- Sending messages, following accounts, deleting files, emptying Trash, making
  purchases, or initiating calls requires an exact preview and per-action
  confirmation by default. A future unattended mode may relax only actions
  that are explicitly allowlisted, bounded, and reversible; destructive or
  external-facing actions remain confirmed.
- For deletion, present exact files, sizes, paths, count, and total bytes, then
  require a separate confirmation. Prefer moving to Recycle Bin/Trash over
  permanent deletion; emptying it requires a second, explicit confirmation.
- Never infer approval from pairing, a previous task, app selection, a voice
  transcript, or an ambiguous phrase.

## Target architecture

### 1. Native device companions

Build a signed, resident companion per supported OS. The companion owns local
device permissions and performs actions only on its own device. The React web
app is the control surface; the FastAPI backend coordinates authenticated
commands, task state, and audit records. A remote Jalora session must not run
desktop input on the backend host and misrepresent it as input on the target.

Companion responsibilities:

- Pairing approval, device identity, per-capability grants, secure token
  storage, heartbeat, and immediate revocation.
- App inventory and OS permission status.
- Screen/accessibility capture only while authorized and visibly active.
- Single-step action execution with target/window validation and post-action
  evidence.
- Capability reporting so Jalora can refuse unsupported tasks before claiming
  it can do them.

### 2. Remote transport and screen session

- Replace browser-only polling as the device-control transport with an
  authenticated, reconnecting native-companion channel (WebSocket or equivalent)
  for commands, status, cancellation, and results.
- Use WebRTC or an equivalent encrypted, low-latency channel for remote screen
  streaming where the platform supports it. Use short-lived session credentials,
  device-bound authorization, and authenticated signaling.
- Keep media peer-to-peer when feasible; if relay is required, use an
  authenticated encrypted relay and document what metadata is retained.
- Do not send screen frames to an AI/model provider by default. If a cloud model
  needs an image, obtain a distinct disclosure/consent and allow a local OCR or
  accessibility-only path where available.
- Enforce expiry, replay protection, per-device request limits, origin checks,
  and command ownership. No public unauthenticated control endpoint.

### 3. Screen understanding and action loop

Use the strongest available signal in this order:

1. Native accessibility/UI Automation tree with stable app/window/control
   identifiers and labels.
2. Screenshot plus on-device OCR and visual grounding when accessibility data
   is insufficient.
3. User-visible coordinate action only as a last resort, with the current
   frame, target app, and proposed click shown for confirmation.

Every action follows:

`observe -> identify target -> propose one bounded action -> authorize if
needed -> execute once -> observe again -> verify expected state -> checkpoint`

Reject stale screen coordinates, ambiguous control matches, unexpected window
changes, focus changes, privilege mismatches, and actions outside the granted
app/device scope. Do not replay an action whose delivery/result is uncertain;
observe first to prevent duplicates.

### 4. Multi-step task interpretation and orchestration

The user may describe a goal that requires several app or system operations.
The planner must turn that request into a task-specific dependency graph at run
time; it must not rely on app-name special cases, fixed app-pair examples, or
hardcoded action sequences.

- Resolve the target device, selected app scope or **Entire device** grant, user
  goal, requested order, preconditions, and completion conditions from the
  current request, live capability inventory, and fresh device observations.
- Preserve an explicitly stated order. Add dependency edges when a later step
  requires an earlier step's result or state change. When the request does not
  define a necessary order, choose a safe order from the dependencies and
  disclose it in the plan before execution.
- Show the user an editable, ordered preview for multi-step tasks, including
  target device, app/process targets, required permissions, expected state
  changes, approval points, and verification signals. Ask for clarification
  before acting if targets or ordering are materially ambiguous.
- Execute dependent steps sequentially. Run steps in parallel only when the
  planner can establish they are independent, operate on separate state, and
  parallel execution does not conflict with the user's requested order.
- After each step, re-observe and check its postcondition before dispatching a
  dependent step. Persist the plan, step status, evidence, and checkpoint so
  pause, cancellation, or reconnection cannot silently skip or duplicate work.
- If a step fails, becomes unverified, or its preconditions changed, stop its
  dependent steps. Explain the blocker and offer a revised plan; obtain approval
  before materially changing the requested order, target, or side effects.
- On resume, rebuild the plan from its persisted state plus a fresh observation
  of the target. Re-check grants and completed-step postconditions; never replay
  an uncertain external or destructive action without first determining whether
  it already occurred.
- Distinguish graceful close requests from confirmed process exit. Do not claim
  that an app is closed, a task is complete, or a later step ran unless the
  target companion supplies evidence for the corresponding postcondition.

This orchestration layer is app-agnostic: the same planning and execution
behavior applies to user-selected apps discovered on the device, subject to
their available controls and permissions. App-specific workflows may add
capabilities and verification rules, but must not replace the generic planner
with hardcoded sequences.

### 5. Task planner and completion evidence

Represent each task as typed steps with target device, app/file scope, required
capability, dependencies, risk, approval state, retry policy, and a measurable
completion predicate. Persist checkpoints and evidence references. A task may
continue after reconnect only when its permissions remain valid and the current
screen can be re-observed.

Examples of completion predicates:

- App launch: target companion reports the requested app process/window active.
- Screen navigation: target app exposes the requested page/topic, verified by
  accessibility label or visible text.
- Playback: media player reports playing and the selected title matches (where
  the target app exposes this state); otherwise report that playback is
  unverified.
- File deletion: each selected path is absent or present in Trash as specified;
  never infer completion from an accepted delete click.
- Message reply: show the exact recipient and message; confirm send separately;
  report provider/app delivery evidence or explicitly say delivery is unknown.

For a multi-step task, the overall result is complete only if every required
step's completion predicate is satisfied. Otherwise return partial or blocked,
list the exact unfinished steps, preserve completed work, and do not imply that
the requested sequence finished.

## Unified agent architecture and reusable modules

All interfaces and integrations enter one agent runtime. Adapters are modular
for implementation and testing, but do not become independent agents with
competing memory, planners, or permission rules.

1. **Interaction gateway:** chat, push-to-talk/voice, task launcher, schedules,
   event triggers, and companion-originated events normalize into a typed user
   goal with user, conversation, target device, and authorization context.
2. **Shared agent runtime:** maintains the current task and conversation
   context, resolves follow-ups, handles clarifying questions, and coordinates
   the complete task lifecycle.
3. **Capability registry:** each reusable tool declares its input/output schema,
   target requirements, data sensitivity, side effects, platform availability,
   required permission scopes, idempotency behavior, verification method, and
   failure modes. The planner selects from this live registry rather than
   hardcoding service-specific routes.
4. **Shared planner/executor:** creates an ordered dependency graph, validates
   preconditions and policies, pauses for approvals, dispatches bounded actions,
   records evidence, verifies postconditions, and handles recovery or
   cancellation. App-specific workflows are tool compositions and verification
   profiles, not their own bots.
5. **Policy/permission engine:** resolves user, app, device, action, data, and
   context grants. The same engine authorizes API tools, local companion
   commands, background jobs, and voice actions. External-facing and destructive
   operations cannot bypass the common approval mechanism.
6. **Memory and task store:** separates short-lived conversation/task context
   from durable user-approved preferences, routines, contacts, and project
   facts. Sensitive content and credentials are not placed in general memory.
   Every stored item has provenance, retention, visibility, and deletion rules.
7. **Integration and companion adapters:** service adapters use official
   provider APIs where available; native companions expose device-specific
   capabilities through the same registry and execution protocol.
8. **Event/background scheduler:** enqueues scheduled and subscribed work into
   the same task executor, applies the same grants and policies, and reports
   results through notifications and task history. It is not a second
   autonomous agent.
9. **Audit/observability:** durable task and action records capture who
   requested an action, target, tool, approval, evidence/result, and policy
   decision. Secret values, raw credentials, and unnecessary screen/message
   content are redacted.

### Functional module breakdown

| Module | Shared-agent responsibilities | Reusable capabilities and boundaries |
|---|---|---|
| Planning and execution | Intent understanding, clarification, dependencies, checkpoints, retries, safe concurrency, completion summaries | Generic tool selection and execution; no app-specific bot |
| Device access | Pairing, device selection, inventory, screen/accessibility session, launch/input/files, revocation | Native companion API with per-platform capability declarations |
| Messaging | WhatsApp/SMS and other supported channels; contact and group identity; unread, search, summarize, classify, draft, follow-up | Read/draft/send are distinct scopes; send obeys policy and delivery verification |
| Email | Gmail, Outlook, and other authorized providers; thread-aware search/read, classification, attachment extraction, labels, drafts, send | Provider adapters normalize to shared message/thread types and shared approval |
| Contacts and calls | Authorized contact/call history, identity resolution, call initiation/state | Confirm ambiguous identities and calls; OS/provider limits apply |
| Browser and media | Search/navigation, media discovery/control, browser app automation | Verify title/creator/state; never choose a search result blindly |
| Files and documents | Scoped file discovery, metadata/content read, document understanding, safe file operations | User-selected roots, preview, recoverable delete by default, stronger approval for permanent deletion |
| Memory and personalization | Preferences, relationships, routines, projects, relevant history | User inspect/edit/delete; provenance and retention; no passwords or unnecessary message archives |
| Background operations | Scheduled jobs, event subscriptions, queues, reminders, proactive notifications | Explicit enablement, visible schedule, bounded monitoring, pause and revoke |
| Voice and multimodal I/O | Speech recognition, spoken response, interruption, context continuation, image/screen understanding | Push-to-talk first; separate opt-in for wake-word and any cloud screen processing |
| Policy and audit | Common approvals, action risk, budgets, logs, emergency stop | Same rules across all modules and execution modes |

### Shared data and capability contracts

- Normalize communications into common message, thread, participant, source,
  timestamp, attachment, sensitivity, and delivery-status shapes while
  retaining provider IDs and provenance.
- Keep provider-specific fields in adapter-owned metadata, not in duplicated
  agent logic.
- Represent each task with a stable task ID, goal, target(s), plan version,
  steps, dependencies, required grants, approvals, checkpoints, artifacts,
  evidence, retries, and terminal status.
- Give each action a unique idempotency key and explicit retry classification.
  Reads may be retried within bounds; uncertain sends, calls, follows, and
  deletes must be reconciled before another attempt.
- Declare source trust. Emails, web pages, documents, notifications, and
  messages are untrusted task data, never user authorization or policy changes.
- Store identity and summaries with source references so the user can inspect
  where an extracted deadline, contact, or suggested action came from.

## Existing repository architecture and communication flow

This section maps the target architecture to code that exists in this checkout.
It is a source inventory, not a claim that every route or provider has passed a
live test. The status matrix below distinguishes code presence from verified
runtime behavior.

### Current request and execution path

```text
React SPA (frontend-react/src)
  ├─ Vite dev server :5173 ── /api proxy + WebSocket ─────────┐
  └─ production bundle served by FastAPI (frontend-react/dist)│
                                                             ▼
FastAPI (backend/app/main.py; routers in backend/app/api/)
  ├─ authenticate user / resolve conversation and target
  ├─ chat/task request -> services/chat_service.py
  │    ├─ conversation history + relevant long-term memory
  │    ├─ agents/graph.py (LangGraph) <-> services/model_router.py
  │    ├─ tools/base.py registry -> registered capability adapter
  │    ├─ shared policy + approval pause/resume
  │    └─ verify tool results and persist run/task/action evidence
  ├─ domain endpoints -> existing provider/service adapters
  ├─ browser tools -> services/browser_driver.py (Playwright or fake)
  ├─ local Windows app API -> devices.py / pywinauto on backend host
  ├─ browser companion -> scoped device pairing and approved URL opening
  └─ schedules -> APScheduler -> services/chat_service.py for agent runs
          │
          ▼
SQLAlchemy models + Alembic migrations (SQLite default; optional Redis)
```

### Component responsibilities and existing touchpoints

| Component | Current repository responsibility and communication | Target change or boundary |
|---|---|---|
| Frontend | `frontend-react/src/App.tsx`, `views/`, `components/`, and `lib/api.ts` manage UI state and send authenticated HTTP/JSON requests. `frontend-react/vite.config.ts` proxies `/api` and WebSockets to FastAPI on port 8000. `backend/app/main.py` serves the React `dist` build when present. | The browser collects intent, selected target/scope, confirmations, and renders evidence/status. It must not hold provider secrets, choose security policy, or claim a remote action succeeded based on a click response. Treat `frontend/` and the README's vanilla-JS description as legacy until usage is confirmed; do not build a second SPA. |
| FastAPI/API boundary | `backend/app/main.py` builds the app and lifespan; `backend/app/api/__init__.py` mounts routers including chat, tasks, memory, documents, devices, integrations, email, calls, schedules, voice, people, workspace, and creative. Pydantic schemas define request/response payloads. | Keep authorization, validation, durable state, and execution server-side. Add endpoints only when the current router/service contract cannot support the feature; version companion-facing contracts. |
| Central agent / model routing | `backend/app/services/chat_service.py` handles a chat execution; `backend/app/agents/graph.py` runs the LangGraph tool loop and uses `backend/app/services/model_router.py`. Intent classification is in `backend/app/agents/intent.py`. | This is the natural-language orchestration path to preserve. Map voice, task-launcher, scheduled, webhook, and future companion inputs to a shared task envelope instead of introducing a bot per channel. Model/provider fallback is not proof that an external action completed. |
| Capability registry | `backend/app/tools/base.py` holds `ToolSpec`, permission/risk metadata, schemas, and request-aware `select_for_task`; `backend/app/tools/__init__.py` imports/registers built-in tool modules and `lazy_loader.py` defers selected imports. `backend/app/services/integration_registry.py` reports provider status. | Registration currently comes from application code; it is not live discovery of installed apps or OS permissions. Extend capability metadata for target, scope, platform, verification, idempotency, and availability. Keep integration registry distinct from callable tool registry, but expose a normalized capability view to the planner. |
| Planning and execution | `agents/graph.py` holds plan/tool execution behavior; `backend/app/services/task_engine.py` provides a persisted task state machine; `backend/app/services/workflow_automation.py` and saved-workflow APIs provide another structured execution path. `Task`, `TaskStep`, and `AgentRun` persist goals/plans/run state. | These are existing overlapping paths, not a reason to create a fourth orchestrator. Phase 1 must trace callers and tests, define the single canonical task/step contract, then converge or explicitly delegate to one shared executor. Preserve the existing users' saved workflows during migration. |
| Short-term conversation context | `backend/app/models/conversation.py` persists `Conversation` and ordered `Message` rows; `/api/conversations/*` manages history. `chat_service.py` builds the model context. | Keep message history user- and conversation-scoped; define bounded context selection and truncation separately from persistent memory. |
| Long-term memory | `backend/app/models/memory.py`, `backend/app/services/memory_service.py`, `embeddings.py`, and `vector_store.py` implement user-scoped memory rows, embeddings/retrieval, a sensitive-content filter, and delete/list controls through `/api/memory`. | Memory exists; provenance, consent semantics, retention/export, correction workflows, and reliable deletion across any external index need acceptance tests before it is treated as complete. Do not copy full private conversations into memory by default. |
| Database and persistent task state | `backend/app/database/session.py` creates the SQLAlchemy engine and runs Alembic migrations on app startup; `backend/alembic/` contains the migration history. SQLite is the settings default. `Task`, `TaskStep`, `AgentRun`, `Approval`, `WorkflowExecutionRecord`, and `Schedule` persist distinct workflow records. | Back up before restore/migration. `init_db()` stamps an existing database without `alembic_version` at head; inspect and rehearse this path on a copy before opening a migrated user DB. Use migrations for schema changes; do not add another task database. |
| Permission and security | `backend/app/tools/base.py` defines AUTO/NOTIFY/CONFIRM/STRICT and side-effect metadata; `backend/app/services/policy.py`, approval models/endpoints, auth dependencies, and audit/activity services apply policy to current server tools. Device pairing has hashed tokens and explicit target approval. | The current device scopes cover browser opening and messaging-related capabilities, not native screen/input/file access. Reuse the common policy decision for every new companion/background action and bind it to user + device + app scope + capability. |
| Browser automation | `backend/app/tools/browser.py`, `browse.py`, and `backend/app/services/browser.py`, `browser_session.py`, `browser_driver.py` provide controlled Playwright sessions plus a fake driver used by tests. Python Playwright is in `backend/requirements.txt`; UI E2E uses `@playwright/test`. | Browser automation is not screen access to the user's existing browser profile or native-device control. Isolate browser sessions and credentials; verify navigation/action postconditions and handle browser install/channel availability explicitly. |
| Device companions and Windows control | `backend/app/api/devices.py`, `models/device.py`, migrations `v1b`-`v1e`, and `frontend-react/src/components/RemoteDeviceCompanion.tsx` implement pairing and scoped browser commands. `/api/devices/local/*` exposes registered executable launch/close/inspect/input on the same Windows machine hosting the backend, subject to loopback and approval checks. `pywinauto` is a backend dependency. | There is no native Windows or Android companion, remote screen stream, target app inventory, or **Entire device** grant. The existing browser token/connection must not be reinterpreted as native access. |
| Voice | `backend/app/api/voice.py` exposes transcription and `/api/voice/live`; `backend/app/services/voice_session.py` handles the live session, with browser and optional local/cloud STT/TTS providers. `backend/app/api/calls.py` and `comms.py` bridge configured Telnyx/Twilio flows. `frontend-react/src/` contains voice-capable task/chat views. | Voice code exists but some engines/providers are optional and live telephony needs external configuration. The live session has its own current tool/session path; verify and converge it on the shared task policy/executor instead of assuming every voice action follows the chat planner. No always-listening client is established. |
| External services | `integrations.py`, `emails.py`, `calls.py`, `comms.py`, `people.py`, and `services/oauth.py`, `email.py`, `calendar.py`, `drive.py` (plus tool adapters) hold provider flows. The registry includes mock/local/provider-dependent availability. | Credentials, scopes, quotas, APIs, provider receipts, and account eligibility determine actual behavior. A mock/fallback is for development, not proof of real delivery. |
| Background scheduler and queue | `backend/app/services/scheduler.py` uses APScheduler with a SQLAlchemy job store and calls `chat_service.execute_chat` for scheduled agent tasks; schedules and notifications have API/model/UI support. Redis is present in Compose and used by rate limiting. | A separate durable, distributed task queue/worker is not established by this code inventory. Scheduler jobs and their task effects need restart, duplicate-fire, concurrency, and outcome-reconciliation tests. Do not describe Redis as a task queue unless a queue producer/worker is implemented and verified. |

### End-to-end contracts to converge on

1. The frontend submits a typed task request: authenticated user, conversation,
   selected target device (or explicit local-host target), allowed app/device
   scope, natural-language goal, and any user-specified ordering. It renders a
   server-returned preview and collects approvals; it never broadens scope.
2. FastAPI authenticates the user, loads only that user's conversation and
   approved memory, resolves the named target and live capability inventory,
   then calls the central agent with a normalized request. A missing target,
   permission, capability, or required input produces a clarification or
   blocked result before side effects.
3. The planner selects only registered capabilities whose schemas, target,
   permissions, and current availability match. It creates ordered steps and
   dependency edges, records each step before dispatch, and obtains policy
   approval for every required action. Independent work may run concurrently
   only when it cannot violate explicit order or shared-state safety.
4. An API/provider adapter or authenticated companion receives one bounded
   action with a stable task/step/idempotency identifier and exact scope. The
   target companion—not the web server—checks its OS grant, active user consent,
   target identity, and current foreground app before acting.
5. The adapter returns a typed result plus provider receipt or target evidence.
   The executor checks the declared completion predicate, stores a checkpoint,
   and only then releases dependent work. On timeout it observes/reconciles
   current state before retrying any non-idempotent side effect.
6. The same task status and evidence feed chat/voice response, Tasks/history,
   approval UI, notifications, and audit logs. Background/scheduled entry points
   use the same path and policy; they differ only in trigger and notification.

### Current API and transport seams

These are existing seams to preserve or converge; they do not imply that
unimplemented companion capabilities are already available.

| Existing path | Current purpose | Planned use / boundary |
|---|---|---|
| `POST /api/chat` and chat streaming | Submit a user turn to the backend agent | Keep as a thin adapter to the common task runtime; return run/task IDs, plan, approval state, and final evidence/status. |
| `/api/conversations/*` | Persist and retrieve conversation/message history | Provide short-term context only; enforce conversation ownership and bounded model context. |
| `GET /api/tasks`, `GET /api/tasks/{task_id}`, `POST /api/tasks/{task_id}/cancel` | List, inspect, and cancel persisted tasks | Extend the existing status/step response rather than create a separate task-history API. Add pause/resume only through the durable run contract. |
| `/api/runs`, approval APIs, `/api/activity` | Run state, explicit human decisions, action audit | All entry points and adapters use the same task/run and approval identifiers. |
| `/api/memory/*` | User-scoped long-term memory CRUD/search | Retrieval joins the task context only when enabled and relevant; this API is not the conversation transcript. |
| `/api/integrations/*`, `/api/emails/*`, `/api/calls/*`, `/api/comms/*` | Provider connection and domain operations | Provider adapters return normalized results/receipts into the shared task executor. |
| `/api/schedules/*`, `/api/notifications/*` | Schedule lifecycle and user notifications | A trigger creates a normal task; notifications link back to its task/run and evidence. |
| `/api/devices/pair-requests/*`, `/api/devices/remote-commands/*` | Target-approved pairing and browser command lifecycle | Currently browser/message scopes only. Version the contract before adding a native command or screen-stream channel. |
| `/api/devices/local/*` | Same-host Windows allowlist and bounded UI operations | Keep explicitly local. Do not send remote-target actions to these routes. |
| WebSocket `/api/voice/live` | Live in-app audio/control session | Normalize recognized utterances into the shared task envelope; voice confirmation must call the common approval mechanism. |

## Platform capability and permission matrix

The matrix is a planning baseline, not a blanket promise. Each release must
replace broad platform labels with tested OS versions, companion versions, and
exact capability results from that target device.

| Capability | Windows | Android | macOS | Linux | iOS |
|---|---|---|---|---|---|
| Pairing and authenticated task channel | Native signed companion required for remote target; current backend-host controls are not remote | Native companion and target approval | Native signed companion and target approval | Native companion, desktop/session-specific | Pairing possible; app background and control limits apply |
| Installed-app inventory | Companion enumerates supported installed apps; picker is future work | Package visibility restrictions; user-mediated inventory | System/API restrictions and user consent | Desktop/environment-specific | No general third-party inventory/control guarantee |
| Remote screen | Companion plus OS-approved capture and visible session | MediaProjection/user prompt; persistent foreground indicator | Screen Recording permission | Portals/session dependent; Wayland restrictions | Arbitrary third-party screen streaming/control is generally unavailable |
| Accessibility and input | UI Automation, integrity-level and foreground restrictions | User-enabled Accessibility Service; Play/distribution policy constraints | Accessibility/Automation consent; app-specific limits | AT-SPI/portal/window-manager dependent | No general cross-app accessibility control |
| File search/delete | User-scoped filesystem permissions; preview/revalidate; Trash preferred | Scoped storage and user-mediated access | Files & Folders permission and protected locations | User/session filesystem permissions | Sandboxed app access; user-mediated document picker |
| Notifications/messages | Notification/platform integrations only when supported | Notification access is separate; messaging app/API restrictions | Notification access and app/API restrictions | Desktop notification provider dependent | Restricted background access and app sandbox |
| Calls/call history | Telephony/provider only where available | Sensitive runtime permissions and distribution-policy review | Not a general phone call-log platform | Telephony stack dependent | Restricted call handling; CallKit/approved integration requirements |
| Background operation | Signed service/tray app, user-visible state, Windows lifecycle | Foreground service, OS limits and notification | Launch agent/background-task constraints | Service/session policy dependent | Strict background execution limits |

Before enabling a capability, the companion must report actual support and
permission state. If the OS or app does not expose a capability, the shared
agent must state the limitation and offer a supported alternative rather than
claiming full access.

## Platform-specific implementation and acceptance gates

The master implementation order is specified after these platform and workflow
gates. These sections define the required technical content and acceptance
conditions; they do not prescribe a separate bot architecture or override that
master dependency order.

### Gate — Product scope and capability contract

- Inventory existing device APIs, Windows controls, browser companion, task
  approvals, and app integrations.
- Define a versioned capability contract for inventory, screen read, app launch,
  UI inspection, input, filesystem, notifications, and external side effects.
- Establish threat model, data retention, audit requirements, supported OS
  versions, signing/release, and recovery behavior.
- Freeze UI vocabulary for selected apps versus **Entire device** and distinguish
  target device from backend host.

**Exit gate:** documented protocol and permission matrix; each advertised
capability maps to a specific native permission and test. The task schema
supports ordered steps, dependencies, verification predicates, and safe
recovery without embedding app-specific sequences.

### Gate — Device picker, consent, and revocation UX

- Build device cards, live/offline state, expandable app list, per-app selection,
  **Entire device** choice, permission explanation, and review screen.
- Pair by short-lived one-time code/QR, approve on the target, and display the
  requested Jalora account and exact grants.
- Add pause, disconnect, revoke, lost-device handling, and access audit history.
- Keep current browser pairing visibly labeled browser-only until a native
  companion is installed and approved.

**Exit gate:** tests prove that no app inventory, screen, action, or filesystem
capability is available before the target grants the corresponding OS permission;
revocation rejects the next command immediately.

### Gate — Windows native companion and remote desktop session

- Ship a signed Windows tray companion that starts explicitly, pairs with the
  selected Jalora account, and runs on the target Windows laptop (not whichever
  computer hosts the backend).
- Enumerate installed applications using supported Windows APIs; implement
  expandable selection, pinned/recent apps, and a clearly scoped **Entire
  device** grant.
- Use Windows UI Automation first and an OS-approved screen-capture mechanism
  for a visible, revocable remote screen session.
- Launch selected apps, inspect accessible controls, send guarded single-step
  input, and close only processes/windows that the companion is authorized to
  manage. Do not force-kill or discard unsaved data.
- Implement authenticated command channel, reconnect, task cancellation, status,
  and auditable results.

**Exit gate:** remote second-machine acceptance test proves pairing, target
identity, screen visibility, app launch, one verified interaction, graceful
close, interruption/reconnect, stop, and revoke end to end.
An additional multi-step acceptance suite uses different synthetic app names
and task graphs to prove the planner follows user-specified order, validates
each intermediate state before continuing, blocks dependent work after a
failure, and resumes without duplicate actions.

### Gate — Android native companion

- Add a native Android app with target approval, a foreground service and
  persistent notification while active.
- Use Android's user-enabled Accessibility Service only after a plain-language
  OS-mediated enablement flow; use MediaProjection only after its screen-capture
  prompt. Request notification access separately.
- Expose app inventory subject to Android package-visibility rules, accessibility
  tree, screen stream, and restricted actions. Keep an app allowlist and an
  explicit **Entire device** grant tied to actual OS toggles.
- Provide easy disable/revoke, persistent active indicator, and emergency stop.

**Exit gate:** a physical-device test verifies prompt-by-prompt consent,
background/foreground transitions, screen and app controls, force-stop/revoke,
and no action after permission revocation.

### Gate — macOS and Linux companions

- macOS: signed companion; use only user-granted Accessibility, Screen
  Recording, Automation, and Files & Folders permissions; explain each System
  Settings prompt and detect revocation.
- Linux: establish a supported distribution/session baseline; use AT-SPI and
  desktop portals where available; do not assume Wayland permits unrestricted
  input or screenshots.
- Maintain platform-specific capability matrices; offer only controls that the
  current session and OS actually permit.

**Exit gate:** per-platform acceptance tests and UI state exact permission gaps;
unsupported capabilities fail explicitly rather than falling back to unsafe
global input.

### Gate — Safe system/file workflows

- App launch/close and searchable inventory on each supported companion.
- Recycle Bin/Trash inventory and preview; empty only after the separate
  destructive-action confirmation.
- Large-file scan with user-selected roots, threshold, extension/filter controls,
  exact path/size list, exclusion of OS/app-critical locations, and
  recoverable-delete default.
- Measure scan limits, progress, cancellation, permission errors, and changes
  between preview and deletion; revalidate each path immediately before acting.

**Exit gate:** synthetic files at boundary sizes (below/equal/above 300 MB),
permission-denied directories, symlink/path traversal cases, cancellation, and
recoverable-delete verification all pass without touching unrelated data.

### Gate — App workflow adapters

Implement one workflow at a time behind an explicit capability declaration:

1. YouTube search/play: user provides track; search and present the exact result;
   user confirms if needed; verify player/title when observable.
2. Game launch: select from installed inventory; launch and verify process/window.
3. WhatsApp read-only: only through official supported APIs or authorized
   on-device UI; limit to user-selected chats; summarize and draft, not send.
4. WhatsApp mark-unread: validate OS/app UI behavior; require preview and
   confirmation; report whether unread state was verified.
5. WhatsApp send: editable draft, exact recipient/chat review, send confirmation,
   delivery evidence; avoid automatic repeat on uncertain results.
6. TikTok/Instagram search: use app-supported search and report the matched
   account/video. Follow requires exact profile preview and one explicit confirm.
7. Law-app search/navigation: user selects the app and jurisdiction/source;
   search available accessible content, show source/title/chapter, and distinguish
   app text from Jalora-generated explanation.

Do not bypass app authentication, CAPTCHAs, platform restrictions, or user
controls. Prefer documented official APIs; where no API is available, use
user-authorized accessibility automation that respects the app's terms and
requires action-specific approval.

**Exit gate:** each adapter has happy-path, ambiguous-result, app-version-change,
offline, permission-revocation, duplicate-prevention, and final-state tests plus
a real-device demonstration.

### Gate — Voice and bounded unattended operation

- Support push-to-talk first on every platform; always-listening activation is a
  separate opt-in with a visible indicator and platform-specific background
  limitations.
- Let users preview/edit recognized text and identify the target device before
  starting.
- Add user-defined policies per action type, app, recipient, time window, and
  spend/file bounds. Default to review for external-facing or destructive
  actions.
- Keep stop, pause, status, and revoke accessible while a task is running.
- Add crash recovery and resumption only from a fresh screen observation and a
  still-valid consent grant.

**Exit gate:** voice false-positive, cancellation, interrupted-network, stale
state, duplicate action, and scheduled/unattended policy tests pass.
Multi-step tests vary app labels and operation combinations so passing behavior
cannot depend on any one hardcoded example.

### Gate — Reliability, privacy, and release

- Threat-model review and independent security/privacy review of pairing,
  command transport, screen streaming, tokens, local storage, and audit logs.
- Signed update/release path, least privilege, secret/token rotation, tamper
  detection, rate limits, and secure diagnostics.
- Device/app compatibility telemetry with explicit opt-in and redaction.
- Measure task completion rate by app/OS, verification rate, median/p95 action
  latency, duplicate rate, recovery rate, revocation latency, and permission
  failure reasons.
- Release only capabilities that pass live target-device tests; stage rollout
  and provide a fast disable switch.

**Exit gate:** published support matrix, passing CI and real-device test matrix,
documented privacy behavior, rollback plan, and no known critical/high security
findings.

## Master implementation roadmap

This is the implementation order for the existing repository, not a greenfield
rewrite. The platform-specific gates above remain the detailed safety and
device-acceptance requirements; phases below reference them rather than
duplicating their protocols. Each phase states the project touchpoints, expected
change type, dependencies, contract, implementation, security, tests, result,
and exit gate. “Existing files” are evidence-based starting points, not a
requirement to change every listed file.

### First working MVP release definition

The MVP is the existing React/FastAPI product made reproducible on the new
machine, with one shared task lifecycle and a small set of demonstrated
capabilities. It includes chat/conversation context, registered-tool planning
and execution, approval and permission checks, persisted task status/history,
user-controlled memory, browser automation, current-host Windows app controls,
basic document/file operations, and voice only when its configured input/output
engines pass the same acceptance tests. It must build and run, expose honest
progress and errors, and complete at least one approved multi-step task in the
shared web UI with evidence for each step.

The MVP does **not** include remote native Windows/Android screen access,
universal app control, personal WhatsApp history automation, arbitrary
unattended monitoring, or live calls/messages without configured providers and
delivery evidence. Those remain later phases; a mock response does not satisfy
the MVP's live-integration acceptance.

### Phase 0 — Recover the development environment on the new Windows machine

**Objective:** establish a reproducible local baseline before feature work,
without copying a virtual environment or silently losing user data.

**Existing files:** `README.md`, `backend/requirements.txt`,
`backend/.env.example`, `backend/app/config.py`, `backend/app/database/session.py`,
`backend/alembic.ini`, `backend/alembic/`, `frontend-react/package.json`,
`frontend-react/package-lock.json`, `frontend-react/vite.config.ts`,
`frontend-react/playwright.config.ts`, `Dockerfile`, and `docker-compose.yml`.

**New files:** none initially. Only add a Python-version/dependency manifest or
split optional extras if the environment recovery exposes a concrete need;
do not add a second requirements system pre-emptively.

**Dependencies and contracts:** inspect installed interpreters with `py -0p`.
The repository does not currently declare `requires-python`; the GitHub Copilot
SDK requirement is conditional on Python 3.11+, while the complete pinned set
and optional ML/browser packages must be resolved on the selected runtime.
Use Python 3.12 as a provisional development baseline, then record it only after
the full Windows dependency and test checks pass. The app reads `JALORA_*` from
the project-root `.env` (not `backend/.env`). Local storage defaults to
`data/jalora.db`; SQLite needs no server. Redis is present in Compose for
optional rate limiting; PostgreSQL is commented out and its driver is not in
the current requirements.

**Implementation sequence:**

1. Preserve the existing checkout and data; record `git status` and do not copy
   the old `.venv`. Confirm the project root and available Python/Node versions.
2. Create a fresh root `.venv`, upgrade pip, and install
   `backend/requirements.txt`. Resolve install failures against the actual
   manifest and platform; do not silently omit pinned dependencies. Test
   optional local model/image packages separately if they cannot be installed
   on the chosen Windows/Python combination.

   From the Jalora project root, the initial commands should be equivalent to:

   ```powershell
   py -0p
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install --upgrade pip
   .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
   .\.venv\Scripts\python.exe -m pip check
   Push-Location frontend-react
   npm ci
   npm run build
   Pop-Location
   Push-Location backend
   ..\.venv\Scripts\python.exe -m pytest tests -q
   Pop-Location
   ```

   `3.12` is provisional; substitute only after confirming the installed
   interpreter and resolving the full pinned dependency set. The launcher
   accepts `JALORA_E2E_PYTHON` or `PYTHON` and otherwise uses `python`.
3. Run `npm ci` in `frontend-react/` from the checked-in lockfile. Install and
   verify Playwright browser dependencies. `playwright.config.ts` now defaults
   to managed Chromium; set `JALORA_E2E_BROWSER_CHANNEL=chrome` or `msedge` to
   use an installed browser channel. The complete E2E suite passed using
   managed Chromium on this checkout.
4. Create local configuration from `backend/.env.example` into the project
   root `.env`; rotate/regenerate development secrets and add real provider
   credentials only from a secure source. Verify setting names without printing
   secret values. Enable only the providers/services needed for the smoke test.
5. Before launching against copied `data/`, make a protected backup. Inspect
   the DB's Alembic revision, check the migration graph, rehearse
   `alembic upgrade head` on a copy, and verify user, task, integration, and
   uploaded-file counts. `init_db()` stamps an existing database lacking
   `alembic_version` as current head; never use that legacy adoption path on a
   valuable database without proving its schema is actually compatible.
6. Start the selected local model service only if used: install/verify Ollama,
   run `ollama list`, pull the configured models deliberately, and exercise the
   actual model router. Cloud providers must use test accounts/keys. Verify
   Tesseract separately for OCR and Chrome/Edge/Playwright for browser tasks.

**Security:** never transfer `.env`, tokens, browser profiles, or copied
credentials as ordinary project files. Use a password manager/secret store,
restrict `.env` permissions, ensure it remains ignored by Git, encrypt DB
backups, and use test-only JWT/provider credentials. Do not paste secrets into
logs or issue reports.

**Tests and expected result:** `python -m pip check`; migration upgrade on a
copy; `python -m pytest backend/tests -q` from the correct working directory;
`npm run build`; Playwright smoke/E2E after removing the absolute interpreter
dependency; launch FastAPI and verify `/api/health`; launch Vite on 5173 and
verify its `/api` proxy reaches port 8000. Run provider/model checks only for
services selected in config. Expected result is a reproducible fresh
environment, intact data, and a short record of exact interpreter, browser,
database revision, local services, and any optional features unavailable.

**Acceptance / exit gate:** backend test collection and results, frontend
TypeScript/Vite build, migration check, API health, and browser smoke are
recorded on the new machine. Any failure is classified and fixed or explicitly
blocks the next phase; no claim of a passing suite based only on README text.

### Phase 1 — Reconcile repository state, current behavior, and test evidence

**Objective:** make the codebase and its documentation agree, and turn the
current implementation into a testable baseline before changing architecture.

**Existing files:** `README.md`, `docs/device-companion.md`,
`backend/app/main.py`, `backend/app/api/__init__.py`,
`backend/app/config.py`, `frontend-react/src/App.tsx`,
`frontend-react/package.json`, all `backend/tests/`, and
`frontend-react/e2e/`.

**New files:** no new subsystem. If a capability inventory is kept in source
control, update this plan or the existing README rather than creating duplicate
architecture documents.

**Dependencies and contracts:** Phase 0. The running app serves
`frontend-react/dist`; Vite uses `frontend-react/` on port 5173 and proxies API
and WebSocket traffic. The legacy `frontend/` directory and README statements
about vanilla JavaScript/no build step do not match `main.py`'s current React
build path and must be classified before removal or further development.
The README's Quickstart creates `.venv` at the Jalora root, while its test
command refers to `..\..\.venv` from `backend/`; reconcile that path along with
the absolute interpreter path in `frontend-react/playwright.config.ts`.

**Implementation sequence:** trace each router/view to its service and
registered tools; map chat, tasks, workflows, memory, schedules, integrations,
browser, Windows-local controls, and voice entry paths. Reconcile documentation
with actual endpoints, environment settings, test fixtures, and mock/provider
fallbacks. Establish the exact test collection count and separate unit,
integration, E2E, provider-mock, and real-device coverage. The README says 37
backend tests while the current tree contains 82 `test_*.py` files; treat that
count/status as stale until test collection is run.

**Security:** preserve all user changes and credentials during this audit; do
not include private `.env`, local DB, uploads, or browser data in diagnostics.
Tests must use isolated databases, fake providers, and non-production accounts.

**Tests and expected result:** Phase 0 test baseline; exercise representative
auth, chat/tool call, approval, memory, task cancel/resume, schedule, integration,
device-pairing, and browser tests. Compare source behavior to README and
`docs/device-companion.md`. Expected result is a source-backed matrix of
implemented, partial, mock-only, provider-dependent, unverified, and absent
capabilities plus the known failing tests—not a feature wishlist.

**Acceptance / exit gate:** one reviewed API/feature inventory, reproducible
test commands and results, documented environment/test blockers, and corrected
entry-point documentation. No feature marked “verified” without a passing test
or an observed end-to-end demonstration.

### Phase 2 — Converge on one shared task and execution contract

**Objective:** strengthen the central Jalora agent and remove orchestration
ambiguity without creating another planner.

**Existing files:** `backend/app/agents/graph.py`,
`backend/app/agents/intent.py`, `backend/app/services/chat_service.py`,
`backend/app/services/task_engine.py`,
`backend/app/services/workflow_automation.py`,
`backend/app/tools/base.py`, `backend/app/tools/plan.py`,
`backend/app/models/task.py`, `backend/app/models/agent.py`,
`backend/app/models/workflows.py`, `backend/app/schemas/__init__.py`,
`backend/app/api/chat.py`, `backend/app/api/tasks.py`, `backend/app/api/runs.py`,
and the related agent/task/workflow tests.

**New files:** `backend/app/services/tool_preflight.py` provides the shared
capability-argument validation boundary, reused by voice and saved workflows.
`backend/app/services/tool_execution.py` decodes structured results consistently
for the graph and provides a shared invocation boundary for voice, saved
workflows, and Copilot workspace tools. The shared boundary normalizes timeout,
exception, and malformed-result failures; each caller retains its existing
authorization, approval, context, and lifecycle behavior. These helpers are
deliberately not a replacement executor. Continue to prefer
the existing task/run/step schema and executor. A schema migration is appropriate
only for a demonstrated missing field, with an Alembic revision and compatibility
tests.

**Dependencies and contracts:** Phase 1. Normalize chat, Agent Tasks, voice,
scheduled requests, webhooks, and future companion requests into one task
envelope: user, conversation, goal, target, granted scope, plan version, steps,
dependencies, risk, approval, idempotency key, evidence, and terminal status.
Define one typed step/result contract before changing callers.

**Implementation sequence:** trace actual call paths and choose the existing
shared runtime/executor boundary; preserve LangGraph's model/tool loop and
approval checkpointing; route other interfaces through that boundary; keep
saved workflows as data-driven compositions, not a separate autonomy engine.
The first convergence slice now shares capability-argument schema validation
between the central graph, voice, and saved workflows; the central graph now
executes the normalized arguments returned by validation, matching the behavior
already used by voice and saved workflows. Existing policy and approval order
are preserved. This does not yet share planning, task persistence, approval
resume, or execution. Use live registered `ToolSpec`
schemas and declared availability, not app-name-specific routing. Enforce
explicit order, add dependencies, ask before material ambiguity, checkpoint
each step, stop dependents after failure, and reconcile state before uncertain
retries.

Saved workflows now classify a timeout or execution exception as
`outcome_unknown`, persist the underlying tool error code, and expose that state
through execution status. The workflow tool explicitly tells the user to check
status before retrying. This prevents an uncertain provider result from being
reported as a definite failure, but it does not yet provide a provider-specific
reconciliation or resume action; do not automatically replay the affected step.
Cooperative cancellation while a capability is in flight now persists an
`OUTCOME_UNKNOWN` step/result before propagating cancellation.

The optional Copilot/Codex workspace adapters now pass through the shared
policy boundary after the required explicit external-agent turn confirmation.
Turn confirmation does not override a per-capability approval requirement:
Copilot receives only tools the shared policy permits without another approval,
and Codex fails closed if policy denies or requires approval for any registered
file-mutating capability because its staged edits are not individual tool calls.
Copilot also validates each SDK invocation with the shared capability argument
schema and rejects malformed structured results. Their model loop, per-file
approval preview, and durable task/approval lifecycle are still separate and
remain migration work.

**Security:** enforce the same `services/policy.py` decision at tool dispatch
for chat, task launcher, integrations, voice, and schedules. Treat tool output
and external content as untrusted. Do not bypass existing confirm/strict
approval, per-user ownership, device scope, or audit.

**Tests and expected result:** use varied synthetic capability names and
different action sequences to test tool discovery, explicit order, dependency
edges, clarification, permission denial, approval pause/resume, stop/cancel,
model outage, partial completion, restart recovery, and duplicate prevention.
Expected result is a single observable task lifecycle across entry points while
existing tool outputs and saved workflow behavior remain compatible.

**Acceptance / exit gate:** test every entry point through the shared executor;
prove all calls are user-scoped, each planned side effect maps to an approved
capability, and a failed/unknown step cannot be reported complete. Do not start
native automation until this gate passes.

### Phase 3 — Deliver the first working MVP on the current web/backend stack

**Objective:** demonstrate useful end-to-end work with the existing React SPA
and FastAPI backend before building native remote-device clients.

**Existing files:** `frontend-react/src/App.tsx`,
`frontend-react/src/views/ChatView.tsx`, `TasksView.tsx`, `MemoryView.tsx`,
`DocumentsView.tsx`, `IntegrationsView.tsx`, and `frontend-react/src/lib/`;
`backend/app/main.py`, `api/chat.py`, `api/tasks.py`, `api/runs.py`,
`services/chat_service.py`, `agents/graph.py`, `tools/`, `models/task.py`,
`models/agent.py`, `models/conversation.py`, `models/memory.py`,
`services/memory_service.py`, `services/policy.py`,
`services/browser_driver.py`, `api/devices.py`, `tools/document_tools.py`,
`api/documents.py`, `api/files.py`, and `services/voice_session.py`.

**New files:** none for the central planner, registry, memory, or task store.
Add a narrowly scoped service/schema/migration only after a failing MVP test
shows the current implementation cannot express the requirement.

**Dependencies and contracts:** Phases 0-2. MVP is: working auth/chat and
conversation context; one shared planner and dynamically selected registered
tools; task plan/status/history/cancel and approval; user-controlled persistent
memory; basic Playwright browser workflows; current Windows backend-host
allowlisted app launch and bounded inspection/input; safe document/file
upload/read/generation; voice path only where the configured browser or server
engines actually work. Existing backend-host control is explicitly labelled
local—not remote.

**Implementation sequence:** stabilize each existing flow with evidence-based
result states; expose plan and permission details in the task UI; make
tool/provider failure visible rather than success-shaped; test read-only and
reversible tasks first; support document search/generation using existing
extractors and libraries; expose clear current-device vs paired-browser
distinctions. Verify an outcome using a postcondition, not merely an HTTP 200,
model response, or dispatched click.

**Security:** least-privilege user scope, server-side secrets, CSRF/OAuth state
checks, isolated file paths, upload type/size checks, approval for send/delete
and OS input, safe browser session isolation, audit redaction, and fail-closed
permission checks.

**Tests and expected result:** Phase 0 unit suites and Playwright E2E for
register/login, chat, plan/progress, approval, memory list/delete, document
upload/search/generation, browser navigation, Windows app registration/launch/
inspection, and failure recovery. Demonstrate at least one approved, ordered
task in the shared Jalora web UI and preserve its task-step evidence.

**Acceptance / exit gate:** new-machine backend/frontend are running; build and
focused tests pass; one representative text task and (if configured) one voice
task complete with verifiable evidence; approval, task history, cancellation,
and honest failure states are visible. This is the first MVP, not proof of
remote desktop or all third-party integrations.

### Phase 4 — Build a native Windows companion for remote device control

**Objective:** move from same-host `pywinauto` functionality to target-approved
control of a separately paired Windows computer.

**Existing files:** `backend/app/api/devices.py`,
`backend/app/models/device.py`, migrations `v1b_device_connections.py`,
`v1c_local_device_apps.py`, `v1d_remote_device_companion.py`, and
`v1e_remote_browser_commands.py`, `frontend-react/src/components/RemoteDeviceCompanion.tsx`,
`frontend-react/src/views/IntegrationsView.tsx`, and `docs/device-companion.md`.
Retain `backend/app/api/devices.py` local controls only for the backend host.

**New files:** a signed Windows companion application and versioned companion
protocol are required because neither currently exists. Choose the companion
language, packaging layout, and protocol after OS/security design; do not
mislabel the browser companion or add native code to the Python backend as a
substitute.

**Dependencies and contracts:** Phases 2-3 and the Windows gate above.
Define authenticated device identity, short-lived credentials, heartbeat,
capability inventory, command/result envelope, target-visible session state,
cancel, revoke, reconnect, and minimum/maximum version compatibility. Use
Windows UI Automation first and OS-approved screen capture/OCR fallback.

**Implementation sequence:** pair and approve on target; inventory installed
apps; add app-selection and **Entire device** grants; surface exact Windows
permissions; stream only while visibly active; execute one validated
accessibility action at a time; return fresh evidence; implement graceful
launch/close and focus/target checks; add upgrade and token-revocation paths.

**Security:** signed installer/updates, encrypted device token, TLS and
device-bound authorization, scope and foreground-window enforcement, visible
screen/input indicator, least-privilege service, rate limits, replay protection,
no force-kill, no silent capture, and immediate stop/revoke.

**Tests and expected result:** protocol/contract tests, simulator tests, and
two physical Windows machines. Verify pairing approval/rejection/expiry,
inventory accuracy, per-app vs entire-device grants, UIA/OCR, action evidence,
offline/reconnect, stale commands, cancellation, update compatibility, and
revocation. Demonstrate from shared web that the selected remote machine—not
the backend host—receives the action.

**Acceptance / exit gate:** all Windows remote-access gate criteria above pass
end to end with versioned supported Windows builds; absent capabilities fail
closed. No remote-control claim before the second-machine test.

### Phase 5 — Add an Android companion under Android's permission model

**Objective:** let the same agent operate an approved Android target where
official APIs and user-granted accessibility/screen permissions allow it.

**Existing files:** shared contracts/models/API from Phases 2 and 4,
`backend/app/api/devices.py`, `backend/app/models/device.py`, device migrations,
and `frontend-react/src/components/RemoteDeviceCompanion.tsx`. The repository
inventory did not find an Android application module.

**New files:** a native Android application, signed build configuration, and
platform capability adapter are required; select package layout and supported
SDK versions as a separate implementation decision. Do not duplicate planner,
memory, task, or permission policy in the Android app.

**Dependencies and contracts:** Phase 4 companion protocol and Android policy
review. The app reports installed-app visibility, active accessibility grant,
MediaProjection state, notification access, and supported actions to the
backend; the server issues only bounded, scoped tasks.

**Implementation sequence:** target approval and pairing; user-readable
permission education; persistent visible foreground session; supported app
inventory; accessibility hierarchy; MediaProjection capture after OS prompt;
one-step execution and postcondition evidence; lifecycle/revoke and capability
reporting.

**Security:** least privilege, Android foreground indicator, Play policy review,
no permission escalation, no background screen capture without active consent,
and separate permissions for notifications, calls, and accessibility.

**Tests and expected result:** Android simulator for API contracts and physical
devices for prompts, app visibility, Accessibility Service, projection,
background/foreground changes, reboot, force-stop, revoke, secure screens, and
app-update compatibility. Verify no action is accepted after permission loss.

**Acceptance / exit gate:** publish a per-Android-version support matrix and
pass shared-web plus physical-device acceptance; do not claim arbitrary app or
call-history access beyond tested system and distribution policy.

### Phase 6 — Consolidate and extend personal communications integrations

**Objective:** deliver supported email, calendar, contacts, messages, and calls
through shared tools and policy, prioritizing existing provider work before new
adapters.

**Existing files:** `backend/app/api/integrations.py`, `emails.py`, `comms.py`,
`calls.py`, `people.py`, `schedules.py`; `backend/app/services/oauth.py`,
`email.py`, `calendar.py`, `integration_registry.py`, `policy.py`, and
`backend/app/tools/email_tools.py`, `calendar_tools.py`, `contact_tools.py`,
`comms.py`, `schedule_tools.py`; integration/communication models, relevant
migrations, and `frontend-react/src/views/IntegrationsView.tsx`,
`CalendarView.tsx`, `PeopleView.tsx`, `SchedulesView.tsx`.

**New files:** only provider-specific adapter modules where no supported
provider contract already exists; no Gmail bot, WhatsApp bot, or calls bot.
Use the common capability registry and normalized message/contact/calendar
schemas.

**Dependencies and contracts:** Phases 2-3; Phase 4/5 only for on-device UI
fallback. Gmail and Calendar/Drive OAuth are explicit; Telnyx/Twilio require
provider credentials and callbacks. The current WhatsApp/personal-account
surface must be audited for provider eligibility and permitted API access before
promising history/unread state or automation.

**Implementation sequence:** reconcile mock vs live connection state; verify
search/read and provider source; ship editable drafts before sending; implement
thread-aware reply and attachment summaries; implement calendar conflicts,
timezone handling, invites, reminders, and follow-ups; resolve contact
identities; add phone call status and messaging where provider permits; require
preview/approval for sends and calls; reconcile receipts before retry.

**Security:** least-privilege OAuth scopes, encrypted token storage, CSRF state,
provider webhook signatures, contact disambiguation, PII minimization, untrusted
message handling, explicit recipient/number review, and no autonomous
unattended sends/calls by default.

**Tests and expected result:** provider contract/mocks plus real test accounts;
search/read, pagination, thread context, drafts/edits, approve/reject, OAuth
revocation/expiry, webhook replay, delivery uncertainty, calendar timezone/DST
and conflicts, duplicate contacts, missed/failed calls, and WhatsApp policy
limits. Verify provider receipts, not only local DB changes.

**Acceptance / exit gate:** each provider shows configured/connected/unsupported
accurately; only features with live provider evidence are labelled live.
Complete end-to-end demonstrations for the providers available in test
accounts; all other capabilities remain explicitly provider-dependent.

### Phase 7 — Deliver cross-domain personal and business productivity

**Objective:** make the same planner useful for documents, projects, meetings,
learning, research, sales, and customer operations—not merely device clicks.

**Existing files:** `backend/app/api/documents.py`, `files.py`, `workspace.py`,
`jobs.py`, `creative/`; `backend/app/tools/document_tools.py`,
`drive_tools.py`, `coding_tools.py`, `web.py`, `browse.py`, and
`backend/app/services/extractors.py`, `knowledge.py`, `fact_verification.py`;
workspace, document, professional-profile, calendar, and task models/migrations;
`frontend-react/src/views/DocumentsView.tsx`, `WorkspaceView.tsx`,
`CreativeStudioView.tsx`, `CalendarView.tsx`, and `TasksView.tsx`.

**New files:** add domain adapters only for missing actions (for example a
selected task/project provider or CRM); reuse existing workspace/tasks,
documents, calendar, and contact services where their schemas fit. Do not add a
parallel project manager, memory store, or document agent.

**Dependencies and contracts:** Phases 2-3 and Phase 6 provider identity/calendar
contracts where needed. A composed task returns sources, artifact IDs,
provenance, plan steps, approval state, and one shared task status.

**Implementation sequence:** expand verified file formats and safe editing;
document meeting-prep summaries and calendar context; add project/task,
deadline, reminder, and sales follow-up compositions; add customer-support/CRM
triage through authorized adapters; add evidence-linked reports and web
research; deliver education/study workflows with source attribution.
Preserve PDF, Word, Excel, PowerPoint, image, and text parsing boundaries;
generation/editing must use actual existing file tools and verify the artifact
is readable and saved.

**Security:** per-user workspace and file ownership, upload and path validation,
prompt-injection isolation for source documents/web pages, no unsupported
legal/medical/business claims, source citations for research, external-sharing
approval, and retention controls for documents and summaries.

**Tests and expected result:** fixtures for PDF/DOCX/XLSX/PPTX/images, corrupt
and oversized files, cross-user access, artifact contents, source provenance,
calendar scheduling, follow-up duplicate prevention, CRM identity ambiguity,
and multi-tool partial failure. Demonstrate a generic multi-app workflow with
synthetic service names and real permitted tools, not a hardcoded example route.

**Acceptance / exit gate:** every productivity workflow has an input contract,
scope and policy, verifiable output/artifact, source evidence, and user-facing
partial/error status; success is demonstrated through the shared web UI.

### Phase 8 — Make scheduled and event-driven autonomy durable and controllable

**Objective:** allow approved work to continue safely when no chat is active,
using the same central executor and policies.

**Existing files:** `backend/app/services/scheduler.py`,
`backend/app/models/schedule.py`, `backend/app/api/schedules.py`,
`backend/app/api/notifications.py`, `backend/app/services/notifications.py`,
`backend/app/services/run_registry.py`, `backend/app/models/task.py`,
`backend/app/models/agent.py`, `backend/app/main.py`, and scheduler/task tests.

**New files:** introduce a durable queue/worker only if Phase 1 proves
in-process scheduling cannot meet restart, concurrency, or latency requirements.
Redis currently serves rate limiting in app code and is in Docker Compose; do
not assume it is a task queue. Select a queue technology and schema only after
operational requirements are measured.

**Dependencies and contracts:** Phases 2 and 6-7 for each event source.
Persist trigger ID, event ID, task ID, idempotency key, due time, policy
snapshot/version, attempt count, checkpoint, result, notification, and next
retry. Triggers must enter the shared planner exactly like a user task.

**Implementation sequence:** define explicit per-user monitoring sources,
schedules, quiet hours, limits, and allowed actions; persist enqueued events
before execution; run with leases/claims and duplicate-event protection; recover
expired claims after restart; pause/resume/stop/revoke schedules and provider
subscriptions; notify users of approval, completion, partial result, and
failure; provide an inspectable execution history.

**Security:** opt-in source-by-source, scoped subscriptions, no broad inbox or
notification scraping, same action policy as foreground tasks, no privilege
inheritance from old approvals, rate/concurrency budgets, secret-free logs, and
immediate revoke.

**Tests and expected result:** restart between enqueue/plan/action/checkpoint,
duplicate webhook, concurrent scheduler instances, missed schedule, time-zone
change/DST, provider outage, credential revocation, expired permission,
uncertain external side effect, quiet hours, stop, and user deletion. Verify no
duplicate sends/calls/deletes and that all outcomes notify the right user.

**Acceptance / exit gate:** background task survives process restart without
lost or duplicate effects, respects policy/quiet-hours/monitoring limit, and is
fully visible and controllable from the web UI. Otherwise retain foreground-only
behavior and label it accurately.

### Phase 9 — Package, deploy, update, back up, and operate supported releases

**Objective:** turn the proven MVP and companions into reproducible, supportable
Windows, Android, and backend deployments with safe recovery.

**Existing files:** `Dockerfile`, `docker-compose.yml`, `Makefile`,
`backend/requirements.txt`, `backend/alembic.ini`, `backend/alembic/`,
`backend/app/config.py`, `backend/app/main.py`, `backend/app/services/metrics.py`,
`structured_logger.py`, `tracing.py`, and `frontend-react/package.json`.

**New files:** signed Windows installer/update manifest, Android signed
APK/AAB/store-release configuration, and deployment/backup automation are
needed only as release artifacts; define exact paths and CI workflow during
implementation rather than pre-creating duplicate packaging systems.

**Dependencies and contracts:** Phases 0-8 to the extent of release scope.
Pin server/frontend/companion protocol versions and define a supported
compatibility window. Current Compose persists SQLite and Redis in volumes;
PostgreSQL is an example/comment, not a ready configured production database.

**Implementation sequence:** choose a supported server topology; externalize
configuration and secret management; add encrypted, tested database and file
backups; define restore runbooks; validate migration upgrade/rollback on a copy;
package a signed Windows installer with user-approved staged updates; release
Android through signed internal-test then approved distribution tracks; define
backend/companion minimum-compatible versions and a safe disable/roll-forward
path; add crash recovery, health/readiness checks, opt-in redacted diagnostics,
and safe rollback. Add PostgreSQL only after driver, migration, backup, and CI
support are implemented.

**Security:** signed artifacts, least-privilege service accounts, secret
rotation, TLS, secure update metadata, dependency vulnerability review,
diagnostic redaction, backup encryption/access controls, and no logging of
screen/message contents by default.

**Tests and expected result:** clean-machine Windows install/update/uninstall;
Android signed artifact install/update and permission retention/revocation;
interrupted upgrade and rollback/roll-forward; database+upload restore; Alembic
upgrade from supported versions; companion/backend version skew; process
crash/restart; health/readiness, latency/resource metrics, and alert checks;
secret scan of built artifacts/logs.

**Acceptance / exit gate:** another clean Windows machine can install, configure,
start, use, upgrade, restore, and roll back using only documented instructions;
release compatibility and support status are visible. No untested data migration
or automatic update to production users.

### Phase 10 — Expand providers/platforms and operate the complete product

**Objective:** broaden the reliable shared capability system to remaining
business providers and platforms without changing the unified-agent principle.

**Existing files:** capability/provider/device contracts, platform gates,
integration registry, OAuth adapter, common policy/audit/task state, and the
current API/UI entry points.

**New files:** platform-specific companion clients or provider adapters only
where absent and approved by a product/security design; no parallel bot or
planner.

**Dependencies and contracts:** mature phases 2-9, published compatibility
matrix, provider terms/API access, app-specific permission review, and
independent security/privacy review.

**Implementation sequence:** expand Windows/Android workflows proven by MVP;
add macOS/Linux only after their permission and session model is tested; support
iOS solely through Apple-permitted/user-mediated functions; add CRM, sales,
education, and business systems via normalized shared capabilities; add
advanced voice/personalization as opted-in extensions of the shared task
runtime.

**Security:** repeat threat model per provider/platform; retain least privilege,
target-side indicator, consent/revocation, signed updates, audit, data
minimization, and known platform restrictions.

**Tests and expected result:** contract, security, real-provider/account,
real-device, regression, accessibility, performance, and shared-web acceptance
for every new capability. Measure task completion, verification coverage,
false-success, duplicate side effects, recovery, latency, and revocation time.

**Acceptance / exit gate:** release a feature only with a tested platform/
provider matrix, passing CI and real-world acceptance, published privacy and
retention behavior, rollback path, and no unresolved critical/high security
findings. Unsupported tasks remain explicitly unsupported or user-mediated.

## Technical dependencies by capability

| Capability | Required foundations | External/platform dependencies |
|---|---|---|
| Shared multi-app agent | Capability registry, task graph, durable checkpoints, policy engine, idempotency, verification contract | Model availability is not a substitute for tool correctness |
| Remote screen and app control | Signed native companion, device identity, authenticated command channel, revocation, target-side OS permission, screen/accessibility adapter | Windows UI Automation/capture; Android Accessibility/MediaProjection; macOS TCC; Linux portals/session; iOS limits |
| App inventory | Companion app catalog API, stable package/process identity, user selection UI | OS package visibility, installed-app APIs, distribution policies |
| WhatsApp | Shared message/thread schema, contact identity, grants, safe content handling, delivery reconciliation | Official API access/eligible account features or permitted user-authorized UI; personal account automation may be restricted |
| Email | OAuth, encrypted credential/token store, normalized threads/attachments, search pagination, common send approval | Gmail/Outlook API quotas, scopes, provider webhooks/change notifications |
| Calls/contacts | Contact/call data schemas, disambiguation, call state machine, explicit authorization | Android runtime permissions/distribution policy, telephony provider or native dialer APIs |
| Media/browser | Search/result normalization, browser/media adapter, UI state evidence | Provider APIs, logged-in session, DRM/app restrictions, accessibility support |
| Background operations | Durable scheduler/queue, subscriptions, idempotency, policy, notifications, visible controls | Provider event APIs, OS service lifecycle, battery/background restrictions |
| Voice | Shared task endpoint, streaming STT/TTS, cancellation and spoken approval | Microphone permissions, supported background audio/wake word, local/cloud speech engines |
| Personalization | User-controlled memory store, provenance, retention/deletion, per-user access controls | Optional vector index; no need to store raw message history by default |
| Documents and productivity | Existing document/extractor, workspace, task, calendar, and artifact services; normalized evidence and output contracts | Parser/OCR/Office-format library support, provider APIs, file permissions, and source-content trust boundaries |
| New-machine development setup | Fresh Python environment, locked Node dependencies, root `.env`, Alembic revision, browser binaries, and selected local AI services | Python/Windows wheel compatibility, Chrome/Edge availability, Ollama model downloads, optional OCR executable |
| Persistent data and task queue | Existing SQLAlchemy models, SQLite/Alembic, APScheduler job store, tested backup/restore | Redis is not currently a general task queue; PostgreSQL needs an implemented driver and production migration/backup support |
| Packaging and operations | Docker Compose, backend/frontend build, signed companion releases, version compatibility, health/metrics/logging, rollback | Windows signing/distribution, Android signing/store policy, TLS/domain/hosting, storage and backup provider |

## Testing and acceptance strategy

Every major capability follows the same release ladder:

1. **Unit/contract tests:** schema validation, adapters, policy decisions,
   dependency planning, idempotency, error mapping, and postcondition evaluation.
2. **Security/privacy tests:** cross-user isolation, scope enforcement, token
   expiry/revocation, replay attempts, prompt-injection content, redaction,
   retention/deletion, and unauthorized-action rejection.
3. **Integration tests:** provider mocks and native-companion simulators test
   offline, timeout, malformed, duplicate, stale-state, and partial-result cases.
4. **Hardware/real account tests:** supported OS versions and permitted test
   accounts verify real prompts, foreground/background behavior, app UI changes,
   and actual provider receipts. Never use a user's production contacts/messages
   as test fixtures without explicit permission.
5. **Shared-web acceptance:** demonstrate target selection, capability status,
   plan preview, approval, execution, evidence, pause/resume, and revoke in the
   shared Jalora web interface.
6. **Completion gate:** report complete only when code, focused tests, real
   target-device test, and shared-web demonstration all pass. Otherwise report
   exactly what was tested and what remains unverified.

Cross-cutting acceptance metrics include per-step and whole-task completion
rate, verification coverage, false-success rate (target: zero), duplicate
side-effect rate (target: zero), safe recovery rate, revocation latency, median
and p95 action latency, and permission/provider failure rates.

## Security, privacy, and autonomy requirements

- Least privilege, separate revocable scopes, OS-mediated grants, short-lived
  device/session tokens, encryption in transit and at rest, key rotation, replay
  protection, request limits, signed companion updates, and secure diagnostics.
- A visible target-side indicator while screen, microphone, or input access is
  active; one-step emergency stop and effective revocation.
- Separate read, draft, send, call, social follow, file search, recoverable
  delete, permanent delete, and screen-stream permissions. **Entire device**
  selects the broadest OS-supported capability set but does not bypass per-action
  approval rules or grant capabilities the OS does not provide.
- Require previews and explicit approvals by default for sending, calls,
  following, purchases, irreversible changes, and deletion. Any future unattended
  policy is per-action, bounded, inspectable, and revocable.
- Treat messages, email, web pages, attachments, OCR, and app content as
  untrusted data; they cannot authorize actions, alter policies, reveal secrets,
  or add tool permissions. Keep model outputs constrained by the policy engine.
- Minimize collection and retention of screen frames, message bodies, call logs,
  and documents; disclose any cloud processing separately; provide deletion and
  export controls and provenance for retained facts.
- Audit task goal, target, grants, approval, tool, result, verification, and
  operator-visible notification. Never log passwords, access tokens, or
  unnecessary raw private content.
- Detect uncertain delivery and inspect current state before retrying. Never
  automatically repeat a potentially consequential side effect based only on a
  timeout.

## Feasibility and third-party limitations

### Feasible with platform/provider support

- A shared capability-driven planner, generic multi-step workflows,
  checkpoints, memory, policies, approvals, task queues, and auditable results.
- Native Windows app inventory, remote screen/accessibility, and guarded input
  after installation and OS authorization.
- Android screen/accessibility interaction after user-granted system
  permissions, subject to OS, distribution, and app policies.
- Provider-authorized Gmail/Outlook operations, calendar tasks, and business
  APIs where scopes and quotas allow.
- Read/draft/send/call/media/file workflows when a provider or OS offers the
  required API and the user grants the appropriate scope.

### Partially possible and adapter-specific

- WhatsApp personal-account history, unread state, group access, and replies:
  feature availability varies by official product/API and may require
  user-authorized on-device interaction. Do not promise parity with the native
  client; document policy and reliability limits.
- Social-app search/follow and law-app content navigation: depends on app
  accessibility, supported APIs, login state, app version, and terms. Results
  require source evidence and explicit confirmation for follows.
- Remote screen/input and call state: OS prompts, lock state, background limits,
  secure screens, elevated windows, DRM, and app accessibility can block access.
- Background monitoring and always-on voice: constrained by OS lifecycle,
  provider event support, battery policy, and distribution requirements.

### Restricted or unavailable by design

- Universal cross-app automation on iOS, silent permission escalation, bypassing
  login/CAPTCHA/DRM/platform controls, and controlling apps that do not expose
  permitted APIs or accessibility surfaces.
- Treating browser pairing as remote desktop access, claiming whole-device
  access from one checkbox, or claiming screen/action success without evidence.
- Guaranteeing a result when the OS, provider, app, network, or user approval
  blocks it. The agent must report blocked, partial, or unverified.

## Definition of complete

The product is not “fully autonomous” merely because it can click a screen.
Completion requires all of the following:

- The user can distinguish the Jalora host from a remote target.
- The target can be paired, choose individual apps from an expandable inventory
  or grant **Entire device**, see the precise OS permissions, and revoke access.
- The target's screen can be viewed remotely with an active indicator and
  immediate stop/revoke.
- Every supported action stays within device/app scope and has a measurable
  postcondition; uncertain outcomes are labeled unverified and not blindly
  retried.
- A multi-process request is converted into a generic ordered plan, honors
  stated ordering and dependencies, verifies each step before dependent actions,
  and reports partial completion honestly when any required step does not pass.
- Each listed workflow is coded, tested on a real target device, demonstrated
  through the shared Jalora web UI, and verified by its completion predicate.
- Unsupported apps/platforms and missing OS permissions are reported honestly.
- Destructive and external-facing actions retain their required previews and
  explicit approvals; no broad device grant bypasses those rules.

## Current state and known gaps

### Evidence and status legend

The following inventory is based on source and test-file inspection of the
repository on 2026-11-07. It is not a fresh test run, provider login, or
real-device acceptance. “Tests exist” means relevant test files are present;
it does not mean they passed on the moved machine. Re-run Phase 0 before
upgrading any item to **verified**.

- **Implemented, test evidence present:** implementation and related tests are
  in the tree; current pass status remains unverified here.
- **Implemented and verified:** requires a passing run or observed end-to-end
  acceptance on the target environment; no feature is assigned this status by
  this source-only review.
- **Implemented, runtime/provider dependent:** code exists but live operation
  depends on machine setup, user configuration, real account, API, or device.
- **Partially implemented:** there is useful code, but scope or lifecycle does
  not satisfy the requested product workflow end to end.
- **Planned, not implemented:** no implementation was found in the reviewed
  application structure.
- **Platform/provider restricted:** implementation is possible only within
  documented OS permissions, provider API access, app policy, and account
  eligibility.

| Capability | Current status from repository evidence | Existing evidence and exact gap |
|---|---|---|
| Main web application | **Implemented, current isolated local run verified** | React SPA in `frontend-react/src/`; `frontend-react/vite.config.ts` proxies `/api` and websockets to FastAPI; `backend/app/main.py` serves `frontend-react/dist`. Vite and the isolated test API returned HTTP 200, and authenticated browser chat requests completed through local Ollama, including a verified arithmetic response. `frontend/` also exists, while README still describes vanilla JS/no build step. Confirm the legacy frontend is unused before retiring it and correct the README. |
| FastAPI backend and API surface | **Implemented, current test evidence present** | `backend/app/main.py` and `backend/app/api/__init__.py` mount chat, tasks, memory, documents, files, devices, integrations, email, calls, comms, schedules, notifications, people, workspace, voice, and creative routers. The complete backend suite passed in this checkout; API presence and test providers do not establish that each external provider works. |
| Central agent/tool loop | **Implemented, test evidence present; unification incomplete** | `backend/app/agents/graph.py`, `services/chat_service.py`, `services/model_router.py`, `tools/base.py`; request-aware tool selection, schemas, plans, structured results, approval and failure paths exist. The graph, voice, and saved workflows reuse `services/tool_preflight.py` for capability argument validation and `services/tool_execution.py` for structured-result decoding; voice applies the shared `policy.decide`. Voice still owns a separate model/tool loop; `TaskEngine` is unwired and `WorkflowEngine` still has a separate executor. Cross-entrypoint planning, persistence, approval resume, and execution remain ununified. |
| Capability discovery | **Partially implemented** | `ToolRegistry.select_for_task()` ranks statically registered tools from metadata/schema. `tools/__init__.py` loads tool modules and `services/integration_registry.py` reports integrations. No native installed-app inventory or dynamic device permission/capability discovery exists. |
| Task plans, history, approvals, cancellation | **Partially implemented, test evidence present** | `models/task.py`, `models/agent.py`, `api/tasks.py`, `api/runs.py`, and `services/task_engine.py` persist tasks, steps, dependencies, runs, approvals, state, and cancellation/resume signals. Pending `send_email` and `send_email_batch` approvals can reconstruct missing serialized graph state only when the owned task has the exact active planned step and the original tool-call ID and capability name match the durable run message. Newly persisted run messages carry an ordering index so recovery does not depend on random UUID ordering when timestamps tie. A subprocess starts a fresh backend app against the same SQLite database with `state_json` missing, reconstructs an individual-email approval, records an explicit skip, and verifies the persisted skipped outcome with no send. Structured `request_user_input` approvals can also recover only when the exact original tool-call ID and capability name remain in durable run messages; mismatched capability names are rejected. Tests cover missing-state editing and approval for a planned batch send and structured input. Approved sending after restart, recovery for other non-email actions, cross-engine consistency, and live provider status remain unverified. |
| Short-term conversation context | **Implemented, test evidence present** | `models/conversation.py` stores conversations/messages and `api/conversations.py` exposes user-scoped history. Bounded context and durable task context must remain distinct. |
| Long-term memory | **Implemented, test evidence present; policy completeness unverified** | `models/memory.py`, `services/memory_service.py`, `embeddings.py`, and `vector_store.py` implement user-scoped storage, retrieval, sensitive-pattern filtering, and deletion/list APIs. Memory controls exist; consent/provenance/retention/export and full index-deletion behavior need further verification. |
| Permission and action approval | **Partially implemented, test evidence present** | `tools/base.py`, `services/policy.py`, `models/agent.py`, approval endpoints, and audit support AUTO/NOTIFY/CONFIRM/STRICT and side-effect metadata for current tools. It is not yet a unified OS/device/app scope model for all future companions and background events. |
| Browser automation | **Implemented, environment dependent** | `tools/browser.py`, `browse.py`, `services/browser_driver.py` implement Playwright and a fake driver; browser state/session tools and tests exist. New machine must install the browser runtime. This is an isolated browser session, not control of an existing user's browser profile or native apps. |
| External coding-agent adapters | **Implemented, externally dependent; policy/lifecycle partial** | The active workspace route can invoke Copilot and Codex adapters. Copilot's exposed tools now honor shared autonomy/tool denial policy and malformed tool outputs fail instead of being treated as success; Codex staged writes are blocked when shared policy denies any registered file-mutating capability, and the service requires explicit external-agent confirmation. SDK/provider availability and credentials are external. Neither adapter is yet integrated with the durable central task/approval lifecycle or per-file approval preview. |
| Current Windows backend-host app operations | **Partially implemented, test evidence present; machine-dependent** | `api/devices.py` provides registered-executable list/add/launch/graceful-close/inspect/input routes, gated to a local Windows backend and confirmed user actions; `pywinauto` is a dependency and `docs/device-companion.md` describes limits. It acts on the backend host, not a remote paired PC; no global installed-app inventory. |
| Browser companion pairing | **Partially implemented** | `models/device.py`, `api/devices.py`, `v1b`-`v1e` migrations, and `RemoteDeviceCompanion.tsx` implement target approval and scoped browser commands. Current target capability is approved public-link opening plus limited message-intake scopes. Pairing is not native screen access or whole-device consent. |
| Native Windows remote control, screen stream, app picker, **Entire device** | **Planned, not implemented** | No resident native Windows companion, target-side screen stream, remote accessibility tree, app inventory picker, or broad-device grant was found. Existing `pywinauto` endpoints cannot substitute. |
| Android companion | **Planned, not implemented; platform restricted** | No Android project/module or companion application was found. Server-side device pairing endpoints do not create an Android app or grant Android OS permissions. |
| Local files and documents | **Partially implemented** | `api/documents.py`, `api/files.py`, document/media tools, extractors, and dependencies for PDF/DOCX/XLSX/PPTX/images exist. This supports application document/upload workflows, not general remote filesystem discovery, Recycle Bin emptying, or large-video deletion. |
| Calendar, reminders, tasks, schedules | **Implemented in part; provider/runtime dependent** | Calendar tools/service and schedule API/model plus APScheduler job store exist; task/workspace/notification views and endpoints exist. Google Calendar requires OAuth; local/mock behavior must not be presented as a live external calendar. Time-zone, recurrence, restart, duplicate-fire, and user-data isolation need runtime acceptance. |
| Email | **Implemented in part; provider dependent** | `api/emails.py`, Gmail OAuth in `api/integrations.py`, email services/tools, drafts and approval/send paths exist. Public-contact outreach sends now require an exact extracted address on a planned, successfully opened source page plus a conservative match between page text and organization-name tokens; the durable Tasks queue displays these two evidence signals separately and warns that ownership remains unconfirmed. Focused source-evidence, sequential eight-organization approval, restart recovery, and frontend production-build checks passed during the latest iteration. Gmail credentials/scopes/receipts are external; Microsoft Outlook is not evidenced as implemented in the inspected provider registry. No live delivery or official-site ownership verification was performed. |
| Contacts and calling | **Partially implemented; provider/OS dependent** | `api/people.py`, contact tools, `api/calls.py`, and Telnyx/Twilio bridges exist. Provider credentials and status webhooks are required for live calls. Universal OS call-history access and remote phone control are not established. |
| SMS / WhatsApp | **Partially implemented; provider/platform restricted** | `api/comms.py`, provider settings, and tool adapters cover configured messaging/webhooks and may use Telnyx/Twilio. Comprehensive personal WhatsApp history/group access/unread-state control is not evidenced; confirm official API and account/policy support before promising it. |
| Web research, media, creative work | **Partially implemented** | Web/search, media/document, creative API/tool modules and React views exist. A specified track/account workflow or every third-party app state is not verified by generic module presence. |
| Customer support/CRM, sales, meeting prep, education | **Planned or partial domain compositions** | Contacts, workspace, jobs, email, calendar, documents, and web research provide reusable pieces. No complete source-backed CRM/customer-support or sales pipeline was identified in this inventory. Build as compositions over shared tools or an authorized provider adapter, not standalone agents. |
| Voice | **Implemented in part; engine/provider dependent** | `api/voice.py` provides transcription and `/api/voice/live`; `services/voice_session.py` plus optional STT/TTS/local model/provider chains are present. Phone media routes also exist. Dependencies and real hardware/provider behavior vary, and voice entrypoints must be tested against the same central policy and task lifecycle. No always-on voice companion is established. |
| Scheduled/background execution | **Partially implemented** | `services/scheduler.py` persists APScheduler jobs and runs scheduled work through `chat_service.execute_chat`; schedules and notifications are user-visible. A general durable distributed queue/worker, arbitrary event subscriptions, broad inbox monitoring, and robust multi-worker exactly-once side-effect semantics were not found. |
| Database and migrations | **Implemented, operational safety needs verification** | SQLAlchemy, SQLite default, Alembic revisions, and startup migration logic exist. Compose includes SQLite data volume and Redis. PostgreSQL is commented out and its driver is not currently listed. Back up/rehearse migration and legacy stamp behavior before restoring a database. |
| Development tests | **Implemented and verified locally (2026-11-09)** | Backend contains 82 `test_*.py` files; the latest full backend suite passed with 1,624 passed, 7 skipped, and 14 warnings after shared structured-result decoding, malformed-output regressions, and external coding-agent policy enforcement. The approval/recovery file contains 11 passing tests. React E2E has eight `*.spec.ts` files; all 40 Chromium E2E tests passed earlier, and the task E2E subset passed all 7 tests against isolated localhost backend/frontend services on 2026-11-09 before the later workspace-only policy change. The task E2E includes a real local API attachment upload and artifact fetch. `npm run build` passed on 2026-11-09 with existing dynamic-import and bundle-size warnings. The authenticated local chat smoke test remains prior evidence; this session's E2E verified the shared localhost task UI, not live model/provider behavior. No live email, external provider, or device action was performed. |
| Installation, operations, updates | **Partially implemented for development/container only** | `Dockerfile`, `docker-compose.yml`, metrics/tracing/logging modules, and local start scripts exist. No signed Windows installer/update mechanism or Android release/upgrade path was found; backup/restore, production DB, rollback, and companion version compatibility need implementation and tests. |
| macOS, Linux, iOS arbitrary app control | **Not implemented / platform restricted** | No native clients were found. iOS universal screen/input is generally unavailable to third-party apps; macOS/Linux need explicit permission/session-specific implementations. Support only tested, OS-permitted capabilities. |

This status matrix is intentionally conservative: source presence and test
files are evidence that an implementation was attempted, not proof it runs on
the new machine, with a live provider, or on a paired device. The roadmap must
upgrade status only with passing tests and the acceptance evidence named above.

## Delivery rule

Work phase by phase. Do not skip the native companion and permissions foundation
to build brittle app-specific clicks. Do not mark a workflow complete until its
code, focused tests, target-device test, and shared-web demonstration all pass.
Record the exact supported app/OS versions and remaining limitations at each
release.
