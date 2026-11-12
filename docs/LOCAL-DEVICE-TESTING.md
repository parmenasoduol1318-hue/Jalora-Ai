# Local-device testing and remaining stage gates

This guide prepares Jalora for safe browser-based testing from a phone or
another computer on the same private network. The browser companion remains
separate from the native Windows and Android companions described below.

## What is available now

- The React application and FastAPI backend run locally. The frontend proxies
  API requests to a backend bound to loopback, so the backend does not need to
  be exposed directly to the LAN.
- A separate LAN test launcher starts a fresh `JALORA_ENV=test` backend on
  `127.0.0.1:8014` and Vite on `0.0.0.0:5184`. It uses a fresh temporary
  SQLite database and temporary files for each run, disables scheduled jobs,
  blanks external provider credentials, and selects mock integrations. Local
  LM Studio can be used for model responses if it is running on this PC.
- Browser pairing supports a deliberately narrow target capability:
  user-approved opening of public HTTP(S) links in the paired browser. The
  target browser must be able to reach Jalora at the configured LAN URL.
- Windows local app launch/inspection is same-host functionality. It is not
  remote control of the phone, and is only available when the backend itself
  runs on that Windows computer.

## Start the isolated LAN test stack

On the Jalora PC, open PowerShell in the repository root:

```powershell
.\scripts\start-lan-device-test.ps1
```

The script selects the PC's private IPv4 address, checks that ports 8014 and
5184 are free, starts a test-only API and frontend, and verifies both health
and the frontend-to-backend proxy. It prints a URL such as
`http://192.168.x.x:5184`. Open that URL on the PC and on a phone connected to
the **same private Wi-Fi**. The registration/login flow uses the isolated test
database; create a dedicated test user. Do not use the test stack for real
email, calls, purchases, or deletion of personal data.

The launcher has passed backend health and frontend-proxy checks, and an
authenticated text chat returned a response from a local model. These checks
were performed on the Jalora PC; they do not prove that a physical phone can
reach the PC through its Wi-Fi and firewall.

To pair the phone's browser, sign into the test account on both devices, create
a pairing request in Integrations, open its link on the phone, and approve only
the displayed `remote.browser.open_url` permission. A test-generated pairing
link uses the current LAN URL, not `localhost`. Revoke the device from
Integrations when finished.

Stop only the two processes created by the launcher:

```powershell
.\scripts\stop-lan-device-test.ps1
```

Each run leaves its isolated database and logs under
`%TEMP%\jalora-lan-device-test\<run timestamp>`. Keep it for debugging or
remove that exact run directory yourself when no longer needed; the launcher
does not delete user data.

If the phone cannot load the URL, confirm both devices are on the same private
Wi-Fi and that Windows Firewall permits Node/Vite on **Private** networks for
port 5184. Do not enable a Public-network rule, router port forwarding, or
internet exposure. The API remains bound to loopback. The address can change
when Wi-Fi reconnects; rerun the launcher to print the current one.

## Stage 7 — email and direct actions

The local/mock implementation covers central task/approval state and safe
provider-independent flows; prior focused evidence is recorded in the master
roadmap. Stage 7 remains **PARTIALLY VERIFIED / WAIVED FOR SEQUENCING**, not
fully verified, until the authorized provider boundary is exercised.

What the user must provide for the remaining acceptance:

1. Choose one supported provider and supply a dedicated test/sandbox account,
   OAuth client/configuration, and redirect URL reachable from the test
   devices. Never paste credentials into chat or commit them.
2. Provide an authorized test mailbox/recipient or sandbox destination. Test
   draft, edit, approve, reject, receipt, duplicate prevention, and uncertain
   outcome reconciliation. Do not use real recipients for the acceptance run.
3. If testing cross-device browser pairing, use the LAN launcher and approve
   the narrow browser-link scope on the target device.
4. Approve live sends only after reviewing the exact recipient and message;
   revoke the sandbox token after testing.

