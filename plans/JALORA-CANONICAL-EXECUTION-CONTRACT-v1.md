# JALORA Canonical Execution Contract v1

**Status:** Finalized v1 design for roadmap Stage 2.
**Authority:** Stage 2 of the [master roadmap](./JALORA-MASTER-IMPLEMENTATION-ROADMAP.md).
**Evidence baseline:** [Stage 1 architecture inventory](./JALORA-STAGE-1-ARCHITECTURE-INVENTORY.md).
**Scope:** define the shared task/run/step/action/approval/result semantics and additive schema decision. This document does not implement runtime changes; schema changes require the separate implementation/test gate in Stage 3.

## 1. Design decisions

1. `Task` is the durable user-request/trigger record. `AgentRun` is one execution attempt/checkpoint for that task. A task may have multiple sequential runs for explicit retry/recovery; `AgentRun.task_id` and `attempt_number` are authoritative in v1. Existing `Task.run_id` remains a compatibility pointer to the current/latest run.
2. `TaskStep.id` is the stable `step_id`. A step represents exactly one independently invokable capability/tool call; whether it has an external side effect is capability metadata. Composite work is represented as a non-side-effect parent step and one child step per independently consequential effect (for example one recipient send per child).
3. `Approval` is a decision record bound to one exact task step and payload revision. Approval decision is distinct from the execution claim and action outcome.
4. `ActivityLog` remains the audit/history record. A dedicated `TaskStepAttempt` row is the durable per-attempt execution record. It is created before dispatch and finalized with the response or `outcome_unknown`; audit rows reference the attempt and preserve the operator-visible event trail. `TaskStep` holds the current canonical result.
5. LangGraph state is a resume checkpoint, not proof that an external effect happened. Persisted step/attempt evidence and provider reconciliation are authoritative for side-effect outcomes.
6. Preserve current ORM IDs, existing database values, legacy HTTP response shapes, and old status strings during migration. Add versioned adapters first; do not rename, drop, or reinterpret fields in place.

## 2. Versioned typed contract

The implementation should define these as Python enums and Pydantic models in a shared contract module. Names below are normative; database and legacy-wire adapters translate them as described below.

Existing Task/Run/Step/Approval primary keys remain opaque stable strings in their current database format (currently UUID hex, 32 characters). New foreign keys must use those same values; do not generate a second identifier for the same record.

```text
contract_version = "1.0"

TaskLifecycle =
  queued | running | awaiting_approval | paused | partial |
  completed | failed | stopped | cancelled

StepLifecycle =
  pending | ready | running | awaiting_approval | paused |
  completed | failed | skipped | cancelled

ActionOutcome =
  planned | prepared | awaiting_approval | approved | submitted | succeeded |
  provider_accepted | delivered | verified | failed | outcome_unknown |
  rejected | skipped | cancelled

VerificationStatus =
  not_applicable | pending | verified | failed | unavailable

PolicyDecision =
  allow | notify | confirm | deny

ApprovalDecision =
  pending | approved | rejected | skipped | expired | revoked | cancelled

ApprovalClaim =
  unclaimed | claimed | finished

AttemptState =
  prepared | claimed | dispatching | response_received | reconciling |
  completed | failed | outcome_unknown | cancelled
```

### Execution envelope

Every canonical task has a validated envelope:

```json
{
  "contract_version": "1.0",
  "task_id": "stable task identifier",
  "goal": "human-readable requested outcome",
  "input": {},
  "target": null,
  "owner_id": "user identifier",
  "conversation_id": null,
  "trigger": {
    "kind": "chat|voice|workflow|email|schedule|workspace|direct|device",
    "source_id": null,
    "occurrence_key": null
  },
  "status": "queued",
  "created_at": "UTC timestamp",
  "updated_at": "UTC timestamp"
}
```

`input` is structured and validated for its entry point; `goal` remains the display summary. `target` is a validated domain reference (for example a calendar/account/device ID), not a permission grant or secret. Trigger data identifies the source, not a permission grant. Do not copy credentials, access tokens, or other secrets into task input, plans, model state, audit metadata, or user-facing summaries.

### Run envelope

