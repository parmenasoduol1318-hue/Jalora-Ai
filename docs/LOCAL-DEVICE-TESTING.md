# Local-device testing and remaining stage gates

This guide prepares Jalora for safe browser-based testing from a phone or
another computer on the same private network. It does **not** make the current
browser companion a native Windows or Android companion.

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

Stage 12 is **BLOCKED for native/device-wide automation**. Pairing a phone
browser to the LAN test stack does not expose installed apps, screen content,
accessibility trees, touch/keyboard input, phone calls, or filesystem access.
Those are not shipped or made safe by enabling a browser permission.

Native Windows/Android implementation still requires separate companion
projects, versioned authenticated/replay-resistant protocol, target-side
permission enforcement, scoped OS grants, revocation/reconnect tests,
install/update/signing, and supported-device test coverage. Android testing
requires a physical/virtual Android device and the user to grant the specific
OS permissions after reviewing the app and data flow. Windows remote testing
requires a second Windows target and explicit UI Automation/desktop permission.
The user must select the first target platform and supply/authorize those
devices before native work can be meaningfully acceptance-tested. Do not
describe browser pairing as completing this stage.

The first safe local validation today is the phone browser UI and approved
public-link handoff described above. For same-PC Windows app actions, run the
backend locally on Windows, register one harmless test application, review each
inspection/input approval, and verify the outcome on screen.

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
4. Keep Stage 12 blocked until native Windows/Android clients exist and are
   tested on named targets; browser tests do not satisfy this gate.
5. Complete Stage 14 deployment, security, backup/restore, rollback, and
   load/concurrency gates before exposing any production service.

For code/test commands and the Stage 14 record, see
[the master roadmap](../plans/JALORA-MASTER-IMPLEMENTATION-ROADMAP.md).
