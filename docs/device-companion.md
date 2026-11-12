# Device and local app control

The per-device API supports browser pairing and source-run Windows and Android
desktop/mobile companions.
On Windows, the backend also supports an explicitly approved same-host desktop
app list. The web application is not a system-wide integration and cannot read
arbitrary apps or control phone calls.

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
their review/approval flows. The same-host app-control implementation does not
read or send messages through other apps. Source-run Windows and Android
companions support bounded, approval-gated native operations. They do not
provide signed installers, always-on voice listening, universal app access, or
local call handling.

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
idempotency key, expected evidence, and delivery lease. Protocol v1 allows
`browser.open_url`, bounded Windows operations (`windows.app.launch`,
`windows.app.close`, `windows.screen.inspect`, `windows.screen.ocr`,
`windows.screen.click`, `windows.screen.type`, and `windows.screen.key`), and
bounded Android operations (`android.app.launch`, `android.screen.inspect`,
`android.screen.click`, and `android.screen.type`). Browser clients poll with
the default `client_type=browser`; native clients use `client_type=windows` or
`client_type=android` and are accepted only for matching paired devices. Raw
shell commands, arbitrary code, screenshot uploads, and generic coordinate
clicks are not accepted.

The Windows and Android clients report authenticated manifests to
`PUT /api/devices/protocol/v1/manifest`. It contains only apps the Windows user
or Android user selected locally, app display names/IDs, and capabilities
permitted by the pairing scopes. Windows executable paths and hashes, and
Android package names, remain on the device. Command creation and delivery
both verify that the operation is in the current manifest. Input actions
require screen-read permission as well as screen control, and are limited to
one uniquely identified accessible control at a time.

Before performing the operation, the target must submit its explicit decision
to `POST /api/devices/protocol/v1/commands/{command_id}/decision` with the
envelope nonce. Only the first successful approval returns `execute: true` and
the URL; replayed approvals never return an executable URL. Rejection cancels
the command. A device submits a bounded receipt to
`POST /api/devices/protocol/v1/commands/{command_id}/receipt`; repeated
identical receipts are idempotent and conflicting replays are rejected.

Queued commands can be delivered again with the same command ID and nonce
after a delivery lease expires. An active lease is returned only to the poll
that acquired it, preventing concurrent polls from executing the same command.
Redelivery after expiry is safe because no external action has been approved
yet. Once execution begins, a lost execution lease is recorded as `uncertain`
and is never automatically redelivered. A late authenticated receipt may
reconcile that result. The signed-in owner can cancel queued or delivered
commands through the remote-command history endpoint; a cancellation after
execution starts is reported as uncertain because the server cannot prove that
a remote action stopped. Receipts store only bounded, allowlisted evidence
fields.

Remote command rows retain their existing central task/run/step identifiers,
the request digest, status, lease, and receipt. Accessibility results are
bounded and stored only when the owner grants the platform's screen-read
scope. The Windows companion encrypts its paired credential and any pending
receipt with current-user DPAPI. The Android companion encrypts credentials,
app selections, and a pending receipt using an Android Keystore key. After a
restart either client may resend the same receipt but never repeats the action.
Message intake and reply APIs remain separate. API callers must use HTTPS in
deployed environments.

The native Windows client is a bounded companion, not full-device
accessibility. It uses UI Automation only for apps the Windows user explicitly
adds, verifies the process/window before action, and asks for local
confirmation for every command. Optional OCR captures only the approved
foreground window in memory and sends bounded OCR text, not an image. It
requires Pillow, pytesseract, the Tesseract executable, `windows.screen.read`,
and local per-command approval.

The Android client is under `companions/android`. It uses intents for
user-selected app launch and an owner-enabled Accessibility Service for bounded
foreground-app inspection and accessible control. Android notification access
is requested separately and forwards only notifications from selected apps
when `messages.read` is granted. Secure windows, password controls, hidden
apps, and unsupported app surfaces remain inaccessible. Neither native client
is signed for production distribution or accepted on a physical device yet.
Do not treat pairing or a browser token as authorization for arbitrary actions.