`run_id` identifies one durable attempt to execute a task. A run records `task_id`, monotonically increasing `attempt_number`, model/executor metadata, lifecycle state, start/end timestamps, and a versioned checkpoint. The new `AgentRun.task_id` link is authoritative; `Task.run_id` remains the latest/current-run compatibility pointer. The database step/attempt records—not a serialized model transcript—are authoritative for completed work. Resume of an interrupted run continues/reconciles that run; a deliberate retry after a terminal failure adds a new run under the same task and does not replay known-complete side-effect steps.

### Step envelope

Each canonical step has:

- Stable `step_id` (`TaskStep.id`), `task_id`, display order, description, dependency **IDs**, capability/tool name and version, and validated arguments.
- The policy decision and policy version used at dispatch, plus the capability-validated set of outcomes that satisfy the requested step.
- Lifecycle (`StepLifecycle`) separate from the action result (`ActionOutcome`).
- `started_at`, `completed_at`, latest attempt number, stable logical action ID, and a typed current result/error record.
- A typed result containing `action_id` (equal to `step_id` for a single effect), outcome, error code, output, evidence references, verification status, provider/target receipt, safe summary, and timestamps.
- An approval reference when required.
- A `TaskStepAttempt` history of every dispatch/reconciliation attempt, including the claim state, payload digest, provider idempotency key when supported, exact outcome, and timestamps.

Dependencies must be stable step IDs in v1. The existing integer/index dependency field remains readable and writable by legacy adapters during transition; the canonical dependency list is the new ID-based field. Resolve old indices against the persisted plan once, persist the mapping, and never recalculate an old dependency from a reordered plan.

`TaskStep.id` is generated once when a validated plan is materialized. `action_id` is a stable opaque ID for the logical side effect; it is not the model/provider `tool_call_id`, which may change on resume. Retain the tool call ID on the attempt only for correlation. Compute payload digests as SHA-256 over deterministic JSON serialization of validated, redacted arguments; exclude secrets. Provider idempotency keys are opaque and persisted before dispatch.

### Result envelope

```json
{
  "contract_version": "1.0",
  "task_id": "task id",
  "run_id": "run id",
  "step_id": "stable TaskStep id",
  "action_id": "stable logical side-effect id",
  "approval_id": null,
  "attempt": 1,
  "lifecycle": "completed",
  "outcome": "provider_accepted",
  "verification": "pending",
  "error": null,
  "output": {},
  "evidence": [],
  "provider_receipt": {
    "provider": "provider name",
    "receipt_id": "provider identifier",
    "status": "accepted"
  },
  "safe_summary": "The provider accepted the request; delivery is not confirmed.",
  "started_at": "UTC timestamp",
  "finished_at": "UTC timestamp"
}
```

All receipt fields are optional, provider-specific, and scrubbed of secrets and unnecessary personal data. Evidence references identify what supports a claim (provider response, callback, target acknowledgement, local verification); they do not themselves imply a stronger outcome than the underlying evidence supports.

## 3. Lifecycle semantics

### Task/run lifecycle

| Canonical lifecycle | Meaning | Completion/reporting rule |
|---|---|---|
| `queued` | Persisted and awaiting execution. | No action has started. |
| `running` | Executor is actively progressing. | Do not describe the overall request as done. |
| `awaiting_approval` | At least one required human decision is pending. | No protected action may dispatch. |
| `paused` | Waiting for a non-approval condition or safe reconciliation/resume. | Preserve completed steps; do not infer remaining work succeeded. |
| `partial` | Some requested work is complete, but at least one requested outcome failed or remains unknown. | Report each known and unknown outcome explicitly. |
| `completed` | Orchestration finished and all required outcomes reached their declared completion threshold. | A submitted or provider-accepted action is not described as delivered or verified. |
| `failed` | The task cannot proceed and required work failed. | Include safe error code/summary and completed work. |
| `stopped` | Execution was stopped without asserting that external in-flight effects were undone. | Any in-flight effect lacking a receipt becomes `outcome_unknown`. |
| `cancelled` | Cancellation was accepted before dispatch, or all in-flight actions were reconciled/cancelled. | Cancellation is not proof of rollback after provider acceptance. |

When there is an unresolved external outcome, the task/run must not be reported as successfully `completed`. Use `paused` while reconciliation can resume, or `partial` if the request has ended with an unresolved/failed component.

