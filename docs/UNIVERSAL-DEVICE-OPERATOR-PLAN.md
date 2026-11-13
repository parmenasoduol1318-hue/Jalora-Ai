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

1. Windows and Android native companion source clients now connect outbound.
   Neither has a signed production distribution or an interactive platform
   acceptance run in this environment.
2. The Android Accessibility Service and notification listener require
   explicit device-owner approval. App-specific restrictions, secure windows,
   lock-screen redaction, and OS background limits remain authoritative.
3. Protocol v1 now transports a bounded, typed operation set and companion
   manifests. It does not transport screenshot images or persist cross-device
   UI checkpoints.
4. Existing UI actions remain narrow and approval-gated. App-specific
   restrictions and API-vs-UI selection are not yet represented by a common
   live capability contract.
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

- Additive schema and APIs extend the existing remote-browser command records;
  they do not create a second task or approval manager.
- Versioned envelopes bind device, task, run, task step, operation, validated
  arguments, required scope, expiry, nonce, idempotency key, payload digest,
  and expected evidence.
- HTTPS long polling is device-initiated. A paired credential exchanges for a
  five-minute signed protocol access token; every request rechecks active
  device identity and current scopes.
- Atomic delivery leases permit safe redelivery only before target approval.
  Stale in-flight execution becomes `uncertain`, never an automatic retry.
  Nonce-bound decisions/receipts, cancellation, revocation, and bounded receipt
  evidence are persisted.
- The browser client receives only `browser.open_url`; native Windows and
  Android clients receive platform-specific app and accessibility operations.
  Neither client accepts raw shell commands or arbitrary code.

**Status:** Implemented and tested for browser-link and the bounded Windows
and Android operation envelopes. The browser companion remains isolated to
browser-link commands. Focused tests cover token expiry, authorization/scopes,
task/run/step binding, idempotent enqueue and receipt, app-manifest validation,
client separation, command expiry/cancellation, revocation, bounded evidence,
and uncertain execution recovery.

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

**Implementation:** The source-run Tkinter client supports DPAPI-protected
pairing credentials, user-selected/hash-checked executables, outbound
long-polling, scoped manifests, local per-command approval, allowlisted UI
Automation, foreground-scoped in-memory OCR, bounded receipts, visible
in-session action history, pause/stop, and safe receipt recovery. A central
status tool exposes persisted outcomes without claiming a queued action
completed. It does not force-close apps or replay an action whose result is
uncertain.

**Status:** Source implementation complete; platform/release acceptance is
blocked. Automated protocol, permission, OCR, and receipt tests pass. Code
signing/distribution and a disposable interactive Windows app demonstration
require Windows release credentials and a Windows target. The central agent
can inspect persisted action status, but automatic task wait/resume delivery
remains in Phase 5. No real account or external action has been used.

### Phase 4 — Android companion

- Build a native Android companion with explicit pairing, per-capability
  permission explanations, revocation, foreground status, and privacy controls.
- Use intents for selected-app launch and an owner-enabled Accessibility
  Service for bounded foreground inspection, unique accessible control clicks,
  and non-password text entry. Request notification access separately and
  forward only selected-app notifications when `messages.read` is granted.
- Encrypt credentials and pending receipts with Android Keystore; reconcile an
  interrupted in-flight operation as `uncertain` rather than repeating it.
- Never bypass credentials, MFA, secure windows, or application sandboxing.

**Implementation:** A native Android Gradle project and companion service now
use the shared pair request, manifest, command, decision, and receipt protocol.
Each action requires a device notification approval. Screen operations stop
unless the selected package is the foreground app. The separate notification
listener honors `messages.read` and the selected-app allowlist.

**Status:** Source implementation is present; Android acceptance remains
blocked. This environment has no JDK, Gradle, Android SDK, emulator, or
physical Android device, so the APK cannot be built or permission, lifecycle,
accessibility, offline reconnect, and revocation behavior demonstrated here.
These are mandatory acceptance gates before production use.

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

**Status:** Partially implemented. Capability discovery, central registry
routing, task-linked commands, individual shared/local approval, persisted
command status, and bounded receipts are implemented. Authenticated device
receipts update the linked task-step evidence and verification status.
Successful receipts automatically resume remaining central-agent steps, and
an identical receipt can retry continuation if an earlier attempt was
interrupted; a conditional run-status claim prevents concurrent duplicate
resumes. Mock coverage verifies two allowlisted Android apps can create
separately approved commands whose receipts update distinct steps in the same
task, and a receipt resumes the remaining response step. This is a
protocol/task-lifecycle acceptance, not proof that the central planner can
autonomously sequence multiple real apps. Full mocked multi-app workflow
coverage and native-device acceptance remain incomplete.

The legacy browser-link `/opened` route now records through the same receipt
and task-update path as protocol v1. Approval leaves the task partial; only a
successful open receipt completes the linked task. The regression test covers
that transition and rejects duplicate decisions/receipts.

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

**Status:** Partially implemented. Protocol tests cover scoped access,
idempotency, delivery/execution leases, cancellation, revocation, bounded
receipts, and uncertain outcomes; companion unit tests use mocks. There are no
measured load/concurrency thresholds, production threat-model sign-off,
signed companion release, Android device acceptance, or demonstrated
backup/restore and retention drills in this environment.

## Current implementation status

Phases 1 and 2 are implemented. Phase 3 now has an expanded Windows companion
source implementation; code signing and interactive Windows acceptance remain
incomplete. Phase 4 has an Android companion source implementation but has
not been built or tested on Android. Device receipts complete a task when its
final tool step is verified; remaining steps require explicit run resume, so
automatic cross-device task wait/resume remains incomplete.
Phases 5 and 6 are partially implemented as stated above and are not accepted.
The browser companion remains restricted to approved public links. Message
bridge APIs remain separate. Screenshot-image reasoning and automatic
cross-device task wait/resume remain future work. The existing same-host
Windows controls retain their previous behavior.
