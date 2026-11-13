 # JALORA Master Implementation Roadmap

**Status:** Authoritative implementation plan
**Created:** 9 November 2026
**Purpose:** The single ordered plan for making JALORA a high-quality AI chatbot and a reliable, consent-based autonomous agent.
**Architecture rule:** **ONE CENTRAL AGENT + ONE SHARED PLANNER/ORCHESTRATOR + ONE DURABLE EXECUTION, APPROVAL, AND RECOVERY LIFECYCLE + MANY SPECIALIZED CAPABILITIES.**

## Authority, scope, and how to use this roadmap

This file is the master reference for future JALORA implementation. Work through numbered stages **1 to 15 in order**, with the stated dependencies and exit criteria. Keep this roadmap current at each stage: update status, evidence, changed files, tests, limitations, and the next stage before starting unrelated work.

Supporting documents remain useful for detailed requirements, but they do not override the master sequence:

- [Capability and market-readiness assessment](./jalora-capability-and-market-readiness.md) is the baseline capability inventory and product assessment.
- [Capability and agentic architecture audit](./jalora-capability-audit.md) records detailed source findings, historical implementation evidence, and known gaps.
- [Full-device automation plan](./full-device-automation-plan.md) is the detailed product/platform subplan for consent-based device automation; it is not evidence that native companions exist.
- [Local-device testing guide](../docs/LOCAL-DEVICE-TESTING.md) documents the isolated LAN test stack and the remaining user/provider/device acceptance gates for Stages 7, 12, and 14.
- [Historical complete implementation plan](./JALORA-COMPLETE-IMPLEMENTATION-PLAN.md) is retained for reference and is superseded as the implementation sequence by this roadmap.

### Operating rules

1. Audit before coding. Search for current implementation and tests; reuse working services instead of implementing duplicates.
2. Do not claim a capability works based on a plan, route, mock, or UI alone. State whether it is source-present, unit-tested, locally verified, provider-tested, or device-tested.
3. Keep provider adapters and device companions as capability modules. They do not own an independent planner, memory, permission policy, task manager, or approval lifecycle.
4. Preserve working interfaces and stored records. Add migrations only when the audited contract proves a necessary data gap; use additive, reversible migrations and document compatibility.
5. Separate **task lifecycle** from **action outcome**. A task may be completed while an action is only submitted; never equate approval, API acceptance, delivery, and verification.
6. Require user approval for consequential external effects according to one policy service. Retrieved content and model output cannot grant permission.
7. Never blindly retry an action with an uncertain result. Reconcile with the provider/target first; if it cannot be resolved, retain `outcome_unknown`, block retry, and tell the user.
8. For each stage, make a small coherent change, add focused tests, run existing regressions, and stop at its exit gate. Do not begin a dependent stage while its prerequisite is failing.
9. Keep platform claims bounded to tested providers, OS versions, permissions, and capabilities. “Full agent” means broad orchestration across supported integrations—not unrestricted control of arbitrary devices or services.
10. The shared localhost UI is part of acceptance, but does not replace backend tests, live-provider sandbox checks, or real-device tests.

### Status vocabulary

- **NOT STARTED:** no stage work has been accepted or verified.
- **IN PROGRESS:** work is active; requirements and open questions remain.
- **PARTIALLY VERIFIED:** code/tests cover part of the stage, but one or more exit criteria are unmet.
- **VERIFIED:** all stage exit criteria have passing evidence at the required test boundary.
- **BLOCKED:** explain the dependency or unavailable external access; do not silently skip.

## Current baseline and stage position

### Verified source baseline

- A central `/api/chat` → `chat_service.execute_chat` → LangGraph path exists, with plan validation, registered tools, policy gates, task/run persistence, and approval pause/resume.
- Existing persistence includes `Task`, `TaskStep`, `AgentRun`, `Approval`, and `ActivityLog`. They already provide task/run/step/approval identifiers, task goal and plan/step arguments, dependencies, progress/status, run state, approval decisions/results, and audit records.
- `Approval` has a durable `pending` → `sending` claim and `outcome_unknown` status. `ActivityLog.meta` and approval/run data can hold evidence and receipt details in existing cases.
- There is no single normalized typed contract covering every execution entry point. Status vocabularies differ between Task, AgentRun, TaskStep, Approval, workflow execution, and provider results. Dedicated idempotency/retry metadata and verification evidence are not established uniformly.
- Chat, schedules, and the workspace chat route reach `execute_chat` in some flows, but this does not mean every adapter uses the same task lifecycle. Voice retains a separate model/tool loop; saved workflows retain a sequential executor; standalone email/direct UI APIs have distinct paths; Copilot/Codex use external-agent loops. The separate `TaskEngine` has no application call site in the prior source audit and must not be wired or deleted without tracing.
- The eight-organization email approval queue is mocked/tested with eight separately edited/approved positions and exactly one mock send per approval; separate tests cover skip, rejection, restart recovery, and uncertain side-effect replay blocking. Official organization ownership and live email sending are not verified.
- Native Windows and Android companion source implementations, paired-device manifests, scoped commands, and receipt handling are present. Signed distribution, physical-device acceptance, and unrestricted app/screen support are not verified; existing same-host Windows operations remain separate.
- Prior evidence includes full backend and Chromium runs, a frontend build, focused approval/outreach regressions, and shared-localhost smoke work. Those results are recorded in the capability audit; rerun relevant tests after changes. They do not prove live provider or real-device acceptance.

### Current stage status

**Stage 1 — VERIFIED / BASELINE FROZEN.** The linked [Stage 1 architecture inventory](./JALORA-STAGE-1-ARCHITECTURE-INVENTORY.md) records source paths, existing model/status fields, lifecycle gaps, full-suite results, and shared-UI limits. Two current Playwright full-suite runs each had one failure; each failed test passed on its isolated rerun, so the baseline records suite instability rather than claiming a clean full-suite pass. Authenticated shared-localhost behavior remains unverified.

**Stage 2 — VERIFIED / CONTRACT V1 FROZEN.** The [Canonical Execution Contract v1](./JALORA-CANONICAL-EXECUTION-CONTRACT-v1.md) has been checked against the existing ORM, approval/email execution paths, API schemas, migration setup, and Stage 1 baseline. It defines separate task/run/step/action/approval/attempt semantics, complete legacy status mappings, additive schema changes, and API compatibility. It also records the unversioned legacy database stamping hazard as a Stage 3 prerequisite. No runtime/schema code was changed. Stage 3 may now implement the schema/types and compatibility tests against this frozen contract.

**Stage 3 — VERIFIED / CENTRAL CHAT LIFECYCLE IMPLEMENTED.** The central chat/LangGraph path now uses a durable task/run/step/attempt lifecycle, typed result envelopes, stable plan-step/dependency IDs, shared tool invocation/decoding, approval linkage, pre-dispatch persistence, and conservative unknown-outcome recovery. The additive `v1g_canonical_execution_lifecycle` migration is tested; `init_db()` refuses to stamp an older unversioned schema as current. Direct deterministic chat paths also create linked task/run pairs. Focused migration, execution, chat, plan, approval, malformed-output, exception, timeout, cancellation, and audit tests passed: **143 passed**. Provider-specific reconciliation/idempotency behavior and authenticated shared-localhost behavior were not verified here; they remain in their later integration/acceptance stages.

