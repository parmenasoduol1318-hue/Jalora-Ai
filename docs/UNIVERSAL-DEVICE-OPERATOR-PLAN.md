# Jalora Universal Device Operator: audit and delivery plan

## Architecture audit

### Existing reusable foundations

- **One central agent:** LangGraph in `backend/app/agents/graph.py` uses the
  shared tool registry, policy checks, task/run state, approvals, and recovery.
  Device actions should remain tools in this flow; a companion should execute
  bounded commands, not plan tasks or make independent decisions.
- **Task lifecycle:** `Task`, `AgentRun`, `TaskStep`, and `Approval` already
  provide persisted progress, approval, cancellation, and recovery surfaces.
  External actions must continue through these shared mechanisms.
- **API-based application access:** Gmail, Calendar, Drive, contacts, and other
  integrations are implemented in provider services and registered tools.
  These should remain preferred over UI automation when the user's integration
  is connected and supports the requested action.
- **Browser and research tools:** Public web research and cloud-side page
  browsing are available to the agent. They do not operate inside a user's
  authenticated local browser session.
- **Windows controls:** `local_device_control.py` provides an allowlisted
  Windows executable launcher, process/window ownership checks, UI Automation,
  screenshot OCR, and bounded click/type/key actions. The API restricts it to
  loopback requests to a Windows-hosted backend; it cannot reach a user's
  desktop from Render.
- **Pairing and device authorization:** `DevicePairingRequest` uses short-lived
  high-entropy pairing codes stored as hashes; the target explicitly approves
  the pairing and receives an opaque bearer token whose digest is stored.
  Devices have scopes, an authenticated heartbeat, and revocation.
- **Current paired companion:** `RemoteDeviceCompanion.tsx` is a browser page.
  It can receive and separately approve requests to open public links. It
  cannot inventory installed apps, read notifications, inspect screens, or
  control native applications. Message endpoints exist for a future native
  client but are not proof that the current browser companion can deliver them.
- **Capability metadata:** The existing `/api/capabilities` catalog describes
  registered tool families, but it does not report which device or application
  is currently available to a particular user. This increment adds that
  user-scoped runtime inventory.

### Principal gaps and constraints

1. There is no resident Windows companion connected outbound to the cloud.
   Local Windows actions work only when the backend itself runs on that
   Windows computer.
2. There is no native Android application or Accessibility Service. The web
   companion does not inherit Android notification, screen, or UI permissions.
3. There is no generic remote UI command protocol, companion-reported app
   inventory, accessibility-tree transport, screenshot transport, or
   cross-device task checkpoint binding.
4. Existing UI actions are narrow and approval-gated; unsupported controls,
   app-specific restrictions, and API-vs-UI selection are not yet represented
   by a common live capability contract.
5. Third-party APIs, app authentication, MFA, application sandboxes, and OS
   permission prompts remain authoritative. No implementation should bypass
   them or claim a result without an observable receipt or screen-state change.

## Incremental implementation plan

### Phase 1 — Common capability contract and discovery (implemented)

- Provide a versioned, user-scoped inventory for paired devices, local Windows
  apps, connected cloud integrations, and registered cloud browser tools.
- Distinguish currently available, offline, permission-required,
  registered-but-not-executable, revoked, and unsupported operations.
- Expose the inventory through `GET /api/devices/capabilities` and a read-only
  central-agent tool so planning uses the same facts as the Devices UI.
- Keep current local/remote operation implementations and shared approval
  lifecycle; do not make unsupported operations appear available.

**Acceptance:** authenticated inventory is user-scoped, contains no device
secrets, reflects heartbeat/scope state, discloses the browser companion's
native limitations, and is callable through the shared tool registry.

### Phase 2 — Versioned outbound companion protocol

- Specify a bounded command envelope: protocol version, device ID, task/run and
  step IDs, operation, validated arguments, required scope, expiry, nonce,
  idempotency key, and expected verification evidence.
- Add durable command/receipt storage and explicit queued, delivered,
  running, succeeded, failed, uncertain, cancelled, and expired states.
- Use an authenticated outbound WebSocket or long-poll channel with
  short-lived access credentials, device identity binding, replay protection,
  heartbeat, reconnect, per-command scope checks, and immediate revocation.
- Keep targets private; devices initiate outbound TLS connections rather than
  exposing desktop/phone listeners to the Internet.
- Route requests through existing central tasks and approvals; prohibit raw
  shell execution and arbitrary code in the protocol.

**Acceptance:** protocol/schema tests cover authorization, expiry, duplicate
delivery, reconnect, revocation, invalid payloads, and truthful uncertain
outcomes before any native UI operation is enabled.

### Phase 3 — Windows companion

- Build a signed, user-visible Windows companion that connects outbound and
  reports a narrow, owner-approved capability manifest.
- Use Windows UI Automation first; use screenshots/OCR or an approved vision
  path only when accessibility data is insufficient and the user has granted
  screen access.
- Scope app launch and interaction to user-registered applications, foreground
  windows, and individually authorized operations. Require reinspection after
  state changes and stop on unexpected screens.
- Implement pause, stop, revocation, local status, and a visible action history.

**Acceptance:** disposable test applications verify launch, accessible
inspection, bounded input, screenshot handling, verification, cancellation,
network loss, and safe recovery. No real account or external action is used.

### Phase 4 — Android companion

- Build a native Android companion with explicit pairing, per-capability
  permission explanations, revocation, foreground status, and privacy controls.
- Use intents for app launch and an owner-enabled Accessibility Service only
  for operations Android permits. Request notification access separately;
  honor notification redaction, app restrictions, and background-execution
  limits.
- Never bypass credentials, MFA, secure windows, or application sandboxing.

**Acceptance:** emulator and physical-device tests cover permission denial,
grant, revocation, app launch, permitted node inspection/action, process
restart, offline reconnect, and app-specific unsupported states.

### Phase 5 — Verified multi-application workflows

- Have the central agent select API, cloud browser, Windows, or Android
  capabilities from the live manifest; combine them when useful.
- Store task/device/app/step IDs, safe state summaries, completed receipts,
  pending approvals, and recovery checkpoints in existing task/run records.
- Prefer accessibility trees and official APIs. Use screenshots only as
  permissioned, bounded evidence. Reassess after each navigation/action instead
  of continuing a blind click sequence.
- Require approval for sends, publishing, purchases, deletion, security changes,
  and other consequential actions. Reconcile uncertain outcomes before retry.

**Acceptance:** mocked multi-app tasks validate dependency order, approval
isolation, per-step verification, editing, pause/resume, cancellation, restart
recovery, and duplicate prevention without real external side effects.

### Phase 6 — Reliability, privacy, and rollout

- Add protocol compatibility tests, load/concurrency tests, redacted audit
  events, command retention/deletion, rate limits, secure update/signing,
  threat-model review, and restore/revocation drills.
- Maintain a tested OS/app support matrix. Mark inaccessible or unsupported
  operations explicitly rather than claiming universal compatibility.
- Stage deployment behind capability flags; do not enable remote UI execution
  for a platform until its companion and protocol acceptance gates pass.

**Acceptance:** security and operational gates pass for both supported native
targets, and production demonstrations use dedicated test accounts/apps with
recorded evidence.

## Current implementation status

Phase 1 is the only phase implemented by this increment. Native Windows and
Android cloud companions, generalized remote UI execution, screenshot-based
reasoning, and cross-device workflow recovery remain future phases; the
existing local Windows controls and browser-link companion retain their
previous, narrower behavior.