Available scopes:

| Scope | Allows |
| --- | --- |
| `messages.read` | Companion submits message notifications |
| `messages.draft` | Jalora prepares an editable reply draft |
| `messages.send` | Companion retrieves a reply only after the user explicitly approves it |
| `remote.browser.open_url` | Target approves opening public web links in its browser |
| `windows.apps.launch` | Launch an app explicitly added to the Windows companion allowlist |
| `windows.screen.read` | Read accessible controls or OCR text from an approved Windows app |
| `windows.screen.control` | Click accessible controls or type into an approved app |
| `android.apps.launch` | Launch an app explicitly selected in the Android companion |
| `android.screen.read` | Read accessibility controls from a selected foreground Android app |
| `android.screen.control` | Click unique accessible controls or enter text in non-password fields |

`messages.send` requires `messages.read`. Each send requires a separate
user-approved action in Jalora; merely pairing a device never authorizes
sending. Windows screen reading requires `windows.apps.launch`. Windows screen
control requires both `windows.apps.launch` and `windows.screen.read`. Android
screen reading requires `android.apps.launch`; Android screen control requires
both `android.apps.launch` and `android.screen.read`. Both native companions
present a local approval for each received command.

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
registered Windows local apps, native companion manifests, connected cloud
integrations, and central browser/research tools. The same read-only inventory
is available to the central agent through
`discover_available_device_capabilities`.

The response distinguishes `available`, `offline`, `permission_required`,
`registered_unavailable`, and `unsupported`. Message inbox and reply endpoints
being registered does not mean the browser companion can access OS
notifications or deliver replies. Native app inventory includes only apps
explicitly selected in that companion; pairing alone does not enumerate
installed software. Local-host Windows controls are listed only when the
authenticated request is loopback to a Windows-hosted backend.

## Windows companion installation

The source-run Windows client is under `companions/windows`. It requires
Python 3.11+, `httpx`, `pywinauto`, and Windows UI Automation. Follow
[`companions/windows/README.md`](../companions/windows/README.md) to create a
virtual environment and run it. Pairing is initiated in Jalora Integrations;
paste the one-time pairing link into the Windows client and review the account,
server, and requested permissions before approval. Add trusted `.exe` files
through the local file picker. The companion hashes each executable and
revalidates it before launch.

The agent can queue one task-linked Windows operation after Jalora's shared
task-level approval. The target user then separately approves it in the
Windows companion. A queued command is not proof of completion; the owner can
review the receipt and accessible-control evidence in Integrations. Full
multi-step cross-device task waiting, recovery, and task-result delivery to the
central agent are part of the later shared-workflow phase. The central agent
can query the persisted outcome with `get_remote_device_action_status`; this
does not automatically resume a previously interrupted task.

## Android companion installation

The Android source project is under `companions/android`. Build and pairing
instructions, system-permission explanations, and known restrictions are in
[`companions/android/README.md`](../companions/android/README.md). It requires
Android 8.0+, JDK 17, Android SDK Platform 35, and Android Studio or compatible
Gradle. Accessibility and notification access are separate owner-enabled
Android settings. Notification contents from selected apps are forwarded only
with `messages.read`; native UI commands require per-command approval. This
workspace has no Android build tools or test device, so the APK has not been
built or device-tested here.

Keep message content only as long as needed. The signed-in user can remove
individual messages from the device messages panel. A native client must
request only the OS permissions it needs and must honor notification privacy
settings and locked-screen redaction.

## Platform limitations

Android may expose notifications only after the user enables notification
access; content may be redacted and app-specific. It does not grant universal
access to every app or message. iOS does not provide third-party apps
with general access to other apps' messages. Desktop access is likewise
app-specific and requires explicit OS/app integration. Calls are not handled
by this bridge; existing Twilio/Telnyx call integrations are separate,
provider-based services and do not control local handset calls.