**Stage 4 — VERIFIED / SHARED APPROVAL LIFECYCLE IMPLEMENTED.** Chat approvals and standalone email approvals now use shared payload digest/version, expiry, atomic claim, decision, and finalization helpers; chat and email approval decisions re-check the current user policy. Edits persist a sanitized revision and reject stale versions. Revocation, expiry, task/run cancellation, and uncertain email reconciliation have distinct durable outcomes. A completed chat run with an unknown external action is downgraded to `partial`, its optimistic reply is replaced with an explicit reconciliation notice, and the task error is visible to the Tasks UI. Restart recovery also repairs approval-to-execution linkage using the run-scoped stable tool-call ID, and canonical plan synchronization includes newly inserted steps before index compaction.

Stage 4 verification on 10 November 2026: **144 backend tests passed** across approvals, tasks/policy, execution lifecycle, email, and repair regressions; **4 focused Chromium E2E tests passed** for reject/edit flows, task cancellation controls, and uncertain-outcome reconciliation. Python compilation, the React TypeScript/production build, and whitespace checks on Stage 4 files passed. Changed runtime surfaces: `backend/app/services/approval_lifecycle.py`, `backend/app/services/policy.py`, `backend/app/services/chat_service.py`, `backend/app/agents/graph.py`, `backend/app/api/{runs,emails,tasks}.py`, and `backend/app/schemas/__init__.py`. Regression coverage was added or updated in `backend/tests/{test_approvals,test_tasks,test_execution_lifecycle,test_fix_repair}.py`.

Stage 4 limits: approval expiry is enforced lazily when approvals are listed or acted on; no background expiry worker was added. Provider reconciliation and real email delivery were not exercised against live providers; direct email actions still do not all create canonical task/run/step records, which is Stage 7 scope. The browser checks used mocked/test services and do not establish authenticated shared-localhost or live-provider acceptance. Proceed to Stage 5; do not treat these later integration gaps as completed.

**Stage 5 — VERIFIED / SAVED WORKFLOWS USE THE CENTRAL EXECUTOR.** Saved workflow definitions and history remain intact, while each validated child capability is expanded into the active central task/run and dispatched through LangGraph's regular validation, policy, attempt, audit, and approval lifecycle. Typed outputs feed dependent workflow steps. Workflow history links to canonical task/run/step IDs; legacy direct execution entry points reject execution outside the graph. The shared skip route is allowed for workflow child approvals only, and cancelling a canonical task also updates its linked workflow execution history. Approval-waiting graph state persists the workflow cursor/context and resumes after a separate backend process starts.

Stage 5 verification: **24 workflow tests passed**, including approved pause/resume, child rejection and skip, typed dependency outputs, cancellation with pending approval, timeout/unknown status without duplicate dispatch, task-detail visibility, migration/history compatibility, and subprocess restart/resume using the email mock. **40 related approval, execution-lifecycle, task, and migration regression tests passed.** A wider run that also included `test_chat.py` had 97 passes and one unrelated pre-existing failure: `test_stop_single_run_endpoint` expects the old response shape and does not include the existing `outcome_reconciliation_required` field. No live email provider was used. Changed workflow/runtime surfaces include `backend/app/agents/graph.py`, `backend/app/api/tasks.py`, `backend/app/services/chat_service.py`, `backend/app/services/workflow_automation.py`, `backend/app/tools/workflow_tools.py`, `backend/app/models/workflows.py`, and `backend/alembic/versions/v1h_workflow_canonical_links.py`; coverage is in `backend/tests/test_workflows.py`. Proceed to Stage 6.

**Stage 6 — VERIFIED / VOICE USES THE CENTRAL TASK LIFECYCLE.** Production in-app WebSocket, Twilio, and Telnyx session creation now injects the shared model router, and actionable voice turns use `execute_chat` in agent mode. The Call record links to the resulting conversation/task/run; voice approval prompts expose the persisted approval ID, and decisions resolve that exact shared approval. DTMF 1/2 decisions delegate to the same approval handler. Reconnecting with an authenticated call ID restores the existing call/task/run/conversation and pending approval instead of creating a second task. A call that observes an approval already decided in another interface reports the persisted state and does not resubmit it.

Stage 6 verification: focused voice, Twilio, Telnyx, approval, task, execution-lifecycle, and workflow regressions passed together: **108 passed** (`python -m pytest tests/test_voice_live.py tests/test_voice_twilio.py tests/test_voice_telnyx.py tests/test_approvals.py tests/test_tasks.py tests/test_execution_lifecycle.py tests/test_workflows.py -q`). Coverage includes central task/run/approval linkage, approve/reject/cancel, cross-interface approval, no dispatch before approval, exact approval-ID matching, restart recovery, and Twilio/Telnyx codec roundtrips and DTMF routing. The React production build passed (`npm run build`; existing chunk-size and mixed dynamic/static-import warnings remain).

Stage 6 changed runtime surfaces: `backend/app/services/voice_session.py`, `backend/app/api/voice.py`, `backend/app/services/voice_twilio.py`, and `backend/app/services/voice_telnyx.py`; frontend reconnection/approval-ID surfaces are `frontend-react/src/lib/api.ts`, `frontend-react/src/lib/voiceLive.ts`, and `frontend-react/src/views/LiveCallView.tsx`. Coverage is in `backend/tests/test_voice_live.py`, `backend/tests/test_voice_twilio.py`, and `backend/tests/test_voice_telnyx.py`. Real STT/TTS/model providers, live Twilio/Telnyx calls, and authenticated shared-localhost UI flows were not exercised. Central-agent replies are generated through the canonical executor before TTS begins; the older independent voice loop's latency downgrade/token-to-TTS interleaving is not part of this canonical path and remains a UX/performance follow-up, not evidence of a separate executor. Proceed to Stage 7.

**Stage 7 — PARTIALLY VERIFIED / DIRECT ACTIONS AND EMAIL RULE LIFECYCLE.** The shared server-initiated direct-action helper invokes the central chat/LangGraph executor. Standalone email compose/send/reply-draft, calendar create/update/delete, strict-approval document deletion, email auto-reply rule mutations, and inbound auto-replies use the shared task/run/step and approval lifecycle. Inbound message IDs have durable deduplication before task creation; document processing results are linked to resumable canonical tasks.

Paired-device message assistance is now a registered central capability. Summary/draft requests create canonical task/run/step records, re-check device status/scopes in the capability, and explicitly keep drafted replies unsent; deleting an unsent device message is strict-approval gated and refuses messages already queued or delivered. Workspace save/create/delete/rename/mkdir/rollback/upload/legacy-import and allowlisted terminal operations now run as registered capabilities through the same executor, preserving sandbox path containment and project ownership. Workspace project creation and rename use the shared approval/task lifecycle, while project deletion archives rather than physically deleting the conversation and its task history. Confirm-level actions follow the user's shared policy; deletion/rollback remain strict. Pending actions return HTTP 202 with shared approval/task/run IDs; the workspace UI leaves local editor/tree state uncommitted until approval completes and refreshes the affected files afterward. Persisted tool errors retain their prior API error mapping.

Stage 7 verification on 10 November 2026: the integrated regression command covering email, approvals, tasks, execution lifecycle, agent phase B, calendar, documents, paired devices, remote companion, and workspace passed **169 tests** (`python -m pytest tests/test_phase_e_email.py tests/test_approvals.py tests/test_tasks.py tests/test_execution_lifecycle.py tests/test_agent_phase_b.py tests/test_oauth_calendar.py tests/test_documents.py tests/test_device_bridge.py tests/test_remote_device_companion.py tests/test_workspace_projects.py -q`). The focused workspace/device suites passed **41 tests**. Against the running shared localhost app, authenticated Playwright checks passed for the device-access view and the full workspace project create/rename/archive approval flow (**2 passed**). The React TypeScript/production build passed; existing mixed dynamic/static-import and large-chunk warnings remain. Python compileall and the focused diff check passed. Pylance found no diagnostics in `workspace.py`; `device_tools.py` only reports its existing platform-conditional static-analysis warnings.

