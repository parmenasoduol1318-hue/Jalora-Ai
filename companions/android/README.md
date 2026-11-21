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

The companion package is `com.jalora.companion`, matching the previously
installed Jalora Android app so this build updates it instead of creating a
second app. Pairing credentials from the retired screen-command protocol are
not reused; the app identifies those credentials and asks you to create and
approve a fresh Android pairing using the current permissions. Revoke the old
device in Jalora after the replacement phone reports online.

1. In Jalora Integrations, create an Android pairing request and select the
   scopes to grant. App launch, screen reading, and screen control are selected
   by default; public browser links are also available.
2. Paste the one-time pairing link into the companion. Verify the account,
   server origin, and requested scopes before approving on-device.
3. App launch and screen control do not require manually selecting apps.
   The companion may report launcher-app metadata for compatibility, but
   app-name resolution and active-screen control do not depend on that
   manifest. Package names stay on this phone. The optional app selector is
   only for notification sharing when `messages.read` is granted.
4. Enable Android's Jalora Accessibility Service only if `android.screen.read`
   or `android.screen.control` was granted. Android shows its own system
   consent screen.
5. Notification access is independent. Enable it only if `messages.read` was
   granted and notification forwarding is wanted. Notification content from
   selected apps is sent to the signed-in Jalora account; Android may redact
   content according to the source app and lock-screen settings.
6. Connect from the foreground activity. Android requires a visible
   foreground-service notification while the connection is active.

For a local Jalora backend on the same computer, connect the phone over USB
with USB debugging authorized and run `adb reverse tcp:8000 tcp:8000` before
pairing or reconnecting. The companion's `localhost` URL refers to the phone;
without this forwarding, it cannot reach the computer's backend. Keep the USB
connection and reverse rule active while testing. For a phone not connected to
that computer, pair with a reachable HTTPS backend origin. Use **Test Jalora
server connection** in the companion to check its saved pairing and server
reachability before trying an app or screen action.

A `messages.read`-only pairing can connect and forward opted-in notifications
without granting app-operation scopes. It reports an empty app manifest and
cannot execute Android application commands.

In Jalora chat, ask for an app by its visible name and describe the task, for
example: “Open Maps on my Android phone and inspect the route.” Jalora discovers
a live paired phone with the required scopes. The companion resolves the
displayed name to one installed launcher package and requests its Android launch
activity; it does not depend on launcher UI search or ADB input. It reports
success only when the foreground package matches the requested app and fresh
screen evidence is available. A queued command or an unverified transition is
not treated as success. Installed-app manifests are not used to declare an app
missing. App controls are executed one command at a time and each receipt
includes the refreshed screen state for Jalora's next step.

Reading requires `android.screen.read`; clicking, scrolling, typing, and
coordinate tapping also require `android.screen.control`. Inspection reports
at most 24 accessibility controls and 16 on-device OCR text bounds from a
bounded accessibility-tree scan. OCR uses an in-memory screenshot on Android
11+; the accessibility-service configuration explicitly requests Android's
screenshot capability. The companion also sends a downscaled, size-limited JPEG
snapshot with the command receipt. Jalora stores the image in paired-device
task evidence and supplies the latest screen image to the task model through
the existing image-modality route; the model also receives structured OCR and
accessibility controls. Grant `android.screen.read` only if you consent to
sending visible screen content to your Jalora backend and configured model
provider. Older Android versions, secure windows, and unavailable screenshot
capture still return accessibility data with an explicit OCR-unavailable
status. When OCR detects authentication fields, screenshot OCR and screenshot
transfer are redacted.

Named clicks require one exact accessible control. If no scrollable control is
available, scrolling uses a bounded screen swipe. Coordinate taps are supported
as a fallback and are limited to the reported screen dimensions. Successful or
uncertain interactions are followed by a fresh accessibility/OCR snapshot;
Jalora must inspect that receipt before selecting the next action. Sensitive
named controls, app-permission grants, and all coordinate taps require Jalora's
explicit confirmation before the phone command is queued. Other scope-covered
navigation and reading do not require an extra phone-side approval.

Back navigation, app switching, notifications, and Quick Settings use Android
global actions. Screen swipes support all four directions. Long-press uses an
accessible action with a bounded coordinate-gesture fallback. Enter is sent
only to the focused accessible control and requires Jalora approval because it
can submit content. Brightness adjustment is available when Android exposes a
visible accessible brightness slider with a settable range; the selected
percentage is checked against the refreshed slider value.

Back navigation is an Android global-back action. It is reported as verified
only when the foreground app remains active and the accessibility tree changes.
Text entry is limited to a unique, visible, enabled, non-password edit control.
Recognized visible password, PIN, and verification-code inputs stop remote
click, scroll, typing, and coordinate taps; enter credentials and complete
that step directly on the phone. Password-field values are never included in
screen inspection results. This check depends on the app exposing the secure
field through Android accessibility and cannot identify every custom app
keypad. Pairing scopes authorize ordinary screen operations; they do not
authorize sensitive send, delete, payment, purchase, or publishing actions.

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

The Android source implementation and mocked backend protocol tests cover
manifest-independent launcher commands, active-screen inspection, OCR
evidence, coordinate taps, sequential delivery, and sensitive-action approval.
The development environment has no JDK, Gradle, Android SDK, emulator, or
physical Android target, so the APK has not been built or device-tested here.
On Windows, install Android Studio with JDK 17, Android SDK Platform 35, and
Android Build Tools 35; connect an unlocked phone with USB debugging enabled
and accept its debugging prompt. Run `adb devices -l` and confirm the phone is
listed as `device`, open `companions/android` in Android Studio and sync Gradle,
then run `gradle assembleDebug` and
`adb install -r app/build/outputs/apk/debug/app-debug.apk` from that project
directory. Pair the app with Jalora, grant the requested screen scopes, enable
the Accessibility Service, and leave the companion foreground service running
with its Android notification. Do not treat source presence or a successful
backend test as Android release acceptance.
