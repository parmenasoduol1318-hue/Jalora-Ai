package ai.jalora.companion

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.IBinder
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class DeviceCompanionService : Service() {
    private val running = AtomicBoolean(false)
    private val paused = AtomicBoolean(false)
    private val pending = mutableMapOf<String, PendingDecision>()
    private var worker: Thread? = null
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
            ACTION_DECISION -> applyDecision(intent)
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
    }

    private fun stopConnection() {
        running.set(false)
        paused.set(false)
        worker?.interrupt()
        synchronized(pending) {
            pending.values.forEach { it.latch.countDown() }
            pending.clear()
        }
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun applyDecision(intent: Intent) {
        val id = intent.getStringExtra(EXTRA_COMMAND_ID) ?: return
        val nonce = intent.getStringExtra(EXTRA_NONCE) ?: return
        val approve = intent.getBooleanExtra(EXTRA_APPROVE, false)
        synchronized(pending) {
            val item = pending[id]
            if (item != null && item.nonce == nonce) {
                item.approved = approve
                item.latch.countDown()
            }
        }
        (getSystemService(NOTIFICATION_SERVICE) as NotificationManager).cancel(actionNotificationId(id))
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
                    reportManifestIfChanged(state)
                    val batch = CompanionApi.request(
                        origin,
                        "/api/devices/protocol/v1/commands?wait_seconds=20&client_type=android",
                        token = accessToken,
                    )
                    val commands = batch.optJSONArray("commands") ?: JSONArray()
                    for (index in 0 until commands.length()) {
                        if (!running.get()) break
                        while (paused.get() && running.get()) Thread.sleep(250)
                        if (running.get()) handleCommand(commands.getJSONObject(index))
                    }
                    updateStatus("Connected. Waiting for an approved Jalora task.")
                } catch (error: CompanionHttpException) {
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
                    updateStatus("Companion error: ${error.message ?: error.javaClass.simpleName}")
                    Thread.sleep(RETRY_DELAY_MS)
                }
            }
        } finally {
            running.set(false)
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
    }

    private fun reportManifestIfChanged(state: JSONObject) {
        val applications = state.optJSONArray("applications") ?: JSONArray()
        val scopes = state.optJSONArray("scopes") ?: JSONArray()
        val scopeSet = (0 until scopes.length()).map { scopes.getString(it) }.toSet()
        val manifest = CompanionApi.manifest(applications, scopeSet)
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

    private fun handleCommand(command: JSONObject) {
        val id = command.getString("command_id")
        val nonce = command.getString("nonce")
        val operation = command.getString("operation")
        val arguments = command.optJSONObject("arguments") ?: JSONObject()
        val state = SecureStore.read(this) ?: return
        val appId = arguments.optString("app_id")
        val apps = state.optJSONArray("applications") ?: JSONArray()
        val app = (0 until apps.length())
            .map { apps.getJSONObject(it) }
            .firstOrNull { it.optString("id") == appId }
        val appName = app?.optString("name") ?: "Selected application"
        val decision = PendingDecision(nonce, CountDownLatch(1))
        synchronized(pending) { pending[id] = decision }
        showApprovalNotification(id, nonce, operation, appName, arguments)
        updateStatus("Action needs your approval on this phone.")
        val resolved = decision.latch.await(35, TimeUnit.SECONDS)
        synchronized(pending) { pending.remove(id) }
        (getSystemService(NOTIFICATION_SERVICE) as NotificationManager).cancel(actionNotificationId(id))
        if (!resolved) {
            updateStatus("Action approval expired locally; it was not executed.")
            return
        }
        val origin = state.getString("api_origin")
        val result = CompanionApi.request(
            origin,
            "/api/devices/protocol/v1/commands/${Uri.encode(id)}/decision",
            "POST",
            accessToken,
            JSONObject().put("nonce", nonce).put("approve", decision.approved),
        )
        if (!decision.approved || !result.optBoolean("execute")) {
            updateStatus("Action rejected on this phone.")
            return
        }
        val inflight = JSONObject().put("command_id", id).put("nonce", nonce)
        state.put("inflight_command", inflight)
        SecureStore.write(this, state)
        val actionResult = execute(operation, arguments, state, apps)
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
        updateStatus("Action receipt recorded as ${actionResult.status}.")
    }

    private fun execute(
        operation: String,
        arguments: JSONObject,
        state: JSONObject,
        apps: JSONArray,
    ): ActionResult {
        val appId = arguments.optString("app_id")
        val app = (0 until apps.length())
            .map { apps.getJSONObject(it) }
            .firstOrNull { it.optString("id") == appId }
            ?: return ActionResult.failed("ANDROID_APP_NOT_ALLOWLISTED", "This app is not selected on this phone.")
        val packageName = app.optString("package_name")
        return when (operation) {
            "android.app.launch" -> {
                if (!hasScope(state, "android.apps.launch")) {
                    return ActionResult.failed("ANDROID_SCOPE_REQUIRED", "App-launch permission was not granted.")
                }
                val launch = packageManager.getLaunchIntentForPackage(packageName)
                    ?: return ActionResult.failed("ANDROID_APP_UNAVAILABLE", "Android could not find a launchable activity for this app.")
                launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(launch)
                ActionResult.succeeded("launch_requested", verified = false)
            }
            "android.screen.inspect",
            "android.screen.back",
            "android.screen.click",
            "android.screen.scroll",
            "android.screen.type" -> {
                val requiredScope = if (operation == "android.screen.inspect") {
                    "android.screen.read"
                } else {
                    "android.screen.control"
                }
                if (!hasScope(state, requiredScope)) {
                    return ActionResult.failed("ANDROID_SCOPE_REQUIRED", "This screen permission was not granted.")
                }
                val service = JaloraAccessibilityService.instance
                    ?: return ActionResult.failed("ANDROID_ACCESSIBILITY_REQUIRED", "Enable the Jalora accessibility service in Android Settings.")
                service.execute(operation, arguments, apps)
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

    private fun showApprovalNotification(
        commandId: String,
        nonce: String,
        operation: String,
        appName: String,
        arguments: JSONObject,
    ) {
        val content = PendingIntent.getActivity(
            this,
            commandId.hashCode(),
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = Notification.Builder(this, ACTION_CHANNEL)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("Jalora action needs review")
            .setContentText("$operation · $appName")
            .setStyle(
                Notification.BigTextStyle().bigText(
                    "$operation\n$appName\n${arguments.toString(2)}",
                ),
            )
            .setContentIntent(content)
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_REMINDER)
            .addAction(
                android.R.drawable.ic_menu_close_clear_cancel,
                "Reject",
                approvalPendingIntent(commandId, nonce, approve = false),
            )
            .addAction(
                android.R.drawable.ic_menu_send,
                "Approve once",
                approvalPendingIntent(commandId, nonce, approve = true),
            )
            .build()
        (getSystemService(NOTIFICATION_SERVICE) as NotificationManager)
            .notify(actionNotificationId(commandId), notification)
    }

    private fun createChannels() {
        if (Build.VERSION.SDK_INT < 26) return
        val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(
            NotificationChannel(STATUS_CHANNEL, "Jalora companion status", NotificationManager.IMPORTANCE_LOW),
        )
        manager.createNotificationChannel(
            NotificationChannel(ACTION_CHANNEL, "Jalora action approvals", NotificationManager.IMPORTANCE_HIGH),
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

    private fun approvalPendingIntent(
        commandId: String,
        nonce: String,
        approve: Boolean,
    ): PendingIntent {
        val action = if (approve) {
            CompanionActionReceiver.ACTION_APPROVE
        } else {
            CompanionActionReceiver.ACTION_REJECT
        }
        val intent = Intent(this, CompanionActionReceiver::class.java)
            .setAction(action)
            .putExtra(EXTRA_COMMAND_ID, commandId)
            .putExtra(EXTRA_NONCE, nonce)
        return PendingIntent.getBroadcast(
            this,
            actionNotificationId(commandId) xor if (approve) 1 else 0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
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

    private fun actionNotificationId(commandId: String) = commandId.hashCode() xor ACTION_NOTIFICATION_MASK

    override fun onDestroy() {
        running.set(false)
        worker?.interrupt()
        super.onDestroy()
    }

    private data class PendingDecision(
        val nonce: String,
        val latch: CountDownLatch,
        @Volatile var approved: Boolean = false,
    )

    companion object {
        const val ACTION_START = "ai.jalora.companion.START"
        const val ACTION_STOP = "ai.jalora.companion.STOP"
        const val ACTION_PAUSE = "ai.jalora.companion.PAUSE"
        const val ACTION_RESUME = "ai.jalora.companion.RESUME"
        const val ACTION_DECISION = "ai.jalora.companion.DECISION"
        const val EXTRA_COMMAND_ID = "command_id"
        const val EXTRA_NONCE = "nonce"
        const val EXTRA_APPROVE = "approve"
        private const val STATUS_CHANNEL = "jalora_status"
        private const val ACTION_CHANNEL = "jalora_actions"
        private const val NOTIFICATION_ID = 7412
        private const val ACTION_NOTIFICATION_MASK = 0x4A41
        private const val RETRY_DELAY_MS = 2500L
    }
}
