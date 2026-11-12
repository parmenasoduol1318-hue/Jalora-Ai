# Device and local app control

The per-device API provides message intake for a future native Jalora
companion. On Windows, the backend also supports an explicitly approved local
desktop-app list. The web application is not a system-wide integration and
cannot read arbitrary apps, click/type into arbitrary app windows, or control
phone calls.

## Windows desktop app allowlist

The local app control works only when Jalora's backend runs on the same
Windows computer as the programs. The authenticated request must come from
loopback. From Integrations, add a trusted app's absolute `.exe` path and
confirm approval. Jalora launches that exact executable without a shell or
user-supplied arguments. A chat or Agent Tasks request such as `open YouTube` can
open `https://www.youtube.com/` in the Windows computer's default browser
without app registration. This is a deterministic browser launch, not control
of a remote chat user's browser, and does not verify that YouTube finished
loading. Exact chat or Agent Tasks requests to open or close another app use its
exact enabled allowlist name; Jalora asks for approval before launching or
requesting a graceful close. Close is limited to a process Jalora launched in
the current backend session and is not a force-kill. Other app launches require
the exact enabled app name in the allowlist and their own explicit approval.
After an approved launch, the agent can inspect
the selected window, click a uniquely matched accessibility control, type text,
or press an allowlisted key. Each inspection and action has a separate
approval, and interactions are restricted to the foreground window of the app
Jalora launched. A fresh Agent Tasks request does not inherit unrelated older
conversation turns.

Jalora can request a graceful `WM_CLOSE` only for the process it started in
the current backend session. It cannot force-close apps that the user started.
Jalora waits briefly for that process to exit and reports the close as complete
only when exit is observed; an app may decline or delay a graceful close to
protect unsaved work, in which case the result remains unverified. Approval can
be removed after the Jalora-launched process exits.

For an app Jalora launched, the Integrations panel can inspect its Windows UI
Automation tree or request screenshot OCR, and can send a single click at
client-area coordinates, type up to 2,000 characters, or press one of a small
allowlist of keys. Inspection captures only the selected app's foreground
window. Screenshots are held in memory and OCR text is returned only to the
authenticated user; images are not stored. Each inspection and every input
action requires a separate confirmation. Accessibility actions require an
exactly unique control match and report whether the UI Automation tree changed;
no visible change is reported as unverified, not as success.

The backend needs `pywinauto` for Windows UI Automation. Screenshot OCR also
needs Pillow, `pytesseract`, and the Tesseract executable. Install Tesseract
separately and set `JALORA_TESSERACT_PATH` if it is not on `PATH`. Apps that
do not expose accessibility controls may not be automatable; OCR is only a
read fallback and does not infer a safe action or run a workflow.

Voice is push-to-talk while the Integrations page is open. Supported commands
are `open <approved app name>`, `close <app name>`, `click <x> <y>`, `type
<text>`, and `press <key>`. A click/type/key command requires the user to first
select an already-open approved app; the recognized command is shown for
explicit confirmation before execution. This is not an always-listening
background assistant.

Email, WhatsApp, and calls continue to use connected provider integrations and
their review/approval flows. This local app-control implementation does not read or send messages through
other apps. The Windows controls are hosted by the Jalora backend and web UI;
there is not yet a resident native tray companion, always-on voice listener,
universal app access, or local call handling. Those require a separate
platform-specific client and OS permission work.

## Starting an agent task

The Tasks page includes a task launcher for typed instructions or user-started
voice dictation. Submitting a request opens it in Agent Tasks; the user can
review the recognized text before submission. The task then uses the existing
agent planning, progress, pause/cancel, and approval flows. For multi-recipient
messaging, each send is a separate approval action; check the recipient and
message text before approving. This interface does not enable unattended
sending or guarantee completion if a provider or model becomes unavailable.

The email inbox has a supported single-message workflow: search and open a
message, ask Jalora for an editable reply, review or edit it, then save it as a
draft or use the existing explicit send approval. Reply generation alone does
not save or send anything. Provider sends remain separate and report the
provider receipt; uncertain delivery outcomes are not retried automatically.

Outbound phone calls through `POST /api/calls` require `confirm: true`. The
returned call ID is the handle for checking current status; real-provider
delivery/completion is established by the provider status callback, not by
acceptance of the initial call request. The direct endpoint does not retry
uncertain provider outcomes.

## Permission model

Pairing is initiated by an authenticated Jalora user with
`POST /api/devices/pair-requests`. The user selects the device platform and
permissions and must affirm consent. The target approves the pairing and
receives an opaque device bearer token exactly once; Jalora stores only its
SHA-256 digest. The current web companion stores the token in that browser
tab's session storage. Revoke the connection immediately if the device is lost.

### Remote browser companion pairing

The Integrations panel can create a 10-minute pairing request. Open its
one-time link on the target device; the target does not need to be signed into
the requester's Jalora account. The link carries a high-entropy, short-lived
pairing capability in its URL fragment. The target page exchanges that
capability for the request details, then must explicitly approve or reject.
The device token is issued only after approval, stored in that browser tab's
session storage, and can be revoked from Integrations. Pairing links open the
frontend configured by `JALORA_FRONTEND_BASE_URL`; keep this separate from
`JALORA_PUBLIC_BASE_URL`, which identifies the backend. On the deployed app use
`https://jalora-ai.vercel.app` for the frontend URL. Locally, an empty frontend
URL falls back to the local public base URL.

To pair the browser currently in use, choose **Pair this browser/device** and
open the approval link in its new tab. To pair a different device, choose
**Pair a different device** and open the link there. Approval creates only a
browser companion connection; it does not grant Windows app or filesystem
access. The companion's current scope is limited to opening approved public
HTTP(S) links in that browser.