Changed Stage 7 surfaces include `backend/app/services/direct_action.py`, `backend/app/services/chat_service.py`, `backend/app/api/workspace.py`, `backend/app/tools/workspace_tools.py`, `backend/app/tools/device_tools.py`, `backend/app/api/devices.py`, workspace/device tests, `frontend-react/src/views/WorkspaceView.tsx`, `frontend-react/src/components/DeviceAccessPanel.tsx`, `frontend-react/src/lib/api.ts`, `frontend-react/src/lib/types.ts`, and the shared-localhost lifecycle regression in `frontend-react/e2e/regression.spec.ts`, alongside the previously listed email/calendar/document integrations.

Stage 7 remains PARTIALLY VERIFIED / BLOCKED ON EXTERNAL ACCEPTANCE. Workspace project create/rename now use canonical tasks and approvals; deletion safely archives the workspace to preserve task history. Authenticated shared-localhost acceptance is now demonstrated by the two Playwright flows above. Live email/calendar provider-sandbox acceptance and paired-device hardware acceptance were not run because no explicitly authorized provider-sandbox target or physical paired-device test target was identified for this session; no live sends or device actions were attempted. Real Windows app-control acceptance remains bounded by Stage 12 platform/security work. The user explicitly waived these remaining Stage 7 acceptance checks to proceed to Stage 8. This is a sequencing waiver only: Stage 7 is not VERIFIED, and the missing external acceptance remains open.

**Stage 8 — VERIFIED AT THE ISOLATED LOCAL TEST BOUNDARY.** Schedule fires are journaled by a stable schedule/UTC-occurrence key and atomically claimed before dispatch. Action schedules invoke `execute_chat` with canonical `trigger_kind`, `trigger_key`, and `trigger_data`; occurrence rows link the resulting canonical task/run and one occurrence-linked notification. A fire with a persisted task is never automatically replayed after interruption; startup recovery reconciles approvals and terminal states, marks active uncertain outcomes unknown/partial, and only reclaims a dispatch that has no canonical task. Notify-only reminders remain distinct. Recurring jobs advance from the exact fire time; fires older than the one-hour grace period are recorded as missed rather than dispatched. Scheduler ownership uses an expiring DB lease, heartbeat, and standby retry. Schedules now persist IANA timezone/expression, parse local wall-clock triggers, reject nonexistent local one-shot DST times, and expose create/edit/timezone/latest-occurrence progress in the API and React view. Task completion/cancellation updates the same occurrence and its linked notification; schedule progress links to the canonical task.

Stage 8 implementation surfaces include `backend/app/services/scheduler.py`, `backend/app/services/chat_service.py`, `backend/app/services/notifications.py`, `backend/app/api/{schedules,notifications,tasks}.py`, `backend/app/models/{schedule,notification}.py`, `backend/alembic/versions/v1i_schedule_occurrence_lifecycle.py`, `backend/app/database/session.py`, `backend/tests/{test_phase_g_schedules,test_migrations}.py`, and `frontend-react/src/{App.tsx,lib/i18n.ts,lib/types.ts,views/SchedulesView.tsx,views/TasksView.tsx}`; the UI regression is `frontend-react/e2e/schedule.spec.ts`.

Stage 8 verification: `python -m pytest tests/test_phase_g_schedules.py tests/test_migrations.py -q` passed **43 tests**, covering timezone/DST, misfire boundaries, simultaneous fires producing one mock external action, occurrence claim races, lease expiry/takeover, cross-process restart recovery, approval/status synchronization, and migration behavior. Isolated Playwright (`CI=1`, separate ports and temporary test DB) passed **4 tests**, including schedule create/edit/timezone and opening the linked canonical task in Tasks. `npm run build` and Python compilation passed; existing frontend chunk/dynamic-import warnings remain. Chat/task/approval/lifecycle regressions passed **93 tests with one known pre-existing failure**: `test_stop_single_run_endpoint` asserts the old response shape and omits `outcome_reconciliation_required`. Pylance reported only unused-symbol warnings and no new errors. The pre-existing shared backend at `127.0.0.1:8000/api/health` timed out during this check, so no shared-instance or live-provider acceptance is claimed; the isolated app used by Playwright started cleanly and passed.

Stage 8 exit criteria are met at the isolated automated-test boundary. Follow-up operational verification: restore/check the pre-existing shared localhost backend before relying on its UI; do not treat that unavailable instance as evidence of Stage 8 failure or success. No live provider actions were sent.

**Stage 9 — VERIFIED AT THE ISOLATED MOCK-PROVIDER/API TEST BOUNDARY.** Workspace Copilot/Codex turns now enter the central chat executor as `external_agent` tasks and use the registered `run_external_coding_agent` capability for shared task/run/step/approval/audit state. Both providers work on isolated staging copies; validated file diffs and provider responses are returned as preview evidence, not written to the selected project. The capability is classified as external, non-idempotent, and non-retryable, and provider command execution is not exposed. Applying a reviewed proposal goes through the existing workspace save capability and shared policy/approval path; tests confirm the project remains unchanged while approval is pending and changes only after approval. Rejection survives a backend subprocess restart without invoking the provider; provider staging/cancellation and both provider approval flows are covered by fake-provider tests. The React workspace detail formatter displays the staged changes and response.

Stage 9 implementation surfaces: `backend/app/services/workspace_agents.py`, `backend/app/tools/external_agent.py`, `backend/app/tools/__init__.py`, `backend/app/api/workspace.py`, `backend/app/services/chat_service.py`, `backend/app/agents/graph.py`, `backend/tests/test_workspace_projects.py`, and `frontend-react/src/lib/toolDetail.ts`. Verification: fake-provider approval/preview tests passed **2 tests**; the combined workspace, approval, execution-lifecycle, and task regression command passed **68 tests**. Python compilation, focused Ruff checks, and the React production build passed in the Stage 9 work; existing frontend bundle warnings remain. The initial apply-after-preview test attempted an `edit_code` tool that the chat task had not selected; the executor correctly rejected it. The test now exercises the supported workspace editor save endpoint under explicit approval, proving the same no-write-before-approval property without weakening tool selection. Live Copilot/Codex credentials and authenticated shared-localhost UI acceptance were not available/run; no live provider request was made. These are not required for the stage's mock contract-test exit gate and remain unverified rather than presumed successful.

**Stage 10 — VERIFIED AT THE LOCAL AUTOMATED-TEST AND MEASURED-BASELINE BOUNDARY.** Memory is user-controlled: manual creation and correction honor the memory-enabled preference; users can correct/export memories and set retention; expired entries are lazily removed on list/search/export; and sensitive preference updates are rejected before existing data is replaced. Conversation and memory retrieval remain user-scoped. System instructions now strengthen uncertainty, clarification, Kiswahili, and truthful action reporting. A loopback-only evaluation runner and version 2 synthetic dataset define explicit thresholds and cover routing, factuality, uncertainty, clarification, truthful outcomes, multilingual behavior, argument quality, task decomposition, refusal, latency, cost, and restart recovery.