Transitions are validated in the service layer. Tasks may move from `queued` to `running`, `cancelled`, or `failed`; from `running` to `awaiting_approval`, `paused`, `partial`, `completed`, `failed`, `stopped`, or `cancelled`; and from `awaiting_approval`, `paused`, `stopped`, `partial`, or `failed` back to `running` only through an explicit resume/retry that creates a new `AgentRun` attempt where appropriate. `completed` and `cancelled` are terminal in v1. A retry preserves the task and completed steps but never repeats an action with an unresolved `outcome_unknown`. Step retries require explicit safe-retry evidence and are separately audited; they never erase prior attempts.

Each step declares a capability-validated `completion_outcomes` set during planning. A read-only/computation step normally allows only `succeeded`; a consequential action requires the outcome(s) that satisfy the explicit request and provider contract. There is no global ordering between `delivered` and `verified`; when a user explicitly requests delivery confirmation or a verified postcondition, the acceptable set must require that evidence. The model cannot weaken the threshold by proposing `failed` or `outcome_unknown`. `TaskStep.lifecycle=completed` means the actual outcome is in its declared set; the result still retains the actual `ActionOutcome`, and user-facing text must name that outcome accurately.

### Action outcome semantics

| Outcome | Minimum evidence/meaning |
|---|---|
| `planned` | Intent only; no external call prepared. |
| `prepared` | Validated action/payload exists; no dispatch. |
| `awaiting_approval` | Required approval is pending; no dispatch. |
| `approved` | A valid approval decision is bound to the exact payload revision; this is permission, not execution. |
| `submitted` | Dispatch was attempted/accepted by the local adapter, but provider acceptance is not confirmed. |
| `succeeded` | A non-external computation/read-only capability completed successfully. Does not imply an external effect or delivery. |
| `provider_accepted` | Provider/target returned evidence it accepted the request. Not proof it delivered or completed it. |
| `delivered` | Provider/target reports delivery. State the source and its limits. |
| `verified` | The requested postcondition was independently observed and evidence is recorded. |
| `failed` | A definitive failure is known; retain provider/error evidence. |
| `outcome_unknown` | Dispatch may have happened but available evidence cannot establish success or failure. Blocks automatic retry. |
| `rejected` / `skipped` | User denied or explicitly skipped the action. |
| `cancelled` | Action was prevented or provider-confirmed cancelled; never imply a sent effect was retracted without evidence. |

`ToolResult.success` is not a universal mapping to `succeeded`, `verified`, `delivered`, or even `provider_accepted`. Translate it using capability-specific metadata and evidence. Existing email receipts can support `provider_accepted`; a delivery callback is needed for `delivered`; a verified postcondition is needed for `verified`.

### Approval decision and execution claim

Approval v1 separates:

- **Decision:** `pending`, `approved`, `rejected`, `skipped`, `expired`, `revoked`, or `cancelled`.
- **Claim:** `unclaimed`, `claimed`, or `finished`, changed atomically so concurrent interfaces cannot dispatch the same approved step twice.
- **Payload binding:** `task_id`, `step_id`, action ID, payload version, and digest of the exact validated arguments approved.

Edits create a new payload revision and invalidate any prior approval of a different digest. Revision snapshots must not contain secret material: credentials remain in the encrypted credential store and a payload may refer only to an opaque credential reference/version. Revocation/expiry/cancellation prevents an unclaimed action from dispatching. If dispatch already began, the system reconciles the action instead of pretending the provider effect was revoked. `Approval` does not carry the terminal action outcome; the step result does.

Legacy approval status translation:

| Existing `Approval.status` | v1 decision | v1 claim/action interpretation |
|---|---|---|
| `pending` | `pending` | `unclaimed`; action is `awaiting_approval`. |
| `sending` | `approved` | `claimed`; dispatch is in flight. If the result is lost, action becomes `outcome_unknown`. |
| `approved` | `approved` | `finished`; read the linked step result for the actual action outcome. |
| `rejected` | `rejected` | `finished`; action is `rejected`. |
| `skipped` | `skipped` | `finished`; action is `skipped`. |
| `failed` | `approved` | `finished`; action is `failed`; approval itself was not the failure. |
| `outcome_unknown` | `approved` | `finished`; action is `outcome_unknown`, so automatic redispatch is blocked. |