The browser companion supports target-approved external HTTP(S) links. Each
request requires confirmation by the sending user and a separate approval on
the target. Private/internal destinations are rejected. Opening a link is not
proof that it loaded or that an action in the destination completed.

### Versioned outbound command protocol

Protocol v1 provides a device-initiated HTTPS long-poll transport; it does not
open an inbound port on the user's device. A paired device exchanges its
one-time pairing bearer credential at `POST /api/devices/protocol/v1/session`
for a signed access token that expires after five minutes. The pairing
credential remains revocable and must be kept private; protocol requests use
the short-lived access token. Device revocation is checked against the database
on each protocol request and invalidates outstanding access tokens.

The device long-polls `GET /api/devices/protocol/v1/commands`; the bounded
`wait_seconds` query parameter defaults to 20 seconds and accepts 0–25 seconds.
The response contains
a versioned envelope with the device, task, run, and task-step IDs, allowlisted
operation and arguments, required scope, expiration, command-scoped nonce,
idempotency key, expected evidence, and delivery lease. Protocol v1 currently
allows only `browser.open_url`, and its URL is revalidated before delivery.
Raw shell commands, arbitrary code, screen capture, and generic UI actions are
not accepted.

Before performing the operation, the target must submit its explicit decision
to `POST /api/devices/protocol/v1/commands/{command_id}/decision` with the
envelope nonce. Only the first successful approval returns `execute: true` and
the URL; replayed approvals never return an executable URL. Rejection cancels
the command. A device submits a bounded receipt to
`POST /api/devices/protocol/v1/commands/{command_id}/receipt`; repeated
identical receipts are idempotent and conflicting replays are rejected.

Queued commands can be delivered again with the same command ID and nonce
after a delivery lease expires. This is safe because no external action has
been approved yet. Once execution begins, a lost execution lease is recorded
as `uncertain` and is never automatically redelivered. A late authenticated
receipt may reconcile that result. The signed-in owner can cancel queued or
delivered commands through the remote-command history endpoint; a cancellation
after execution starts is reported as uncertain because the server cannot
prove that a remote action stopped. Receipts store only bounded, allowlisted
evidence fields.

Remote command rows retain their existing central task/run/step identifiers,
the request digest, status, lease, and receipt. The protocol is a transport
foundation, not a native companion: the shipped browser companion now uses
protocol v1 for approved link requests, but no Windows/Android background
client consumes it yet. Message intake and reply APIs remain separate. API
callers must use HTTPS in deployed environments.

This is not full-device accessibility. A browser page cannot inspect another
app's screen, click its controls, type into it, initiate calls, or keep
operating when the page is closed. Those features require a platform-specific
native companion and explicit OS permissions (for example, an Android
Accessibility Service enabled by the device owner). Such a client and its
screen/action protocol are not implemented here. Do not treat pairing or a
browser token as authorization for arbitrary actions.

Available scopes:

| Scope | Allows |
| --- | --- |
| `messages.read` | Companion submits message notifications |
| `messages.draft` | Jalora prepares an editable reply draft |
| `messages.send` | Companion retrieves a reply only after the user explicitly approves it |
| `remote.browser.open_url` | Target approves opening public web links in its browser |

`messages.send` requires `messages.read`. Each send requires a separate
user-approved action in Jalora; merely pairing a device never authorizes
sending.

## Companion protocol

All companion requests use `Authorization: Bearer <device_token>`.

1. Submit an eligible notification to `POST /api/devices/inbox` with
   `external_id`, `source_app`, `sender`, `content`, and optional ISO timestamp
   `received_at`. The external ID is idempotent per device.
2. The signed-in user can inspect or delete stored messages at
   `GET /api/devices/inbox` and edit drafts with
   `PATCH /api/devices/inbox/{message_id}`.
   User-triggered summaries and reply drafts use a direct text-model call with no
   agent tools attached; message text is treated as untrusted input.
3. After the user reviews and approves a reply, the companion polls
   `GET /api/devices/actions`. Each action has a stable message ID, which the
   companion should use for idempotent delivery.
4. After the platform confirms the reply action, acknowledge it with
   `POST /api/devices/actions/{message_id}/ack`.
5. The user can revoke a device with `DELETE /api/devices/{device_id}`.
   Revocation immediately invalidates its token and prevents further polling.

   ## Capability discovery

   The authenticated user can query `GET /api/devices/capabilities` for a
   user-scoped inventory of paired-device status, granted operation scopes,
   registered Windows local apps, connected cloud integrations, and central
   browser/research tools. The same read-only inventory is available to the
   central agent through `discover_available_device_capabilities`.

   The response distinguishes `available`, `offline`, `permission_required`,
   `registered_unavailable`, and `unsupported`. In particular, message inbox and
   reply endpoints being registered does not mean the current browser companion
   can access OS notifications or deliver replies; that requires a native
   companion. Pairing never implies installed-app discovery, screen inspection,
   or native UI control. Windows controls are listed only when the authenticated
   request is loopback to a Windows-hosted backend.

   Keep message content only as long as needed. The signed-in user can remove
individual messages from the device messages panel. A native client must
request only the OS permissions it needs and must honor notification privacy
settings and locked-screen redaction.

## Platform limitations

Android may expose eligible notifications through a user-enabled notification
listener and may support inline reply actions for some apps. It does not grant
universal access to every app or message. iOS does not provide third-party apps
with general access to other apps' messages. Desktop access is likewise
app-specific and requires explicit OS/app integration. Calls are not handled
by this bridge; existing Twilio/Telnyx call integrations are separate,
provider-based services and do not control local handset calls.