Stage 10 implementation surfaces: `backend/app/{api/memory.py,api/settings.py,schemas/__init__.py,services/memory_service.py,services/chat_service.py,services/preference_extractor.py,tools/memory_tools.py,agents/prompts.py}`, `backend/tests/{test_memory.py,test_chat_quality_eval.py,fixtures/chat_quality_eval.json}`, `backend/scripts/evaluate_chat_quality.py`, and `frontend-react/src/{views/MemoryView.tsx,lib/types.ts,lib/i18n.ts}`. Focused verification passed **41 tests, 1 skipped**, including memory, evaluation-contract, preference, Ollama-evaluation, and external-agent restart-recovery tests; Python compilation passed. The React production build passed after the memory UI changes; existing bundle/dynamic-import warnings remain.

Version 2 local model results: Ollama `qwen2.5:1.5b` passed **8/8** (100%; critical factuality/truthfulness 100%; average **8.504s**; local estimated cost **$0**), meeting all measured thresholds. Ollama `qwen2.5:0.5b` passed **4/8** (50%; critical 100%; average **5.957s**; $0) and failed current-data uncertainty, clarification, Kiswahili, and study-plan decomposition; one failure invented a current rainfall value. This fallback does **not** meet the quality threshold and must not be reported as meeting it. LM Studio Qwen3 4B's earlier version 1 baseline was **5/7**, average **36.758s**, and is not directly comparable to version 2; it exceeded the latency threshold and also missed clarification and Kiswahili. These are synthetic local model checks using the evaluation prompt, not full production-chat or provider certification.

The broader chat/recovery regression run still has **9 failures**: eight direct graph tests supply fabricated task/run IDs that are not persisted as canonical rows, so the lifecycle correctly rejects those invalid links; the stop-endpoint test expects the old response shape and omits `outcome_reconciliation_required`. These are not counted as passing Stage 10 evidence and remain visible regression/test-fixture work; no lifecycle validation was weakened to hide them. Ruff was unavailable in the active environment. No authenticated shared-localhost or live-provider acceptance is claimed. Stage 10's exit gate is met for the memory lifecycle, isolation tests, and explicit benchmark baselines; model-specific and broader-suite limitations remain recorded above.

**Stage 11 — VERIFIED AT THE LOCAL AUTOMATED-TEST, MOCK-PROVIDER/API, AND ISOLATED-BROWSER BOUNDARY.** Added an authenticated capability catalog endpoint and Integrations-view scope cards derived from the shared registry. The catalog links each advertised module to actual registered operations, input fields, permission levels, default approval requirement, and side-effect classification; each card also lists its scope, limitations, and existing local tests. The UI explicitly distinguishes registration/local coverage from live-provider verification and warns that provider acceptance, delivery, reservation, and verification require action-specific task evidence. Covered modules include web research/browser, email, messaging/calls, contacts, files/documents/media, calendar/reminders, cloud drive, maps, weather, translation, travel, payments, jobs, creative generation, coding/workspace, and math/charts. No independent execution engine or planner was added.

Stage 11 implementation surfaces: `backend/app/services/capability_catalog.py`, `backend/app/api/system.py`, `backend/app/agents/graph.py`, `backend/app/services/chat_service.py`, `backend/tests/test_capability_catalog.py`, `backend/tests/test_phase_j_coding.py`, `frontend-react/src/views/IntegrationsView.tsx`, `frontend-react/src/lib/i18n.ts`, `frontend-react/src/styles.css`, and `frontend-react/e2e/regression.spec.ts`. The catalog contract tests verify authentication, registry-backed tool/schema/permission metadata, existing test references, and approval-required defaults for consequential actions. Full focused capability, policy, provider-mock, and recovery batch passed **583 tests, 1 skipped**. Additional execution lifecycle/resume regressions passed **10 tests**; changed Python modules compiled successfully; React production build passed with existing dynamic-import and large-chunk warnings; isolated Playwright capability-catalog acceptance passed **1 test**.

During the approval-path regression, validation exposed that a legacy workspace scope was not retained across approval resume; the resumed executor instead derived a different workspace target from the chat conversation. The canonical run state now persists `tool_context_project_id` through graph serialization and restores it on resume, retaining the existing conversation-based fallback for old runs. The workspace rollback regression now asserts there is no write while approval is pending and that the approved action restores the intended file. No external provider sends, bookings, calls, payments, or live searches were performed. Credentials, provider sandboxes, actual recipient delivery, and device-lab acceptance remain unverified; the catalog makes this boundary explicit rather than claiming those capabilities are live-certified.

**Stage 12 — PARTIALLY VERIFIED / BUILD AND HARDWARE GATES BLOCKED.** Versioned outbound protocol support and Windows/Android companion source clients are present, including scoped app manifests, per-command target approval, bounded operations, revocation, and receipts. The Windows companion can discover classic desktop executables from installed-app registry entries but still requires the user to select each app into its local allowlist; packaged/UWP apps are not discovered. It refuses to type into password fields and marks text entry successful only when UI Automation confirms the approved text, without returning the field contents. Receipt evidence updates the linked canonical task step; successful receipts automatically resume remaining central-agent steps, and identical receipt retries can recover continuation after an interrupted attempt without repeating the device action. The verified receipt is persisted as a structured successful tool result so the central graph does not misclassify it as a failed action. Mocked Android coverage verifies independent commands/receipts for two approved apps on one shared task and receipt-triggered continuation; the related Android, remote-device, protocol, and agent-resume batch passed **38 tests**. Windows companion discovery and executor coverage passed **16 tests**. This does not prove central-agent autonomous multi-app sequencing, signed distribution, or physical-device interaction. No JDK/Gradle/Android SDK or physical Android target was available for build/device acceptance; signed Windows distribution and interactive Windows acceptance are also outstanding. Browser pairing remains browser-only. No platform is claimed universally compatible, and no native-control capability is inferred from browser pairing or backend-host Windows controls.

## Ordered implementation stages

### 1. Complete the architecture inventory and freeze the baseline

**Goal:** establish what actually runs, what is only present in code, and where each entry point reaches execution, persistence, policy, approval, and reporting.

**Scope:**

- Trace chat, voice/WebSocket, saved workflow tools, standalone email, direct frontend actions, scheduled jobs/reminders, workspace Copilot/Codex adapters, and every capability with consequential side effects.
- For each path identify: entry endpoint, planner/model loop, capability registry, policy decision, approval record, task/run/step persistence, audit, provider/target receipt, retry/recovery, UI surface, and tests.
- Locate existing state fields and migrations; search call sites before treating `TaskEngine` or any legacy executor as live.
- Record a baseline of focused/full tests and localhost UI behavior without touching user data or real recipients.

**Exit gate:** every execution path is represented in a source-linked matrix; each stated behavior has a test or is explicitly marked unverified; contract gaps are separated from existing fields. No implementation change starts until the contract design in Stage 2 is written and reviewed.

Stage 1 source-path and model-field inventory: [JALORA Stage 1 architecture inventory](./JALORA-STAGE-1-ARCHITECTURE-INVENTORY.md). The report records observed paths, existing persistence/policy behavior, lifecycle gaps, relevant test suites, exact full-suite baseline results, and shared-localhost verification limits.

### 2. Define the canonical task, run, step, result, and approval contract

**Goal:** specify one backward-compatible contract that the central executor and all adapters can share.

**Stage 2 frozen design:** [JALORA-CANONICAL-EXECUTION-CONTRACT-v1.md](./JALORA-CANONICAL-EXECUTION-CONTRACT-v1.md). The design includes the additive migration decision, legacy API strategy, state mapping, and test matrix. Stage 2 is design-only; implementation and migration tests belong to Stage 3.