If no provider sandbox or authorized test account is available, the mock/API
suite is the supported boundary and live send acceptance stays blocked.

## Stage 12 — native device companions

Stage 12 is **IMPLEMENTED IN SOURCE / NOT PLATFORM-ACCEPTED**. The Windows
companion uses outbound protocol v1, current-user DPAPI credential storage, an
owner-selected executable allowlist, local per-command approval, UI Automation,
foreground-only OCR, visible session history, pause/stop, and bounded receipts.
The Android source companion supports device pairing, app selection, Android
Keystore-encrypted credentials, a foreground service, notification approval,
scoped Accessibility Service operations, and a separate notification listener
gated by `messages.read`. Notification-only Android pairings can connect without
application-control scopes. Device receipts are linked back to task-step
evidence. The Android project has not been built in this environment.

Windows automated tests use mocked API responses and do not send real external
actions. The remaining Windows acceptance requires a signed distribution,
interactive test app, UIA/OCR evidence, and network-loss and revocation
demonstration. Android acceptance requires building the APK, a physical or
virtual Android target, and explicit OS-permission, launch, accessibility,
restart/reconnect, and revocation tests after reviewing the app and data flow.
Browser pairing does not complete native-device acceptance.

## Universal workflow and reliability acceptance

The shared capability registry and task-linked remote commands are implemented,
but automatic central-agent wait/resume after a device receipt is not. Multi-app
workflow acceptance remains incomplete. Protocol tests cover authorization,
idempotency, command leases, cancellation, revocation, receipts, and uncertain
recovery; no production load targets, signed companion releases, or Android
device demonstrations have been verified in this environment. Keep Phases 5
and 6 blocked until their listed acceptance checks in
[`the universal device operator plan`](./UNIVERSAL-DEVICE-OPERATOR-PLAN.md)
have been executed on the intended platforms.

For Windows and Android development, follow [the companion run guide](./device-companion.md)
and use disposable apps/accounts. For the phone browser UI and approved
public-link handoff, use the isolated LAN test stack described above.

## Stage 14 — security, recovery, and deployment

Stage 14 remains **PARTIALLY VERIFIED / DEPLOYMENT GATES BLOCKED**. Focused
local regressions and operational changes are recorded in the master roadmap
and [operations runbook](./OPERATIONS-RUNBOOK.md). Before a production claim,
the user/operator must:

- install Docker Desktop (or choose another documented runtime) to build and
  exercise the container deployment; Docker was unavailable during local
  verification;
- set strong, separate JWT and Fernet credential-encryption keys via a secret
  manager, back them up securely, and rehearse rotation/restore on disposable
  data before migrating real credentials;
- run the independent security/threat-model review and decide/record resolution
  or explicit acceptance of each finding;
- own encrypted off-host backups, retention, access control, and periodic
  restore drills; and
- choose the database, worker count, load/concurrency thresholds, provider and
  OS support matrix, privacy export/deletion and audit-retention policy, and
  responsible incident contacts.

The LAN launcher is a **development/test environment only**; its fixed test
secret, mock integrations, temporary database, HTTP transport, and unauthenticated
LAN reachability are not suitable for production or untrusted networks. It
does not replace HTTPS, production secret management, or operational controls.

## Required acceptance sequence

1. Verify the LAN test stack from the Jalora PC, then open it from one phone on
   the same private Wi-Fi and create a test account.
2. Exercise chat, task details, approval/rejection, cancellation, and browser
   pairing; record what is verified versus only submitted.
3. Run Stage 7 mock tests, then provider acceptance only after the user creates
   an authorized sandbox account.
4. Keep Stage 12 unaccepted until the Windows companion is signed and validated
   against a disposable UIA app and the Android APK is built and tested on
   named targets; browser tests do not satisfy this gate.
5. Complete Stage 14 deployment, security, backup/restore, rollback, and
   load/concurrency gates before exposing any production service.

For code/test commands and the Stage 14 record, see
[the master roadmap](../plans/JALORA-MASTER-IMPLEMENTATION-ROADMAP.md).
