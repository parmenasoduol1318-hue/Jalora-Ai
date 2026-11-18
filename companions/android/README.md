# Jalora Android Companion

This is a native Android executor for Jalora's existing paired-device protocol.
It is not an agent or planner. Jalora's task flow creates the commands; the app
validates the scopes granted when the phone was paired and returns a receipt
for each action.

## Build and install

Requirements:

- Android Studio with JDK 17
- Android SDK Platform 35 and Build Tools installed
- Gradle 8.9 or newer compatible with Android Gradle Plugin 8.7.3
- Android 8.0 (API 26) or newer

Open `companions/android` in Android Studio, sync the Gradle project, and run
the `app` configuration on an emulator or a dedicated test phone. With a
compatible Gradle installation and SDK configured, build a debug APK with:

```powershell
gradle assembleDebug
```

The debug APK is for development only. Production distribution requires an
organization-owned signing key, review of the Android Accessibility API and
notification-access disclosures, and a signed release build. Do not commit a
keystore or signing credentials.

## Pairing and permissions

1. In Jalora Integrations, create an Android pairing request and select the
   scopes to grant. App launch, screen reading, and screen control are selected
   by default; public browser links are also available.
2. Paste the one-time pairing link into the companion. Verify the account,
   server origin, and requested scopes before approving on-device.
3. App launch and screen control do not require manually selecting apps.
   The companion reports the installed launcher-app inventory to Jalora;
   package names stay on this phone. The optional app selector is only for
   notification sharing when `messages.read` is granted.
4. Enable Android's Jalora Accessibility Service only if `android.screen.read`
   or `android.screen.control` was granted. Android shows its own system
   consent screen.
5. Notification access is independent. Enable it only if `messages.read` was
   granted and notification forwarding is wanted. Notification content from
   selected apps is sent to the signed-in Jalora account; Android may redact
   content according to the source app and lock-screen settings.
6. Connect from the foreground activity. Android requires a visible
   foreground-service notification while the connection is active.

A `messages.read`-only pairing can connect and forward opted-in notifications
without granting app-operation scopes. It reports an empty app manifest and
cannot execute Android application commands.

In Jalora chat, ask for an installed app by name and say what to do, for example:
“Open Maps on my Android phone and inspect the route.” Jalora discovers the
live paired-device/app list, uses Android rather than the Windows-host app
controller, and executes actions covered by the pairing scopes without an
additional per-action phone approval. Public HTTP(S) links can also be opened
in the phone's default handler when `remote.browser.open_url` is granted.
Reading requires `android.screen.read`; clicking, scrolling, or typing also
requires `android.screen.control`. The companion reads Android's accessibility
tree, not screen pixels; apps that do not expose accessible text and controls
cannot be fully interpreted.

The accessibility service inspects only the target app while that app is the
foreground package. It reports at most 40 bounded controls from a 300-node
inspection. Clicks require a unique exact accessible-name or view-ID match.
Scrolling requires a unique visible, enabled, scrollable accessibility node and
uses one native forward/backward scroll action; success is reported only when
the accessibility tree changes.
Back navigation is an Android global-back action. It is reported as verified
only when the foreground app remains active and the accessibility tree changes.
Text entry is limited to a unique, visible, enabled, non-password edit control.
Recognized visible password, PIN, and verification-code inputs stop remote
click, scroll, and typing actions; enter credentials and complete that step
directly on the phone. Password-field values are never included in screen
inspection results. This check depends on the app exposing the secure field
through Android accessibility and cannot identify every custom app keypad.
Pairing scopes are the standing authorization for remote actions. No screen
image, coordinate gesture, force-close, credential entry, MFA interaction, or
background hidden action is implemented.

These controls do not make every third-party app workflow available. App-based
messages require accessible controls; the companion does not read a complete
chat history. Notification summaries are limited to content exposed in
selected-app notifications. SIM Toolkit menus and carrier/payment flows are
app- and device-specific; Jalora must stop at credential entry and the user
completes the sensitive step on the phone.

Pairing credentials, app selections, and a pending receipt are encrypted using
an Android Keystore AES-GCM key. A restart during an action records an
`uncertain` receipt and never repeats the action. The service uses outbound
HTTPS polling only, retries connection errors, and clears local pairing data
when the server rejects the device credential. Revoke the device in Jalora as
well as disconnecting locally.

## Current acceptance status

The Android source implementation is present, and backend protocol behavior is
covered by mocked API tests. This environment has no JDK, Gradle, Android SDK,
emulator, or physical Android target, so the APK has not been built or
device-tested here. Do not treat source presence or a successful backend test
as Android release acceptance.