**Contract requirements:**

- Stable `task_id`, `run_id`, `step_id`, tool-call/action ID, and `approval_id`; preserve current identifiers and exact linkage where already durable.
- Task goal plus structured, validated input; capability/tool name and version; dependency IDs/order; timestamps; progress; owner/conversation/trigger/target.
- Typed step result with success/failure, error code, output, evidence/source references, verification state, provider/target receipt, and safe user-facing summary.
- Separate task lifecycle from action outcome. Task lifecycle must represent queued/running/awaiting approval/paused/partial/completed/failed/stopped/cancelled. Action outcome must distinguish planned/prepared/awaiting approval/approved/submitted/provider accepted/delivered-if-confirmed/verified/failed/unknown/rejected/skipped/cancelled as applicable.
- Durable approval reference and decision metadata; edit/version history where required; expiry/revocation rules.
- Retry policy, attempt count, idempotency key, and reconciliation strategy. Reuse an existing column/JSON contract if safe; add columns/tables only after a documented gap and migration review.
- Resume cursor/checkpoint that cannot mark a side effect complete without its recorded result.

**Design rule:** create a mapping table from every existing status and field to this contract. Do not force provider delivery states into `Task.status`, and do not mark a whole run successful when a requested consequential step is unknown.

**Exit gate:** versioned contract and migration decision are documented; schema/type compatibility and legacy API response strategy are testable. No destructive migration.

### 3. Strengthen the central execution service and lifecycle

**Goal:** make the existing chat/LangGraph architecture the canonical orchestrator without rebuilding it.

**Scope:**

- Keep the graph responsible for understanding/planning/capability choice/dependencies and user-facing clarification.
- Extract or strengthen the shared execution boundary for argument validation, policy evaluation, tool invocation, structured result decoding, timeout/error mapping, audit, receipt capture, and task-step updates.
- Ensure every side effect receives stable step/call identity and idempotency/reconciliation context where supported.
- Persist before dispatch and after result; handle cancellation races and process shutdown explicitly.
- Map graph state and existing database rows to the Stage 2 contract without losing completed steps.
- Keep tool implementations modular and avoid feature-specific planners.

**Exit gate:** central chat acceptance, plan/dependency tests, malformed/exception/timeout tests, cancellation tests, audit tests, and existing full relevant suites pass. A side effect cannot produce a success-shaped fallback.

### 4. Centralize policy, approval, edits, revocation, and safe recovery

**Goal:** any interface can request an approval, but all interfaces observe and resolve the same durable decision and task state.

**Scope:**

- Define one policy decision service for AUTO/NOTIFY/CONFIRM/STRICT and user/tool/provider/device scopes.
- Normalize approval create/claim/edit/approve/reject/skip/expire/revoke and stop/cancel interactions across interfaces.
- Preserve atomic pending-to-in-flight claims; prevent two interfaces from executing the same approval.
- Version edits and bind the approved payload to the exact action/step; do not approve one payload then execute different arguments.
- Record safe retry/idempotency and provider reconciliation semantics; unknown remains blocked from retry absent positive reconciliation or explicit supported resolution.
- Ensure background tasks and external content cannot bypass approval.

**Exit gate:** concurrent approval, edit race, duplicate submission, expiry/revocation, cancellation-in-flight, restart recovery, and unknown-outcome tests pass with consistent API/UI status.

### 5. Integrate saved workflows into the central executor

**Goal:** preserve saved workflow definitions, templating, and execution history while removing the parallel execution/approval lifecycle.

**Scope:**

- Keep workflow records as reusable task templates/capability compositions.
- Convert validated workflow steps into central task steps with dependencies and stable inputs/outputs.
- Resolve templates from typed prior-step outputs; fail explicitly when references are unavailable.
- Route every step through Stage 3 invocation and Stage 4 policy/approval.
- Persist checkpoints/outcomes using the canonical task/run/step contract.
- Provide migration/compatibility for existing workflow records and execution-history APIs; do not erase old records.

**Exit gate:** existing workflow tests remain green; tests prove approved pause/resume, reject/skip, restart recovery, cancellation, timeout/unknown handling, dependency outputs, and one consistent task visible in the shared Tasks UI.

### 6. Integrate voice sessions into the central task lifecycle

**Goal:** retain audio/STT/TTS transport while making voice a user input/output surface for the same central agent and task executor.

**Scope:**

- Parse voice turns into the same authenticated task request/context envelope as chat.
- Preserve in-call conversation and audio streaming but submit actionable intent to the shared graph/executor.
- Use the same policy, approval records, progress, cancellation, task ID, and recovery path.
- Voice confirmations must resolve the exact pending approval shown in the shared UI; ambiguous affirmations cannot authorize an unrelated action.
- Persist clear links between Call, conversation, run, and task; do not create a second voice task manager.

**Exit gate:** in-app voice and telephony transport tests verify the shared task/run/approval IDs, denial/approval parity, interruption behavior, cancellation, restart visibility, and no-send-before-approval. Real audio/provider testing is separately labeled.

### 7. Unify standalone email and direct frontend actions

**Goal:** keep dedicated email/calendar/documents/devices views and APIs, but ensure consequential actions use the shared task, policy, approval, and receipt contracts.

**Scope:**

- Direct send/modify/delete/share actions must create or attach to a canonical task/run/step and use shared policy/approval.
- Retain specialized domain forms and APIs as adapters; remove duplicated send/approval state transitions only after contract and regression coverage.
- Preserve attachment ownership/validation, exact edited-payload binding, provider receipts, uncertain-outcome reconciliation, and no blind retry.
- Integrate source-aware multi-organization outreach with identity/uncertainty decisions and ordered per-organization outcomes.
- Ensure the same approval appears and resolves consistently in Chat, Tasks, Email, and activity history.

**Exit gate:** email mock and provider-sandbox tests cover single/multiple sends, edit, attachments, reject/skip/save, restart, duplicate approval, provider acceptance/uncertainty/reconciliation. Direct UI actions surface the same task/approval state. Live sends are only to an authorized sandbox/test recipient.

### 8. Unify schedules, reminders, notifications, and background execution

**Goal:** scheduled triggers create ordinary canonical tasks rather than a separate kind of execution.

**Scope:**

- Keep schedule definitions and scheduler as trigger infrastructure; every action trigger creates a normal task/run with schedule/occurrence/idempotency identity.
- Notifications link to the canonical task and show its actual state/evidence.
- Handle approval-required jobs, missed fires, timezone/DST, repeated fires, cancellation, schedule edits, restart, and duplicate scheduler workers.
- Keep notify-only reminders distinct from actions that use tools.
- Define bounded background autonomy, user policy, and pause/revoke controls.

**Exit gate:** restart/multi-worker/double-fire and timezone tests prove one occurrence cannot accidentally duplicate a non-idempotent action; approvals and outcomes are visible in the same task UI.

### 9. Integrate Copilot/Codex and other external agent adapters

**Goal:** retain external coding models as optional capability providers, not parallel Jalora agents.

**Scope:**

- Make each external turn a canonical task with central policy, approval, logs, cancellation, and result reporting.
- Preview staged file changes and require appropriate approval before workspace mutation; preserve existing safety gates and tool filtering.
- Normalize provider timeouts, partial output, tool invocation, SDK errors, and unknown write outcomes.
- Make task ownership/target directory/allowed file scope explicit and auditable.

**Exit gate:** fake and available SDK tests show consistent task/approval state, denied action enforcement, edits/approval previews, no unapproved writes, cancellation, and safe recovery. Live SDK credentials are not required for mock contract tests and must not be claimed if absent.

