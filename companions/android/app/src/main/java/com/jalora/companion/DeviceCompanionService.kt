package com.jalora.companion

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.IBinder
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicBoolean

class DeviceCompanionService : Service() {
    private val running = AtomicBoolean(false)
    private val paused = AtomicBoolean(false)
    private var worker: Thread? = null
    private var heartbeatWorker: Thread? = null
    @Volatile private var accessToken = ""
    @Volatile private var accessTokenExpiry = 0L

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> startConnection()
            null -> startConnection()
            ACTION_PAUSE -> {
                paused.set(true)
                updateStatus("Paused. No new command will be executed.")
            }
            ACTION_RESUME -> {
                paused.set(false)
                updateStatus("Resuming the companion connection.")
            }
            ACTION_STOP -> stopConnection()
        }
        return START_STICKY
    }

    private fun startConnection() {
        if (running.getAndSet(true)) {
            paused.set(false)
            return
        }
        createChannels()
        startForeground(NOTIFICATION_ID, statusNotification("Connecting to Jalora over HTTPS."))
        worker = Thread(::connectionLoop, "jalora-android-companion").apply {
            isDaemon = true
            start()
        }
        heartbeatWorker = Thread(::heartbeatLoop, "jalora-android-heartbeat").apply {
            isDaemon = true
            start()
        }
    }

    private fun stopConnection() {
        running.set(false)
        paused.set(false)
        worker?.interrupt()
        heartbeatWorker?.interrupt()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun heartbeatLoop() {
        var failures = 0
        while (running.get()) {
            try {
                val state = SecureStore.read(this)
                if (state == null) {
                    Thread.sleep(HEARTBEAT_RETRY_BASE_MS)
                    continue
                }
                CompanionApi.request(
                    state.getString("api_origin"),
                    "/api/devices/heartbeat",
                    "POST",
                    token = state.getString("device_token"),
                    body = JSONObject(),
                )
                failures = 0
            } catch (error: InterruptedException) {
                if (!running.get()) return
            } catch (error: Exception) {
                failures = (failures + 1).coerceAtMost(5)
                Log.w(TAG, "Heartbeat request failed (${error.javaClass.simpleName}).")
                updateStatus(
                    "Heartbeat failed; reconnecting: ${error.message ?: error.javaClass.simpleName}",
                )
            }

            val retryDelay = if (failures == 0) {
                HEARTBEAT_INTERVAL_MS
            } else {
                (HEARTBEAT_RETRY_BASE_MS * (1L shl (failures - 1)))
                    .coerceAtMost(HEARTBEAT_MAX_RETRY_MS)
            }
            try {
                Thread.sleep(retryDelay)
            } catch (error: InterruptedException) {
                if (!running.get()) return
            }
        }
    }

    private fun connectionLoop() {
        try {
            while (running.get()) {
                if (paused.get()) {
                    Thread.sleep(250)
                    continue
                }
                try {
                    var state = SecureStore.read(this) ?: error("This phone is not paired.")
                    val origin = state.getString("api_origin")
                    val deviceToken = state.getString("device_token")
                    if (accessToken.isBlank() || System.currentTimeMillis() >= accessTokenExpiry) {
                        val session = CompanionApi.request(
                            origin,
                            "/api/devices/protocol/v1/session",
                            "POST",
                            token = deviceToken,
                        )
                        accessToken = session.getString("access_token")
                        accessTokenExpiry = System.currentTimeMillis() + 240_000
                    }
                    state = recoverInterruptedAction(state)
                    deliverSavedReceipt(state)
                    val installedApps = CompanionApi.installedApplications(this)
                    reportManifestIfChanged(state, installedApps)
                    val batch = CompanionApi.request(
                        origin,
                        "/api/devices/protocol/v1/commands?wait_seconds=20&client_type=android",
                        token = accessToken,
                    )
                    val commands = batch.optJSONArray("commands") ?: JSONArray()
                    for (index in 0 until commands.length()) {
                        if (!running.get()) break
                        while (paused.get() && running.get()) Thread.sleep(250)
                        if (running.get()) {
                            handleCommand(commands.getJSONObject(index), state, installedApps)
                        }
                    }
                    updateStatus("Connected. Waiting for a Jalora task.")
                } catch (error: CompanionHttpException) {
                    Log.w(TAG, "Companion request returned HTTP ${error.status}.")
                    if (error.status == 401) {
                        accessToken = ""
                        if (error.message?.contains("invalid or revoked", true) == true) {
                            SecureStore.clear(this)
                            updateStatus("Jalora no longer authorizes this phone. Pair it again if appropriate.")
                            stopConnection()
                            return
                        }
                    }
                    updateStatus("Connection error: ${error.message}")
                    Thread.sleep(RETRY_DELAY_MS)
                } catch (error: InterruptedException) {
                    if (!running.get()) return
                } catch (error: Exception) {
                    Log.w(TAG, "Companion loop failed (${error.javaClass.simpleName}).")
                    updateStatus("Companion error: ${error.message ?: error.javaClass.simpleName}")
                    Thread.sleep(RETRY_DELAY_MS)
                }
            }
        } finally {
            running.set(false)
            heartbeatWorker?.interrupt()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
    }

    private fun reportManifestIfChanged(state: JSONObject, installedApps: JSONArray) {
        val scopes = state.optJSONArray("scopes") ?: JSONArray()
        val scopeSet = (0 until scopes.length()).map { scopes.getString(it) }.toSet()
        val manifest = CompanionApi.manifest(installedApps, scopeSet)
        val digest = manifest.toString()
        if (state.optString("manifest_digest") != digest) {
            CompanionApi.request(
                state.getString("api_origin"),
                "/api/devices/protocol/v1/manifest",
                "PUT",
                accessToken,
                manifest,
            )
            state.put("manifest_digest", digest)
            SecureStore.write(this, state)
        }
    }

    private fun handleCommand(
        command: JSONObject,
        state: JSONObject,
        installedApps: JSONArray,
    ) {
        val id = command.getString("command_id")
        val nonce = command.getString("nonce")
        val operation = command.getString("operation")
        val arguments = command.optJSONObject("arguments") ?: JSONObject()
        Log.i(TAG, "Received protocol command id=$id operation=$operation.")
        val origin = state.getString("api_origin")
        val result = CompanionApi.request(
            origin,
            "/api/devices/protocol/v1/commands/${Uri.encode(id)}/decision",
            "POST",
            accessToken,
            JSONObject().put("nonce", nonce).put("approve", true),
        )
        if (!result.optBoolean("execute")) {
            Log.w(TAG, "Phone rejected protocol command decision id=$id.")
            updateStatus("The phone did not authorize this command.")
            return
        }
        updateStatus("Executing an authorized Jalora task.")
        val inflight = JSONObject().put("command_id", id).put("nonce", nonce)
        state.put("inflight_command", inflight)
        SecureStore.write(this, state)
        val actionResult = execute(operation, arguments, state, installedApps)
        val receipt = JSONObject()
            .put("nonce", nonce)
            .put("status", actionResult.status)
            .put("evidence", actionResult.evidence)
        actionResult.errorCode?.let { receipt.put("error_code", it) }
        state.put(
            "pending_receipt",
            JSONObject().put("command_id", id).put("body", receipt),
        )
        state.remove("inflight_command")
        SecureStore.write(this, state)
        deliverSavedReceipt(state)
        Log.i(
            TAG,
            "Protocol command finished id=$id operation=$operation status=${actionResult.status} " +
                "error_code=${actionResult.errorCode ?: "none"}.",
        )
        updateStatus("Action receipt recorded as ${actionResult.status}.")
    }

    private fun execute(
        operation: String,
        arguments: JSONObject,
        state: JSONObject,
        apps: JSONArray,
    ): ActionResult {
        if (operation == "browser.open_url") {
            if (!hasScope(state, "remote.browser.open_url")) {
                return browserFailure(
                    "ANDROID_SCOPE_REQUIRED",
                    "Opening links was not granted when this phone was paired.",
                )
            }
            val uri = Uri.parse(arguments.optString("url"))
            val host = uri.host
                ?: return browserFailure("ANDROID_URL_INVALID", "The link has no valid host.")
            return try {
                startActivity(
                    Intent(Intent.ACTION_VIEW, uri)
                        .addCategory(Intent.CATEGORY_BROWSABLE)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                )
                ActionResult(
                    "succeeded",
                    JSONObject()
                        .put("browser_open_receipt", true)
                        .put("opened_url_host", host),
                    null,
                )
            } catch (_: Exception) {
                browserFailure(
                    "ANDROID_URL_OPEN_FAILED",
                    "Android could not open the link in a browser.",
                )
            }
        }
        if (operation == "android.launcher.open") {
            if (!hasScope(state, "android.apps.launch")) {
                return ActionResult.failed("ANDROID_SCOPE_REQUIRED", "App-launch permission was not granted.")
            }
            val accessibility = ScreenAccessibilityService.instance
                ?: return ActionResult.uncertain(
                    "ANDROID_ACCESSIBILITY_REQUIRED",
                    "Enable Jalora Accessibility in Android Settings to search the launcher and open apps.",
                )
            return accessibility.launchInstalledApp(arguments.optString("app_name"))
        }
        if (operation.startsWith("android.screen.")) {
            val requiredScope = if (operation == "android.screen.inspect") {
                "android.screen.read"
            } else {
                "android.screen.control"
            }
            if (!hasScope(state, requiredScope)) {
                return ActionResult.failed("ANDROID_SCOPE_REQUIRED", "This screen permission was not granted.")
            }
            if (operation != "android.screen.inspect" &&
                !hasScope(state, "android.screen.read")
            ) {
                return ActionResult.failed(
                    "ANDROID_SCOPE_REQUIRED",
                    "Screen control requires the screen-read pairing permission.",
                )
            }
            val service = ScreenAccessibilityService.instance
                ?: return ActionResult.failed(
                    "ANDROID_ACCESSIBILITY_REQUIRED",
                    "Enable the Jalora accessibility service in Android Settings.",
                )
            return if (arguments.optString("app_id").isBlank()) {
                service.executeActive(operation, arguments)
            } else {
                service.execute(operation, arguments, apps)
            }
        }
        val appId = arguments.optString("app_id")
        val app = (0 until apps.length())
            .map { apps.getJSONObject(it) }
            .firstOrNull { it.optString("id") == appId }
            ?: return ActionResult.failed("ANDROID_APP_UNAVAILABLE", "This app is not installed or no longer launchable.")
        return when (operation) {
            "android.app.launch" -> {
                if (!hasScope(state, "android.apps.launch")) {
                    return ActionResult.failed("ANDROID_SCOPE_REQUIRED", "App-launch permission was not granted.")
                }
                val accessibility = ScreenAccessibilityService.instance
                    ?: return ActionResult.uncertain(
                        "ANDROID_ACCESSIBILITY_REQUIRED",
                        "Enable Jalora Accessibility in Android Settings to find and open the app from the launcher.",
                    )
                accessibility.launchInstalledApp(app.optString("name"))
            }
            else -> ActionResult.failed("ANDROID_OPERATION_UNSUPPORTED", "This Android operation is not supported.")
        }
    }

    private fun recoverInterruptedAction(initial: JSONObject): JSONObject {
        val state = SecureStore.read(this) ?: initial
        if (state.optJSONObject("pending_receipt") != null) return state
        val inflight = state.optJSONObject("inflight_command") ?: return state
        val receipt = JSONObject()
            .put("nonce", inflight.getString("nonce"))
            .put("status", "uncertain")
            .put(
                "evidence",
                JSONObject().put("reason", "Android companion restarted during an action; the action was not repeated."),
            )
            .put("error_code", "ANDROID_ACTION_RECOVERY_UNCERTAIN")
        state.put(
            "pending_receipt",
            JSONObject()
                .put("command_id", inflight.getString("command_id"))
                .put("body", receipt),
        )
        state.remove("inflight_command")
        SecureStore.write(this, state)
        return state
    }

    private fun deliverSavedReceipt(state: JSONObject) {
        val pendingReceipt = state.optJSONObject("pending_receipt") ?: return
        CompanionApi.request(
            state.getString("api_origin"),
            "/api/devices/protocol/v1/commands/${Uri.encode(pendingReceipt.getString("command_id"))}/receipt",
            "POST",
            accessToken,
            pendingReceipt.getJSONObject("body"),
        )
        state.remove("pending_receipt")
        SecureStore.write(this, state)
    }

    private fun hasScope(state: JSONObject, expected: String): Boolean {
        val scopes = state.optJSONArray("scopes") ?: return false
        return (0 until scopes.length()).any { scopes.optString(it) == expected }
    }

    private fun browserFailure(code: String, reason: String) = ActionResult(
        "failed",
        JSONObject().put("reason", reason),
        code,
    )

    private fun createChannels() {
        if (Build.VERSION.SDK_INT < 26) return
        val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(
            NotificationChannel(STATUS_CHANNEL, "Jalora companion status", NotificationManager.IMPORTANCE_LOW),
        )
    }

    private fun statusNotification(message: String): Notification {
        val open = PendingIntent.getActivity(
            this,
            1,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val builder = if (Build.VERSION.SDK_INT >= 26) {
            Notification.Builder(this, STATUS_CHANNEL)
        } else {
            Notification.Builder(this)
        }
        val notification = builder
            .setSmallIcon(android.R.drawable.stat_sys_upload_done)
            .setContentTitle("Jalora Device Companion")
            .setContentText(message)
            .setContentIntent(open)
            .setOngoing(true)
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setCategory(Notification.CATEGORY_SERVICE)
        notification.addAction(
            android.R.drawable.ic_media_pause,
            if (paused.get()) "Resume" else "Pause",
            serviceActionPendingIntent(
                if (paused.get()) CompanionActionReceiver.ACTION_RESUME
                else CompanionActionReceiver.ACTION_PAUSE,
                2,
            ),
        )
        notification.addAction(
            android.R.drawable.ic_menu_close_clear_cancel,
            "Stop",
            serviceActionPendingIntent(CompanionActionReceiver.ACTION_STOP, 3),
        )
        return notification.build()
    }

    private fun serviceActionPendingIntent(action: String, requestCode: Int): PendingIntent {
        val intent = Intent(this, CompanionActionReceiver::class.java).setAction(action)
        return PendingIntent.getBroadcast(
            this,
            requestCode,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun updateStatus(message: String) {
        if (running.get()) {
            (getSystemService(NOTIFICATION_SERVICE) as NotificationManager)
                .notify(NOTIFICATION_ID, statusNotification(message))
        }
    }

    override fun onDestroy() {
        running.set(false)
        worker?.interrupt()
        heartbeatWorker?.interrupt()
        super.onDestroy()
    }

    companion object {
        private const val TAG = "JaloraDeviceCompanion"
        const val ACTION_START = "com.jalora.companion.START"
        const val ACTION_STOP = "com.jalora.companion.STOP"
        const val ACTION_PAUSE = "com.jalora.companion.PAUSE"
        const val ACTION_RESUME = "com.jalora.companion.RESUME"
        private const val STATUS_CHANNEL = "jalora_status"
        private const val NOTIFICATION_ID = 7412
        private const val RETRY_DELAY_MS = 2500L
        private const val HEARTBEAT_INTERVAL_MS = 15_000L
        private const val HEARTBEAT_RETRY_BASE_MS = 2500L
        private const val HEARTBEAT_MAX_RETRY_MS = 30_000L
    }
}
