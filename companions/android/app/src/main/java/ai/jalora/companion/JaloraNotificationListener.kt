package ai.jalora.companion

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import org.json.JSONObject
import java.security.MessageDigest
import java.time.Instant

class JaloraNotificationListener : NotificationListenerService() {
    override fun onNotificationPosted(notification: StatusBarNotification) {
        try {
            val state = SecureStore.read(this) ?: return
            if (!hasScope(state, "messages.read")) return
            val app = selectedApp(state, notification.packageName) ?: return
            val extras = notification.notification.extras
            val sender = extras.getCharSequence(android.app.Notification.EXTRA_TITLE)
                ?.toString()
                ?.take(255)
                .orEmpty()
            val content = (
                extras.getCharSequence(android.app.Notification.EXTRA_BIG_TEXT)?.toString()
                    ?: extras.getCharSequence(android.app.Notification.EXTRA_TEXT)?.toString()
                )
                ?.take(10_000)
                .orEmpty()
            if (content.isBlank()) return
            val externalId = externalId(notification)
            CompanionApi.request(
                state.getString("api_origin"),
                "/api/devices/inbox",
                "POST",
                state.getString("device_token"),
                JSONObject()
                    .put("external_id", externalId)
                    .put("source_app", app.optString("name"))
                    .put("sender", sender)
                    .put("content", content)
                    .put("received_at", Instant.ofEpochMilli(notification.postTime).toString()),
            )
        } catch (error: Exception) {
            Log.e(TAG, "Could not forward a selected app notification to Jalora.", error)
        }
    }

    private fun selectedApp(state: JSONObject, packageName: String): JSONObject? {
        val apps = state.optJSONArray("applications") ?: return null
        return (0 until apps.length())
            .map { apps.getJSONObject(it) }
            .firstOrNull { it.optString("package_name") == packageName }
    }

    private fun hasScope(state: JSONObject, expected: String): Boolean {
        val scopes = state.optJSONArray("scopes") ?: return false
        return (0 until scopes.length()).any { scopes.optString(it) == expected }
    }

    private fun externalId(notification: StatusBarNotification): String {
        val raw = "${notification.packageName}|${notification.key}|${notification.postTime}"
        return MessageDigest.getInstance("SHA-256")
            .digest(raw.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it.toInt() and 0xff) }
    }

    companion object {
        private const val TAG = "JaloraNotifications"
    }
}