### 10. Unify context, memory, and chatbot quality

**Goal:** improve the assistant as a chatbot while preserving explicit user control over shared context.

**Scope:**

- Define authorized conversation context and task-local context boundaries for chat, voice, schedules, and adapters.
- Preserve user-scoped memory provenance, consent, correction, export/deletion, and retention controls; prevent cross-user/task leakage.
- Add clarification behavior for ambiguity and missing inputs, grounded answers/citations for research, and honest uncertainty.
- Build model/provider evaluation datasets for factuality, tool selection, task decomposition, argument quality, refusal, latency, cost, recovery, and multilingual behavior.
- Establish regression thresholds and compare configured models using repeatable tests rather than anecdotal prompts.

**Exit gate:** context-isolation/security tests pass; memory lifecycle is documented and testable; benchmark results have explicit baselines and no claimed improvement without measurement.

### 11. Complete high-value capability modules through the shared architecture

**Goal:** improve practical capability breadth without adding feature-specific planners or duplicate execution systems.

Implement and validate each module through the central registry and Stage 2-4 lifecycle:

- **Web research/browser:** trustworthy search, source provenance, multi-source corroboration, URL safety, untrusted-content isolation, citations, and explicit low-confidence decisions.
- **Email:** supported inbox/thread/attachment workflows, source-aware contacts, drafts, approval, safe send, receipts, and provider reconciliation.
- **WhatsApp/SMS:** only supported official integrations and accounts; label unsupported personal-history features clearly.
- **Contacts/calling:** contact disambiguation, provider permission/approval, call progress and truthful completion evidence.
- **Files/documents:** owner-scoped search/read/summarize/create, generated-file validation, review before destructive operations, attachment safety.
- **Calendar/reminders:** provider-aware event operations, timezone/recurrence, approvals, and durable scheduled actions.
- **YouTube/media:** identify requested content, control only supported playback interfaces, and verify player state where possible.
- **Additional modules:** maps, weather, translation, travel, payments, jobs, creative tools, and future capabilities are audited against the same registry/policy/lifecycle before expansion.

**Exit gate:** each claimed capability has an explicit supported-scope card, schema and policy tests, provider/mock contract tests, failure/recovery tests, and a user-visible verified/unverified outcome. Do not expand breadth by creating independent agents.

### 12. Implement consent-based native Windows and Android companions

**Goal:** extend automation to paired devices only where the OS supports the capability and the user grants scoped permission.

**Scope:** follow the detailed [full-device automation plan](./full-device-automation-plan.md) for target selection, device/app inventory, screen/accessibility, local and remote control, least-privilege permission, pairing, revocation, verification, install/update, and named workflows.

- Design authenticated, versioned, replay-resistant companion protocol and target identity before remote commands.
- Build native Windows and Android companion projects separately; do not treat browser pairing or the Windows-host API as a native companion.
- Report live capability inventory and OS permission status from the target.
- Require clear preview/approval for consequential actions; enforce policy target-side as well as server-side.
- Verify action results using target evidence; record blocked/missing permission honestly.
- Test named destructive workflows only with explicit confirmation and safe test fixtures.
- Consider macOS, Linux, and iOS independently; no broad support claim without an implementation and OS-specific test.

**Exit gate:** security review, permission/revocation tests, protocol tests, supported-version/device-lab acceptance, restart/reconnect tests, and a shared UI demonstration all pass for each claimed OS capability. Hardware/platform gaps remain BLOCKED, never silently marked complete.

### 13. Deliver consistent task, approval, and evidence UX

**Goal:** make the shared lifecycle understandable and controllable from every frontend surface.

**Scope:**

- One task center with plan, progress, active capability, dependencies, sources/evidence, approval status, completed/failed/blocked/unknown steps, and next action.
- Consistent pause/resume/cancel and approval editing where valid; do not expose impossible controls or claim hidden execution.
- Clear descriptions of permissions and provider/OS boundaries before authorization.
- Notification links point to the same task/approval, including background and voice-originated work.
- Accessible responsive interface, keyboard/screen-reader controls, localization, and sensible mobile/low-bandwidth behavior.

**Exit gate:** API/UI contract tests and shared-localhost Playwright scenarios cover each entry point and state transition; users can identify what happened, what did not, and what decision is required.

### 14. Complete security, privacy, reliability, and operations foundations

**Goal:** make a tested local product supportable and safe to deploy.

**Scope:**

- Threat-model tools, prompt injection, API/webhook boundaries, file attachments, credentials, companion protocol, multi-user isolation, and background execution.
- Enforce least privilege, secret redaction, signed webhook verification, SSRF protections, secure storage, audit retention, backup/restore, and deletion/export.
- Add rate limits, timeouts, retries only where safe, circuit breakers, quotas/cost controls, metrics, tracing, alerts, health checks, and operational runbooks.
- Rehearse database migrations/rollback and production database behavior; test multi-worker scheduling and concurrent approvals.
- Define support matrix, provider terms/policies, versioned API contracts, secure distribution/update for native companions, and incident response.

**Exit gate:** security review findings are resolved or explicitly accepted; recovery/backup/restore and load/concurrency tests meet documented thresholds; deployment has a tested rollback path and no secret leakage.

### 15. End-to-end acceptance, release gate, and ongoing roadmap maintenance

**Goal:** demonstrate that JALORA is a capable chatbot and an autonomous agent for its explicitly supported capabilities.

**Mandatory acceptance scenarios:**

1. Chat answers, clarifies, cites when required, and does not invent tool outcomes.
2. Chat starts a multi-step task with validated dependencies, visible progress, and persisted outcomes.
3. Voice creates the same task/run and approval identity as chat and can resume/cancel consistently.
4. Saved workflows execute via central steps and share the same approval/recovery behavior.
5. Direct email action uses central policy, editable approval, durable receipt, and no duplicate on uncertain result.
6. Scheduled work creates a normal tracked task and cannot bypass permission.
7. External coding adapters use the same task/policy/audit boundary and do not make unapproved writes.
8. An interruption/restart preserves completed work and resumes only safe remaining work.
9. Approval/rejection/edit/revocation/cancellation from any supported interface updates all views atomically.
10. Existing capabilities remain functional; run the eight-organization outreach mock acceptance and all existing relevant regression suites.
11. Shared localhost browser acceptance verifies authenticated user flows; provider sandbox and actual device acceptance are separately recorded.
12. Each result is classified as prepared, approved, submitted, accepted, delivered-if-known, verified, failed, unknown, or cancelled with evidence.

**Release gate:** all relevant backend tests, type/build/lint checks, full browser regression, migration tests, security tests, provider sandbox tests, and device-lab tests for claimed capabilities pass. Publish changed files, exact evidence, known limitations, and next stage. A skipped external provider/device test must be labeled unverified, not assumed passed.

## Cross-stage acceptance contract

Every implementation slice must demonstrate:

- **Correctness:** focused tests reproduce the target behavior; negative cases fail safely.
- **Compatibility:** current APIs, data, and working UI are preserved or migrated with explicit compatibility tests.
- **Consistency:** task/run/approval status is the same across chat, Tasks, voice, email, schedules, and activity surfaces for that task.
- **Safety:** authorization is explicit and scoped; untrusted content cannot grant authority; uncertain external effects are not blindly repeated.
- **Durability:** restart recovery uses persisted evidence and stable IDs, not in-memory assumptions.
- **Truthfulness:** reported success is backed by the strongest available provider/target evidence and labeled with its actual certainty.
- **Modularity:** new capability modules register tools/adapters and do not add another agent/planner/task manager.

