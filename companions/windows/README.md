# Jalora Windows Companion

This visible Windows client is an outbound-only executor for paired Jalora
devices. It is not a second agent or planner. The central Jalora task remains
responsible for planning and task-level approval; the Windows user separately
reviews every command in this companion.

## Run

Use Python 3.11 or newer on Windows:

```powershell
cd companions\windows
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m jalora_windows_companion
```

In Jalora Integrations, create a Windows pairing request. Select only the
permissions needed, then copy its pairing link into the companion. Review the
Jalora server, requesting account, and requested permissions before approving.
Use **Discover installed apps** to inspect classic desktop apps registered in
the Windows uninstall registry, or select an exact `.exe` path manually.
Discovery never adds an application automatically: choose which entries to
allowlist locally. The companion records a SHA-256 digest and refuses to
launch that executable if it later changes. Packaged/UWP apps and apps without
a registered executable are not discovered.

## Supported operations

- Launch an app explicitly added to the local allowlist (no shell or arguments).
- Read up to 40 accessible controls from an app launched by this companion.
- Click one uniquely named or identified enabled accessible control.
- Type up to 2,000 characters into one uniquely identified accessible edit
  field, or press a small allowlist of keys.
- Request a graceful close only for an app launched in this companion session.
- Open a public HTTP(S) link after local approval when that scope was granted.
- Capture OCR text from the approved app's foreground window, if OCR dependencies
  and the Tesseract executable are installed.

Each delivered command requires local confirmation. UIA input is limited to
apps launched by this process, and the foreground window's process is checked
before input. OCR captures only the approved foreground window, holds the
image in memory, and sends bounded OCR text—not the screenshot—to Jalora. It is
available only with `windows.screen.read` and per-command local approval. To
use OCR, install the declared Python dependencies and install Tesseract
separately; set `JALORA_TESSERACT_PATH` if it is not on `PATH`. Arbitrary shell
execution, background input, and universal app compatibility are not
supported. Accessible control names and text are sent to Jalora only when the
user explicitly grants `windows.screen.read`.

Credentials are protected with Windows DPAPI for the current Windows account
under `%LOCALAPPDATA%\Jalora\WindowsCompanion`. The companion creates no inbound
listener. A saved action receipt is encrypted before network submission and
may be replayed as a receipt after reconnect; the action itself is never
automatically repeated. A lost execution result is reported as uncertain.
Revoke the device in Jalora Integrations to invalidate its credentials.
Pause stops new command polling; a command already delivered may still need a
local decision. Stop closes the connection. The in-session action history
contains only operation, result, and time, and is not persisted.

## Limits

Windows UI Automation requires applications to expose accessible controls and
may not work across privilege levels. The companion does not bypass login,
MFA, application restrictions, secure desktops, or OS consent. Closing Jalora
while an action is in progress can leave its result uncertain. Verified
receipts are returned to the central task, but real multi-app planning,
connection-loss recovery, signing, and interactive device acceptance have not
been demonstrated.
