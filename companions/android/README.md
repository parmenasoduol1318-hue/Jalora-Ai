# Jalora Android Companion

This is a native Android executor for Jalora's existing paired-device protocol.
It is not an agent or planner. Jalora's central task and approval flow creates
the commands; the app validates the device grant and the Android user approves
each command on the phone.

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

1. In Jalora Integrations, create an Android pairing request. Select only the
   required Android scopes and approve each scope on this phone.
2. Paste the one-time pairing link into the companion. Verify the account,
   server origin, and requested scopes before approving on-device.
3. Select only apps the companion may operate, or whose notifications may be
   shared when `messages.read` is granted. Package names stay on this phone;
   Jalora receives an app ID and display label.
4. Enable Android's Jalora Accessibility Service only if `android.screen.read`
   or `android.screen.control` was granted. Android shows its own system
   consent screen.
5. Notification access is independent. Enable it only if `messages.read` was
   granted and notification forwarding is wanted. Notification content from
   selected apps is sent to the signed-in Jalora account; Android may redact
   content according to the source app and lock-screen settings.
6. Connect from the foreground activity. Android requires notifications for
   visible foreground-service status and per-action approval.

A `messages.read`-only pairing can connect and forward opted-in notifications
without granting app-operation scopes. It reports an empty app manifest and
cannot execute Android application commands.

The accessibility service inspects only the selected app while that app is the
foreground package. It reports at most 40 bounded controls from a 300-node
inspection. Clicks require a unique exact accessible-name or view-ID match.
Scrolling requires a unique visible, enabled, scrollable accessibility node and
uses one native forward/backward scroll action; success is reported only when
the accessibility tree changes.
Text entry is limited to a unique, visible, enabled, non-password edit control.
Every remote action has a separate approval notification; the expanded
notification shows the exact request on an unlocked device. No screen image,
coordinate gesture, force-close, credential entry, MFA interaction, or
background hidden action is implemented.

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