## Deferred ideas from historical plans

The older complete plan contains preference engines, priority scoring, business planning, customer-support ticketing, code-review enhancements, and other feature proposals. These are **not automatically approved implementation scope**. Before adding any, confirm a user/customer need, search for existing implementations, define how it fits the shared capability/task contract, and prioritize it against reliability, safety, and the market-readiness assessment.

## Progress ledger

Update this table in every milestone report. Do not mark a stage VERIFIED without linking its tests or acceptance evidence.

| Stage | Status | Evidence / remaining work |
|---:|---|---|
| 1 | VERIFIED | [Stage 1 architecture inventory](./JALORA-STAGE-1-ARCHITECTURE-INVENTORY.md) freezes source paths, current model/status fields, gaps, test inventory, exact baseline results, and localhost limitation. Backend: 1,631 passed / 7 skipped. Playwright: two full runs at 39 passed / 1 failed each; each failing test passed in isolation. Shared authenticated UI remains unverified. No product code changed. |
| 2 | VERIFIED | [Canonical Execution Contract v1](./JALORA-CANONICAL-EXECUTION-CONTRACT-v1.md) frozen after source review. It specifies multi-run retry linkage, per-step attempt journal, typed outcomes/thresholds, approval binding, exhaustive legacy mappings, additive schema/API compatibility, and tests. Explicit Stage 3 prerequisite: safely handle older unversioned DBs that `init_db()` might otherwise stamp as current. Documentation links/whitespace checked; no runtime/schema code changed. |
| 3 | VERIFIED | Added [canonical lifecycle contract types](../backend/app/services/execution_contract.py), [durable lifecycle service](../backend/app/services/execution_lifecycle.py), and additive [v1g migration](../backend/alembic/versions/v1g_canonical_execution_lifecycle.py). Task/run/step/approval/audit records now carry canonical linkage, stable plan/dependency identity, typed outcomes, approval references, retry/idempotency metadata, and attempt history. The central graph uses shared result decoding/invocation, persists before dispatch and after response, links audit and approval records, carries action identity through tool context, and blocks automatic replay of uncertain side effects. Legacy unversioned-schema preflight fails clearly instead of incorrectly stamping. Chat/local deterministic task creation is linked. Validation: `pytest tests/test_migrations.py tests/test_execution_lifecycle.py tests/test_agent_phase_b.py tests/test_agentic_hardening.py tests/test_chat.py tests/test_approvals.py -q` — **143 passed**; modified Python files compile; Alembic has one head (`v1g_canonical_execution_lifecycle`). No live provider idempotency/reconciliation or authenticated shared-localhost verification; proceed to Stage 4 for cross-interface approval/revocation/recovery unification. |
| 4 | VERIFIED | Shared approval lifecycle, edit/version binding, expiry/revocation/cancellation, policy re-checks, and uncertain-outcome handling are implemented. Evidence and limits are documented in the Stage 4 report above; focused backend and Chromium tests passed. |
| 5 | VERIFIED | Saved workflow child capabilities execute through the central graph and durable lifecycle; workflow pause/resume, restart recovery, cancellation, skip/reject, typed dependencies, and legacy compatibility are covered by the Stage 5 tests above. Live email provider acceptance remains unverified. |
| 6 | VERIFIED | In-app, Twilio, and Telnyx voice paths link to the central chat/task/run/approval lifecycle. The seven-suite focused regression run passed **108 tests** and the React production build passed. Live providers and authenticated shared-localhost UI remain unverified; voice-specific incremental token-to-TTS/latency behavior is a follow-up. |
| 7 | PARTIALLY VERIFIED / WAIVED FOR SEQUENCING | Email, calendar, document, paired-device message-assistance, and workspace file/import/command and project create/rename/archive actions use the canonical lifecycle. Existing evidence: integrated Stage 7 regression 169 passed; workspace/device suites 41 passed; authenticated shared-localhost Playwright flows 2 passed; React build and Python compileall passed. Additional local-device preparation: focused Stage 7/device regression 142 passed; isolated LAN test backend/frontend health and frontend proxy returned HTTP 200; browser pairing generated a LAN-reachable URL and granted only `remote.browser.open_url`. Latest email fix preserves structured direct-draft content and prevents email-body wording from being misclassified as a second task; the focused approval/draft/routing checks passed 111 tests, and the broader email/recovery/device/preferences regression batch passed 429 tests. Eight-recipient mock coverage confirms separate approvals and exactly one send per approved recipient; skip, reject, restart, and uncertain-outcome cases remain mock-only. No live provider send or delivery is claimed. Provider-sandbox and paired-device acceptance remain unverified; Stage 7 is not VERIFIED. See [local-device testing](../docs/LOCAL-DEVICE-TESTING.md). |
| 8 | VERIFIED / ISOLATED TEST BOUNDARY | Schedule+migration: 43 passed, including concurrent double-fire of a mock external action and subprocess restart recovery; isolated schedule UI E2E: 4 passed; React build and Python compile passed. Shared backend health at port 8000 timed out, so shared-instance and live-provider acceptance are explicitly unverified. |
| 9 | VERIFIED / ISOLATED MOCK-PROVIDER/API TEST BOUNDARY | Copilot/Codex turns use the canonical task and approval lifecycle, operate on isolated staging copies, and expose previews without project writes. Workspace apply uses the shared save/approval path; restart/rejection, cancellation, and provider-preview behavior are covered. Focused fake-provider tests: 2 passed; combined workspace/approval/lifecycle/task regression: 68 passed. Compilation, focused Ruff checks, and React build passed. Live SDK/provider and shared-localhost UI acceptance remain unverified; no live provider requests were made. |
| 10 | PARTIALLY VERIFIED | Chat memory exists; cross-entrypoint context, governance, and measured chatbot quality need completion. |
| 11 | PARTIALLY VERIFIED | Many capability modules exist; live provider and end-to-end verification is uneven. |
| 12 | PARTIALLY VERIFIED / NATIVE DEVICE GATES BLOCKED | Windows and Android companion source clients and the versioned scoped protocol are implemented; the Windows client discovers classic registered desktop executables without automatically allowing them and verifies typed text without returning field contents. Mocks cover authorization, receipts, and two allowlisted Android apps. Successful device receipts resume remaining central-agent steps; duplicate-receipt regression verifies retry after interrupted continuation. The Android, remote-device, protocol, and agent-resume batch passed 38 tests; Windows companion tests passed 16. Native signing, Android build toolchain/device acceptance, and physical Windows/Android demonstrations remain blocked. No device action was performed in this validation. See [local-device testing](../docs/LOCAL-DEVICE-TESTING.md). |
| 13 | VERIFIED / ISOLATED LOCALHOST ACCEPTANCE | Canonical task details, safe evidence, approval state/edit/decision controls, and next-action guidance are wired from Tasks, notifications, schedules, Chat, and Workspace. Focused API regressions: 76 passed; authenticated localhost Playwright acceptance: 9 passed; React type-check/build passed. A later authenticated shared-localhost smoke chat received the exact requested response; no email or device action was sent. The shared app used `openai:gpt-4o-mini`; Ollama and LM Studio API endpoints also returned HTTP 200 after LM Studio server startup. Broader Playwright results are not clean: 44 passed/3 failed; the two chat failures and capability-catalog failure passed in isolation, while calendar event creation still fails in isolation and is unrelated to these changes. |
| 14 | PARTIALLY VERIFIED / DEPLOYMENT GATES BLOCKED | Added authenticated low-cardinality metrics, truthful database readiness, bounded Redis-outage limiting, timeout/circuit-breaker safeguards, production credential-key enforcement/rotation, SQLite backup/restore tooling, and an operations runbook. Earlier evidence: **92 focused tests**, **1,709 full backend tests passed, 7 skipped**. Latest full backend run found two email regressions (1,748 passed, 7 skipped); both were fixed and the complete relevant email/recovery/device/preferences batch passed **429 tests**. Frontend type-check/build passed. Docker/Ruff are unavailable locally. Independent security review, live backup/restore and rollback rehearsal, production load/concurrency thresholds, off-site retention, account-wide privacy export/deletion policy, provider/device support acceptance, and production database support remain unverified. |
| 15 | NOT STARTED | Release-level cross-entrypoint acceptance has not been demonstrated. |