Unknown/unrecognized legacy approval values must be surfaced for reconciliation and must not be treated as approved for dispatch.

`TaskStepAttempt.state` transitions are `prepared` → `claimed` → `dispatching` → `response_received` → `completed|failed`; a lost response after dispatch transitions to `reconciling` and then to a provider-evidenced terminal result or `outcome_unknown`. Pre-dispatch attempts may become `cancelled`; a dispatched attempt cannot be marked cancelled without provider/target confirmation. Transitions use conditional updates/uniqueness constraints so a restart or competing worker cannot acquire an already-claimed attempt.

## 4. Durable action and recovery rules

1. Persist the task, run/checkpoint, step, validated payload digest, and idempotency key before dispatch.
2. Atomically claim the step/approval and persist an attempt record before making the external call. A claim conflict means another executor owns it; it is not permission to dispatch again.
3. Persist provider acceptance/receipt and the typed result before advancing dependants or reporting success.
4. Reuse the same idempotency key for retries of the same logical action when the provider supports idempotency. New logical actions receive new keys. Keys are opaque and contain no user data.
5. If the provider lacks idempotency, reconcile by provider receipt/query or target state before retrying. A timeout, process death, connection reset, or missing response after dispatch is `outcome_unknown`, not a retryable failure.
6. Unknown actions are not automatically redispatched. Retry is allowed only after positive evidence the previous attempt did not take effect, or a provider guarantees deduplication for the same key. Otherwise require an explicit resolution flow that warns the user of duplicate risk.
7. On restart, rehydrate approvals and step states from the database. A step with no recorded dispatch attempt may resume under policy; a started attempt without a recorded result is reconciled first. Completed steps remain immutable to ordinary plan refreshes.
8. Cancellation prevents not-yet-dispatched steps. An in-flight action requires provider reconciliation; do not mark it cancelled merely because the local coroutine was cancelled.
9. Record each attempt and reconciliation in the audit trail linked to task/step/action IDs. Never log secrets or claim success from a tool message alone.

## 5. Existing-field mapping

| Current storage/API field | v1 mapping | Compatibility rule |
|---|---|---|
| `Task.id` | `task_id` | Keep value and key format. |
| `Task.run_id` | Current/latest run pointer | Preserve the existing unique constraint and nullability for legacy rows; `AgentRun.task_id` becomes the authoritative run history link. |
| `Task.goal` | `goal` | Keep as display text; add structured input rather than encoding JSON in this field. |
| `Task.plan` | Legacy plan/checkpoint view | Preserve for old UI and graph resume; canonical step rows become the durable execution list. |
| `Task.status` | `TaskLifecycle` adapter | `waiting_for_approval` maps to `awaiting_approval`; all other known values map directly. Preserve raw values in old API responses. |
| `Task.current_step`, `progress` | Derived presentation hints | Not authority for dependency completion or side-effect success. |
| `Task.error`, `result` | Legacy summary | Preserve; canonical typed results live per step. Do not overwrite step evidence with generated prose. |
| `TaskStep.id` | `step_id`, and `action_id` for a single consequential effect | Keep stable; never regenerate or delete completed steps during a plan refresh. |
| `TaskStep.idx` | Legacy display order | Keep unique `(task_id, idx)`; it is not identity. |
| `TaskStep.dependencies` | Legacy dependency indices | Keep for legacy reads/writes; migrate to persisted dependency IDs. |
| `TaskStep.tool_id`, `arguments` | Capability/tool and validated input | Preserve keys/serialization during rollout; never store secrets in arguments. |
| `TaskStep.status` | `StepLifecycle` adapter | `active`→`running`, `done`→`completed`; `pending`, `failed`, `skipped` map directly. Preserve old API values until consumers migrate. |
| `TaskStep.detail` | Legacy human-readable detail | Keep as summary only; not a typed receipt/evidence record. |
| `AgentRun.id`, `state_json`, `status` | `run_id`, `task_id`, attempt number, checkpoint, and run lifecycle | Add task link/attempt number for v1 rows; keep graph-specific checkpoint for resume; DB step/attempt outcomes override checkpoint claims. `paused_approval` maps to `awaiting_approval`; `error` maps to `failed`. |
| `Approval.id`, `run_id`, `plan_step_id` | `approval_id`, legacy run/plan-step links | Keep all existing fields; add canonical task/step links. Preserve opaque historical `plan_step_id` values. |
| `Approval.arguments` | Current payload revision | Keep for existing consumers; bind with version/digest and retain edit history before accepting edited approvals. |
| `Approval.status` | Legacy combined decision/claim view | Keep `pending`/`sending`/terminal values for old APIs. New internal decision and claim fields disambiguate these states. |
| `Approval.result` | Legacy receipt/result view | Preserve as a compatibility projection of canonical step result; do not treat it as the sole source of truth. |
| `ActivityLog.id`, `run_id`, `tool`, `arguments`, `success`, `meta` | Existing audit fields | Keep; add nullable canonical task/step/action/attempt linkage and typed outcome. Existing rows remain valid; this table is not the authoritative attempt state. |
| `ToolResult(success,message,data,error)` | Capability adapter input | Preserve public shape. Decode `data` into the versioned result and choose outcome only from capability evidence, not the boolean alone. |
| `ChatOutcome` / API response | Existing chat wire contract | Preserve existing fields/status strings. Add an optional versioned execution/result block only after frontend/API compatibility tests; no required-field change in v1. |
| `TaskOut`, `TaskDetailOut`, `/api/tasks` dictionaries | Legacy task wire contracts | Keep field names, status values, and omission/null behavior. Add a separate versioned representation or optional additive fields; do not silently make old clients consume new enums. |
| `WorkflowExecutionResult`, `Call`, `Schedule`, `DeviceRemoteCommand` | Capability/domain-specific records | Keep their public contracts; link each canonical execution to task/run/step and translate domain results into the v1 envelope. Domain status remains distinct from task/action outcome. |

