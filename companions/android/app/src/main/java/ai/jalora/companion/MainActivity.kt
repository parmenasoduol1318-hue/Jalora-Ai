package ai.jalora.companion

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.ViewGroup
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executors

class MainActivity : Activity() {
    private val worker = Executors.newSingleThreadExecutor()
    private lateinit var pairingLink: EditText
    private lateinit var status: TextView
    private lateinit var appList: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        render()
        refreshState()
    }

    private fun render() {
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24, 24, 24, 24)
        }
        val scroll = ScrollView(this).apply { addView(content) }
        setContentView(scroll)

        content.addView(TextView(this).apply {
            text = "Jalora Android Companion"
            textSize = 22f
        })
        content.addView(TextView(this).apply {
            text = "Pair this phone with a Jalora account you control. App access is limited to apps you select, each remote action is shown here for approval, and Android permissions can be revoked in system settings."
            textSize = 15f
            setPadding(0, 10, 0, 16)
        })
        pairingLink = EditText(this).apply {
            hint = "Paste the Jalora Android pairing link"
            minLines = 2
        }
        content.addView(pairingLink, matchWidth())
        content.addView(button("Review pairing and approve") { reviewPairing() })
        status = TextView(this).apply { textSize = 15f }
        content.addView(status)

        content.addView(sectionTitle("Selected applications"))
        appList = TextView(this).apply { textSize = 14f }
        content.addView(appList)
        content.addView(button("Choose apps this companion may open or inspect") {
            chooseApps()
        })

        content.addView(sectionTitle("Operating-system permissions"))
        content.addView(TextView(this).apply {
            text = "Accessibility is required only for screen-reading and interaction scopes. Notification access is separate and sends notification text only when Jalora has messages.read permission and the app is selected above."
            textSize = 14f
        })
        content.addView(button("Open Accessibility settings") {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        })
        content.addView(button("Open notification-access settings") {
            val state = SecureStore.read(this)
            if (state == null || !hasScope(state, "messages.read")) {
                status.text = "Notification access requires Jalora's separate messages.read permission."
            } else {
                startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
            }
        })
        content.addView(button("Check permissions") { refreshState() })

        content.addView(sectionTitle("Connection"))
        content.addView(button("Connect / resume") { startCompanion() })
        content.addView(button("Pause command polling") {
            startService(Intent(this, DeviceCompanionService::class.java).setAction(DeviceCompanionService.ACTION_PAUSE))
            status.text = "Paused. Commands already delivered remain available for your review."
        })
        content.addView(button("Stop connection") {
            startService(Intent(this, DeviceCompanionService::class.java).setAction(DeviceCompanionService.ACTION_STOP))
            status.text = "Connection stopped."
        })
        content.addView(button("Disconnect this phone") { disconnect() })
    }

    private fun reviewPairing() {
        val link = try {
            CompanionApi.parsePairingLink(pairingLink.text.toString())
        } catch (error: IllegalArgumentException) {
            status.text = error.message
            return
        }
        status.text = "Checking the one-time pairing request…"
        worker.execute {
            try {
                val request = CompanionApi.request(
                    link.origin,
                    "/api/devices/pair-requests/${Uri.encode(link.requestId)}/target",
                    "POST",
                    body = JSONObject().put("pairing_code", link.pairingCode),
                )
                if (request.optString("platform") != "android") {
                    error("This pairing request is not for an Android device.")
                }
                if (request.optString("status") != "pending") {
                    error("This pairing request is ${request.optString("status")}.")
                }
                runOnUiThread {
                    val scopes = request.optJSONArray("scopes") ?: JSONArray()
                    val permissionText = (0 until scopes.length())
                        .joinToString("\n") { scopeLabel(scopes.optString(it)) }
                    AlertDialog.Builder(this)
                        .setTitle("Approve Android device pairing?")
                        .setMessage(
                            "Jalora account: ${request.optString("requested_by", "unknown")}\n" +
                                "Device: ${request.optString("name", "Android device")}\n" +
                                "Server: ${link.origin}\n\n" +
                                "Requested permissions:\n$permissionText\n\n" +
                                "This does not grant Android accessibility or notification access. " +
                                "Those require separate actions in Android Settings.",
                        )
                        .setNegativeButton("Cancel", null)
                        .setPositiveButton("Approve on this phone") { _, _ ->
                            approvePairing(link, request)
                        }
                        .show()
                }
            } catch (error: Exception) {
                runOnUiThread { status.text = "Pairing review failed: ${error.message}" }
            }
        }
    }

    private fun approvePairing(link: PairingLink, request: JSONObject) {
        status.text = "Approving the pairing on this phone…"
        worker.execute {
            try {
                val response = CompanionApi.request(
                    link.origin,
                    "/api/devices/pair-requests/${Uri.encode(link.requestId)}/approve",
                    "POST",
                    body = JSONObject()
                        .put("confirm", true)
                        .put("pairing_code", link.pairingCode),
                )
                val device = response.optJSONObject("device")
                    ?: error("Jalora did not return the paired device.")
                val state = JSONObject()
                    .put("api_origin", link.origin)
                    .put("device_id", device.optString("id"))
                    .put("device_name", device.optString("name", request.optString("name")))
                    .put("device_token", response.getString("device_token"))
                    .put("scopes", device.optJSONArray("scopes") ?: request.optJSONArray("scopes") ?: JSONArray())
                    .put("applications", JSONArray())
                    .put("sent_message_ids", JSONArray())
                SecureStore.write(this, state)
                runOnUiThread {
                    pairingLink.setText("")
                    status.text = "Paired. Select permitted apps, review Android permissions, then connect."
                }
            } catch (error: Exception) {
                runOnUiThread { status.text = "Pairing approval failed: ${error.message}" }
            }
        }
    }

    private fun chooseApps() {
        val state = try {
            SecureStore.read(this) ?: error("Pair this Android device first.")
        } catch (error: Exception) {
            status.text = error.message
            return
        }
        val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val candidates = packageManager.queryIntentActivities(launcher, 0)
            .mapNotNull { resolve ->
                val packageName = resolve.activityInfo?.packageName ?: return@mapNotNull null
                val label = resolve.loadLabel(packageManager)?.toString() ?: packageName
                packageName to label
            }
            .distinctBy { it.first }
            .filter { it.first != packageName }
            .sortedBy { it.second.lowercase() }
        val current = state.optJSONArray("applications") ?: JSONArray()
        val selected = candidates.map { (packageName, _) ->
            (0 until current.length()).any {
                current.optJSONObject(it)?.optString("package_name") == packageName
            }
        }.toBooleanArray()
        AlertDialog.Builder(this)
            .setTitle("Choose apps for Jalora")
            .setMessage(
                if (hasScope(state, "messages.read")) {
                    "Selected app notifications may be sent to your Jalora account because messages.read is enabled."
                } else {
                    "Only selected apps can be launched or inspected under the permissions you approved."
                },
            )
            .setMultiChoiceItems(
                candidates.map { it.second }.toTypedArray(),
                selected,
            ) { _, index, checked -> selected[index] = checked }
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Save selected apps") { _, _ ->
                val apps = JSONArray()
                candidates.forEachIndexed { index, (packageName, label) ->
                    if (selected[index]) {
                        apps.put(
                            JSONObject()
                                .put("id", CompanionApi.appId(packageName))
                                .put("name", label.take(120))
                                .put("package_name", packageName),
                        )
                    }
                }
                state.put("applications", apps)
                SecureStore.write(this, state)
                appList.text = selectedAppNames(apps)
                status.text = "Saved ${apps.length()} selected application(s) on this phone."
            }
            .show()
    }

    private fun startCompanion() {
        val state = try {
            SecureStore.read(this) ?: error("Pair this Android device first.")
        } catch (error: Exception) {
            status.text = error.message
            return
        }
        val hasNativePermission = listOf(
            "android.apps.launch",
            "android.screen.read",
            "android.screen.control",
        ).any { hasScope(state, it) }
        val canReadNotifications = hasScope(state, "messages.read")
        if (!hasNativePermission && !canReadNotifications) {
            status.text = "This pairing has no Android app-operation or notification-reading permission. Select a permission in Jalora and pair again."
            return
        }
        if ((state.optJSONArray("applications")?.length() ?: 0) == 0) {
            status.text = "Select at least one app before connecting."
            return
        }
        if (Build.VERSION.SDK_INT >= 33
            && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQUEST_NOTIFICATIONS)
            status.text = "Grant notifications so you can see and approve each incoming device action."
            return
        }
        val intent = Intent(this, DeviceCompanionService::class.java)
            .setAction(DeviceCompanionService.ACTION_START)
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(intent) else startService(intent)
        status.text = "Connecting through outbound HTTPS. This phone will ask before every native action."
    }

    private fun disconnect() {
        if (SecureStore.read(this) == null) {
            status.text = "This phone is not paired."
            return
        }
        AlertDialog.Builder(this)
            .setTitle("Disconnect this phone?")
            .setMessage("Stop the connection and remove its encrypted local credential? Revoke the device separately in Jalora to prevent future access.")
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Disconnect") { _, _ ->
                startService(Intent(this, DeviceCompanionService::class.java).setAction(DeviceCompanionService.ACTION_STOP))
                SecureStore.clear(this)
                appList.text = ""
                status.text = "Local credential removed. Revoke the phone in Jalora as well."
            }
            .show()
    }

    private fun refreshState() {
        val state = try {
            SecureStore.read(this)
        } catch (error: Exception) {
            status.text = "Saved credential error: ${error.message}"
            return
        }
        if (state == null) {
            status.text = "Not paired."
            appList.text = "No application allowlist is configured."
            return
        }
        appList.text = selectedAppNames(state.optJSONArray("applications") ?: JSONArray())
        val accessibilityEnabled = isAccessibilityEnabled()
        val notificationEnabled = isNotificationListenerEnabled()
        status.text = "Paired with ${state.optString("device_name")}.\n" +
            "Accessibility service: ${if (accessibilityEnabled) "enabled" else "not enabled"}\n" +
            "Notification access: ${if (notificationEnabled) "enabled" else "not enabled"}"
    }

    private fun isAccessibilityEnabled(): Boolean {
        val expected = ComponentName(this, JaloraAccessibilityService::class.java).flattenToString()
        val enabled = Settings.Secure.getString(contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES)
            ?: return false
        return enabled.split(':').any { it.equals(expected, ignoreCase = true) }
    }

    private fun isNotificationListenerEnabled(): Boolean {
        val enabled = Settings.Secure.getString(contentResolver, "enabled_notification_listeners")
            ?: return false
        return enabled.split(':').any { it.startsWith(packageName) }
    }

    private fun hasScope(state: JSONObject, scope: String): Boolean {
        val scopes = state.optJSONArray("scopes") ?: return false
        return (0 until scopes.length()).any { scopes.optString(it) == scope }
    }

    private fun scopeLabel(scope: String): String = when (scope) {
        "android.apps.launch" -> "Launch only apps selected on this phone"
        "android.screen.read" -> "Read accessible content from selected foreground apps"
        "android.screen.control" -> "Click, scroll, or enter approved text in selected apps"
        "messages.read" -> "Read selected apps' notification content"
        else -> scope
    }

    private fun selectedAppNames(apps: JSONArray): String =
        if (apps.length() == 0) "No apps selected."
        else (0 until apps.length()).joinToString("\n") { apps.optJSONObject(it)?.optString("name").orEmpty() }

    private fun button(label: String, action: () -> Unit) =
        Button(this).apply {
            text = label
            setOnClickListener { action() }
            layoutParams = matchWidth()
        }

    private fun sectionTitle(title: String) =
        TextView(this).apply {
            text = title
            textSize = 18f
            setPadding(0, 18, 0, 8)
        }

    private fun matchWidth() = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.WRAP_CONTENT,
    )

    override fun onResume() {
        super.onResume()
        if (::status.isInitialized) refreshState()
    }

    override fun onDestroy() {
        worker.shutdownNow()
        super.onDestroy()
    }

    companion object {
        private const val REQUEST_NOTIFICATIONS = 931
    }
}