### Stage 13 milestone report

- **Status before/after:** PARTIALLY VERIFIED → VERIFIED for the Stage 13 UX scope using isolated localhost acceptance.
- **Behavior:** `GET /api/tasks/{task_id}` now returns owner-scoped canonical step outcomes, capability/dependency metadata, redacted safe summaries/evidence, and safe approval/decision metadata. Run lookup is user-scoped. The task center loads details on demand, explains next actions, exposes only valid controls, and prevents approving unsaved approval edits.
- **Entry points:** notification and schedule navigation resolve to the canonical task; Chat and Workspace approvals link to that task. Chat also offers the link inside its modal, where the banner link is covered. Workspace approval banners show the permission level, request arguments (credentials remain hidden), and provider/device scope note before decision controls.
- **Changed files:** `backend/app/api/tasks.py`, `backend/tests/test_tasks.py`; `frontend-react/src/components/TaskExecutionDetails.tsx`, `frontend-react/src/components/NotificationBell.tsx`, `frontend-react/src/views/TasksView.tsx`, `frontend-react/src/views/ChatView.tsx`, `frontend-react/src/views/WorkspaceView.tsx`, `frontend-react/src/App.tsx`, `frontend-react/src/lib/types.ts`, `frontend-react/src/lib/i18n.ts`, `frontend-react/src/styles.css`; `frontend-react/e2e/tasks.spec.ts`, `frontend-react/e2e/approval.spec.ts`, and `frontend-react/e2e/regression.spec.ts`.
- **Validation:** `python -m pytest tests\\test_tasks.py tests\\test_approvals.py tests\\test_execution_lifecycle.py tests\\test_phase_g_schedules.py -q` — **76 passed**. Focused authenticated localhost Playwright covering notification/evidence/edit/approval, Chat approval navigation, Workspace approval context/navigation, and schedules — **9 passed**. `npm run build` passed (TypeScript plus Vite); Pylance reported no syntax errors in `backend/app/api/tasks.py`.
- **Observed test instability:** one task-creation Playwright case timed out while navigating in a broader run; it passed when rerun alone. The final focused 9-scenario browser run passed.
- **Limitations:** browser checks used the isolated E2E SQLite/test setup on ports 8013/5183; existing shared development ports 8000/5173 were left untouched. No live provider or native-device action was performed. The build still reports the existing mixed static/dynamic import and large-chunk warnings.
- **Next:** Stage 14 security, privacy, reliability, and operations foundations. Stage 7 remains waived for sequencing and Stage 12 remains blocked on native companion/device gates; this report does not change those statuses.

### Stage 14 milestone report

- **Status:** IN PROGRESS → PARTIALLY VERIFIED. Local implementation and focused regression coverage are complete for the verified items below; Stage 14 is not complete because required production and external acceptance gates remain blocked.
- **Behavior and implementation:** `/api/metrics` is authenticated and uses route templates/fixed unmatched-path labels; `/api/health` reports database failure as HTTP 503. The global limiter now uses persistent bounded in-memory fallback when Redis fails, including through the HTTP middleware. Async timeouts and exclusive circuit-breaker recovery probes are covered, and invalid retry/breaker bounds are rejected. Production requires a dedicated valid Fernet credential key separate from the JWT secret; an atomic credential re-encryption command and validated SQLite online backup/restore commands are available. Redis is not host-published, the app waits for Redis health, and CI installs the timeout plugin it uses rather than silently skipping browser acceptance.
- **Operational documentation:** [Operations runbook](../docs/OPERATIONS-RUNBOOK.md) covers secret setup/rotation, offline backup/restore, migration and image rollback, health/metrics/limits, and incident response. It explicitly documents unsupported SQLite alternatives and unverified production gates.
- **Changed files:** `.github/workflows/ci.yml`, `Dockerfile`, `docker-compose.yml`, `backend/.env.example`, `backend/app/api/system.py`, `backend/app/config.py`, `backend/app/main.py`, `backend/app/services/rate_limiter.py`, `backend/app/services/reliability.py`, `backend/app/services/credential_rotation.py`, `backend/app/services/sqlite_backup.py`, `backend/tests/test_metrics.py`, `backend/tests/test_rate_limit_phase_l.py`, `backend/tests/test_reliability.py`, `backend/tests/test_health.py`, `backend/tests/test_production_config.py`, `backend/tests/test_credential_rotation.py`, `backend/tests/test_sqlite_backup.py`, `backend/tests/test_chat.py`, `scripts/rotate_credential_key.py`, `scripts/sqlite_database.py`, and `docs/OPERATIONS-RUNBOOK.md`. Regression fixtures/assertions were also brought in line with persisted lifecycle and approval-first behavior in `backend/tests/test_model_failure_recovery.py` and `backend/tests/test_phase_j_sandbox.py`.
- **Focused validation:** the combined security/reliability/recovery batch passed **92 tests** across metrics, rate limiting, reliability, health, production config, credential rotation, SQLite backups, auth, audit, tracing, migrations, CORS, and readiness gaps. The model recovery module passed **24 tests**. PowerShell syntax checks passed for the isolated LAN start/stop scripts. Pylance syntax checks found no syntax errors in changed Python files; Compose and CI YAML parsed successfully.
- **Broader validation:** the first full backend run found three stale workspace tests that expected file writes/rollback to bypass approval. Those tests now assert that no write happens before approval and verify the completed action afterward. Final `python -m pytest -q`: **1,709 passed, 7 skipped, 14 warnings**. Docker and Ruff are unavailable in this environment, so image build, container-volume restore, Ruff, and CI Playwright execution were not verified.
- **Unverified/blocked gates:** no independent threat-model/security review; no production database, multi-worker load/concurrent-approval threshold, migration rollback, or live container restore rehearsal; no operator-owned off-site backup/retention drill; no account-wide export/deletion or audit-retention policy; and no external provider/native-device/support-matrix acceptance. These remain exit criteria, not implied successes.
- **Next:** obtain evidence and owners for those blocked gates before marking Stage 14 VERIFIED or beginning Stage 15. Stage 7 remains waived for sequencing and Stage 12 remains blocked on native companion/device gates.

## Milestone report template

For each completed slice, append/update a concise record:

```text
Stage and item:
Status before/after:
Source evidence and exact changed files:
Behavior added/fixed:
Focused tests and result:
Broader regression/build/E2E result:
Provider/device/shared-localhost evidence:
Known limitations or blocked verification:
Remaining acceptance criteria:
Next numbered item:
```

**Completion definition:** this roadmap is complete only when all applicable exit gates are verified for the declared product support matrix. Unsupported operating-system/provider capabilities remain clearly labeled as restricted rather than represented as completed.