### Complete legacy status mapping

The canonical enums are internal v1 values; the adapter preserves the original raw status for all existing APIs. Values outside the listed source vocabularies are not coerced into success.

| Record | Legacy status | Canonical value |
|---|---|---|
| Task | `queued` | `queued` |
| Task | `running` | `running` |
| Task | `waiting_for_approval` | `awaiting_approval` |
| Task | `paused` | `paused` |
| Task | `partial` | `partial` |
| Task | `completed` | `completed` |
| Task | `failed` | `failed` |
| Task | `stopped` | `stopped` |
| Task | `cancelled` | `cancelled` |
| Task step | `pending` | `pending` |
| Task step | `active` | `running` |
| Task step | `done` | `completed` |
| Task step | `failed` | `failed` |
| Task step | `skipped` | `skipped` |
| Agent run | `running` | `running` |
| Agent run | `completed` | `completed` |
| Agent run | `failed` or `error` | `failed` |
| Agent run | `partial` | `partial` |
| Agent run | `paused_approval` | `awaiting_approval` |
| Agent run | `paused` | `paused` |
| Agent run | `stopped` | `stopped` |
| Approval | `pending`, `sending`, `approved`, `rejected`, `skipped`, `failed`, `outcome_unknown` | Use the decision/claim/action interpretation table above; never treat `failed` or `outcome_unknown` as permission to dispatch. |
| Workflow execution | Recognized engine statuses are translated only when exact step/result evidence supports them; otherwise expose `legacy_unmapped` and block consequential resume. | No inference from `success` boolean alone. |
| Call/schedule/device command | Keep capability-specific state in the domain record and map only evidenced external result to `ActionOutcome`. | For example, an opened browser URL does not imply a verified task outcome. |

## 6. Additive persistence decision

### Decision: additive schema support is required before broad adapter migration

The Stage 1 source audit confirms that current tables have stable IDs and useful JSON fields, but there is no durable structured task input/trigger, stable dependency-ID list, typed per-step result/evidence, attempt/idempotency record, or consistent task/step linkage on approvals and audit rows. Reusing `state_json`, `detail`, or arbitrary `meta` alone would leave recovery dependent on graph/provider-specific parsing and would not support reliable cross-interface status queries.

Use the existing tables and add the following nullable/defaulted fields plus narrowly scoped revision/attempt history first; do not add a parallel task/run/approval manager:

| Table | Additive v1 fields | Purpose |
|---|---|---|
| `tasks` | `contract_version` (legacy rows set to `0`), `input_data` JSON, `target_data` JSON, `trigger_data` JSON, nullable unique `trigger_key` | Validated request input/target, source/occurrence identity, and duplicate schedule-trigger prevention. |
| `agent_runs` | nullable legacy-compatible `task_id` FK, `attempt_number`, `started_at`, `completed_at`, unique `(task_id, attempt_number)` for non-null task links | Durable run history for retries. Keep `Task.run_id` as the current/latest-run compatibility pointer; backfill only exact run links. |
| `task_steps` | `dependency_step_ids` JSON, `capability`, `tool_version`, `policy_decision`, `policy_version`, `completion_outcomes` JSON, `action_id` nullable unique, `idempotency_key` nullable, `result_data` JSON, `outcome_status`, `verification_status`, `error_code`, `started_at`, `completed_at` | Durable stable dependency, capability/version, policy decision, completion threshold, logical action/idempotency identity, and current action result. Preserve current index dependencies and fields. |
| New `task_step_attempts` | attempt ID, step FK, run FK, attempt number, transient tool-call ID, claim/dispatch status, payload digest, provider idempotency key snapshot (nullable), result/outcome/evidence/receipt JSON, error code, timestamps; unique `(step_id, attempt_number)` | Immutable per-attempt recovery journal. Create/commit before dispatch; finalize after result or unknown outcome. Do not overload generic audit logs as the action state machine. |
| `approvals` | nullable `task_id` and `task_step_id` FKs, `decision_status`, `claim_status`, `payload_version`, `payload_digest`, `claimed_at`, `expires_at`, `revoked_at`, `decision_actor_id` | Exact lifecycle linkage, decision actor/timing, and separate human decision from atomic dispatch claim. Keep `run_id`, `plan_step_id`, `status`, and `result` during compatibility rollout. |
| `activity_logs` | nullable `task_id`, `task_step_id`, `action_id`, `attempt_id`, `outcome_status` | Queryable audit linkage without parsing arbitrary JSON. Keep current `run_id`/`meta`; attempt journal remains authoritative for recovery. |
| New `approval_revisions` | approval FK, version, validated arguments JSON, digest, editor/user ID, timestamp | Immutable history for edits; approval binds to one revision. Credentials stay in the encrypted credential store and revisions use opaque references only. Apply existing ownership/access controls, configured data-retention/deletion rules, and secret redaction. |

All new links are nullable for historical rows and adapters not yet migrated. Backfill only where the relationship is provable (for example task by exact run ID, or step by an exact persisted plan mapping). Leave ambiguous historical links null and label them legacy; never guess from timestamps or descriptions. Existing identifiers and records are not rewritten or deleted.

The unique idempotency/trigger constraints apply to non-null v1 keys. A scheduled `trigger_key` is a deterministic digest of the schedule ID and scheduled occurrence instant; it contains no user text or other PII. Before adding unique indexes, migration tests must detect and report existing collisions; do not silently rewrite keys. SQLite is the local/test default and PostgreSQL is also represented in deployment configuration, so the migration must be tested on each supported deployment dialect. If nullable-unique semantics differ, implement an equivalent filtered/partial index through a dialect-aware migration.

**Legacy database adoption is a migration blocker to resolve before deployment.** Current `init_db()` stamps an existing database with no `alembic_version` as the current head. Therefore a pre-existing, unversioned database created by an older model schema could skip the new additive migration and silently lack the v1 columns/tables. Stage 3 must add an explicit schema-signature/preflight path: stamp as current only when the legacy schema is verified compatible; otherwise run a deterministic, resumable adoption migration or fail startup with a clear remediation error. It must not infer the schema version solely from table existence. Extend `test_migrations.py` with an actual older `create_all`/snapshot schema that is missing v1 fields and assert safe upgrade or explicit refusal before any v1 task is accepted.

The observed adoption behavior is in [`init_db()`](../backend/app/database/session.py); existing [`test_migrations.py`](../backend/tests/test_migrations.py) only builds an unversioned database from current ORM metadata and therefore does not cover this older-schema case.

### Migration and rollout order

1. Implement and verify the unversioned-legacy database preflight/adoption rule. Then add nullable/defaulted columns and the approval revision/attempt tables; keep all old columns and routes.
2. Deploy dual-read/dual-write adapters. New v1 task creation writes both old fields and new structured fields; old clients still read current shapes. Every new `AgentRun` is linked to its task and numbered; update `Task.run_id` to the current run.
3. Backfill only unambiguous rows in a resumable/idempotent migration command. Report counts for linked, legacy-unlinked, and conflicting rows.
4. Enforce v1 linkage and typed validation for newly created canonical executions; legacy entry paths remain explicitly marked until migrated.
5. Add compatibility tests before changing any frontend or old response shape. No destructive migration, field rename, status rewrite, or removal of legacy reads is in scope for v1.

## 7. Legacy HTTP/API compatibility strategy

- Existing endpoints and their current response keys remain v0-compatible. In particular, `/api/tasks` and `/api/tasks/{task_id}` keep their current dictionaries, and `TaskOut`/`TaskDetailOut`, `ApprovalOut`, `ActivityOut`, and `ChatOutcome.as_response()` do not lose or rename fields.
- Add `contract_version: "1.0"` and a distinct optional `execution` object (or a new versioned endpoint) for canonical fields. Existing status fields retain their legacy strings; the v1 object uses canonical enums.
- Keep Pydantic validation strict for v1 request/result models, but do not validate historical raw JSON as if it were already v1. Use explicit `legacy` adapters and report malformed legacy state rather than silently defaulting a consequential outcome.
- Existing clients may ignore the additive object. No client should be required to migrate until the cross-interface acceptance suite proves parity.
- Never return provider receipts or arguments to another user. Reuse existing owner scoping and redact secret-bearing inputs from both old and new representations.

## 8. Stage 2 compatibility and acceptance test plan

These are required tests for implementing the contract in later stages; Stage 2 itself is documentation-only.

| Test group | Required assertions |
|---|---|
| Contract model | Valid v1 task/step/result/approval parses; unknown enum, malformed IDs, non-JSON-safe data, unsafe receipt fields, and invalid state transitions fail explicitly. |
| Status adapters | Every legacy Task/TaskStep/AgentRun/Approval status maps to one canonical value and round-trips to the unchanged legacy API value; unknown values are surfaced as legacy/unsupported, never success. |
| Schema migration | Upgrade on empty and populated SQLite/PostgreSQL DBs; test tracked and old unversioned schemas; all prior rows/IDs/response fields preserved; null legacy links accepted; downgrade does not run destructively in production. |
| Backfill | Exact links populate; ambiguous plans remain unlinked; rerunning is idempotent; duplicate trigger/idempotency keys are reported without mutation. |
| Approval binding | Edit increments payload version and digest; old approval cannot dispatch edited arguments; concurrent claim has one winner; reject/expire/revoke/cancel block unclaimed dispatch. |
| Attempt/recovery | Persist-before-dispatch crash resumes safely; provider accepted receipt survives restart; timeout after dispatch becomes unknown; unknown never auto-retries; supported idempotency reuses the same provider key; prior attempt records remain immutable. |
| Outcome truthfulness | Submitted is not reported as provider accepted; accepted is not delivered; delivery is not verified without the declared postcondition; unknown cannot produce successful task completion. |
| Legacy API | Snapshot the current `/api/tasks`, task detail, approval, activity, chat success/pause/error response shapes; v1 additions are optional and owner-scoped. |
| Cross-entrypoint | Chat, workflow, voice, email, scheduler, workspace adapters and direct actions produce the same IDs/status/result semantics once migrated; existing provider/domain behavior remains covered. |

## 9. Explicitly deferred

- This contract does not unify the executor, move voice/workflows/email/workspace adapters, alter policy thresholds, add live providers, change frontend views, or implement native device control. Those are later numbered stages.
- It does not claim delivery, external-provider, shared-localhost, multi-worker, or real-device acceptance. Those require the corresponding later tests and environments.
- It does not permit automatic retry of any `outcome_unknown` external action.
- It does not authorize implementation against a materially different interpretation of this contract. Resolve review comments here first; then implement schema/type and legacy-adapter tests before migrating entry points.
