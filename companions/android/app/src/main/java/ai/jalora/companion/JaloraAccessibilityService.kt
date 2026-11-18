package ai.jalora.companion

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.app.KeyguardManager
import android.graphics.Path
import android.graphics.Rect
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.DisplayMetrics
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject
import java.util.ArrayDeque
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class JaloraAccessibilityService : AccessibilityService() {
    override fun onAccessibilityEvent(event: android.view.accessibility.AccessibilityEvent?) = Unit

    override fun onInterrupt() = Unit

    override fun onServiceConnected() {
        instance = this
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    fun isAppForeground(packageName: String): Boolean {
        val root = rootInActiveWindow ?: return false
        return try {
            root.packageName?.toString() == packageName
        } finally {
            root.recycle()
        }
    }

    fun launchAppThroughLauncher(packageName: String, appName: String): ActionResult {
        val keyguard = getSystemService(KEYGUARD_SERVICE) as? KeyguardManager
        if (keyguard?.isKeyguardLocked == true) {
            return ActionResult.failed(
                "ANDROID_DEVICE_LOCKED",
                "Unlock the phone before asking Jalora to control its screen.",
            )
        }
        if (appName.isBlank()) {
            return ActionResult.failed("ANDROID_APP_UNAVAILABLE", "The installed app name is unavailable.")
        }
        if (isAppForeground(packageName)) return launchReceipt(appName)
        if (!performGlobalAction(GLOBAL_ACTION_HOME)) {
            return ActionResult.failed("ANDROID_HOME_UNAVAILABLE", "Android could not open the home screen.")
        }

        Thread.sleep(LAUNCHER_SETTLE_MS)
        var appDrawerOpened = false
        var searchAttempted = false
        var searchButtonOpened = false
        repeat(MAX_LAUNCHER_SWIPES + 1) {
            if (isAppForeground(packageName)) return launchReceipt(appName)
            val root = rootInActiveWindow
            if (root != null) {
                val appNode = findExactLabel(root, appName)
                if (appNode != null) {
                    val bounds = Rect().also(appNode::getBoundsInScreen)
                    val clicked = appNode.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                        || tapBounds(bounds)
                    if (appNode !== root) appNode.recycle()
                    root.recycle()
                    if (clicked && waitForForeground(packageName)) {
                        return launchReceipt(appName)
                    }
                    return ActionResult.failed(
                        "ANDROID_LAUNCH_UNVERIFIED",
                        "The launcher icon was activated, but Android did not show $appName in the foreground.",
                    )
                }

                if (appDrawerOpened && !searchAttempted) {
                    val searchField = findEditable(root)
                    if (searchField != null) {
                        val arguments = Bundle().apply {
                            putCharSequence(
                                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                                appName,
                            )
                        }
                        searchAttempted = true
                        val entered = searchField.performAction(
                            AccessibilityNodeInfo.ACTION_SET_TEXT,
                            arguments,
                        )
                        if (searchField !== root) searchField.recycle()
                        root.recycle()
                        if (entered) {
                            Thread.sleep(LAUNCHER_SETTLE_MS)
                            if (isAppForeground(packageName)) return launchReceipt(appName)
                            val results = rootInActiveWindow
                            if (results != null) {
                                val resultNode = findExactLabel(results, appName)
                                if (resultNode != null) {
                                    val bounds = Rect().also(resultNode::getBoundsInScreen)
                                    val clicked = resultNode.performAction(
                                        AccessibilityNodeInfo.ACTION_CLICK,
                                    ) || tapBounds(bounds)
                                    if (resultNode !== results) resultNode.recycle()
                                    results.recycle()
                                    if (clicked && waitForForeground(packageName)) {
                                        return launchReceipt(appName)
                                    }
                                    return ActionResult.failed(
                                        "ANDROID_LAUNCH_UNVERIFIED",
                                        "The launcher search result was activated, but Android did not show $appName in the foreground.",
                                    )
                                }
                                results.recycle()
                            }
                            return ActionResult.failed(
                                "ANDROID_APP_NOT_FOUND",
                                "The launcher did not show an accessible search result for $appName.",
                            )
                        }
                    } else if (!searchButtonOpened) {
                        val searchButton = findSearchButton(root)
                        if (searchButton != null) {
                            searchButtonOpened = searchButton.performAction(
                                AccessibilityNodeInfo.ACTION_CLICK,
                            )
                            if (searchButton !== root) searchButton.recycle()
                            root.recycle()
                            if (searchButtonOpened) {
                                Thread.sleep(SEARCH_SETTLE_MS)
                                return@repeat
                            }
                        } else {
                            root.recycle()
                        }
                    } else {
                        root.recycle()
                    }
                } else {
                    root.recycle()
                }
            }

            if (!swipeLauncher()) {
                return ActionResult.failed(
                    "ANDROID_LAUNCHER_UNAVAILABLE",
                    "Android did not expose a usable launcher screen or app drawer.",
                )
            }
            Thread.sleep(LAUNCHER_SETTLE_MS)
            appDrawerOpened = true
        }
        return ActionResult.failed(
            "ANDROID_APP_NOT_FOUND",
            "The app was not visible, searchable, or reachable in the Android launcher.",
        )
    }

    private fun launchReceipt(appName: String): ActionResult =
        ActionResult(
            "succeeded",
            JSONObject()
                .put(
                    "operation_result",
                    JSONObject()
                        .put("ok", true)
                        .put("status", "app_opened")
                        .put("app_name", appName)
                        .put("verified", true),
                ),
            null,
        )

    private fun waitForForeground(packageName: String): Boolean {
        repeat(20) {
            if (isAppForeground(packageName)) return true
            Thread.sleep(250)
        }
        return false
    }

    private fun findExactLabel(
        root: AccessibilityNodeInfo,
        label: String,
    ): AccessibilityNodeInfo? {
        val expected = normalize(label)
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var visited = 0
        while (queue.isNotEmpty() && visited < MAX_NODES) {
            val node = queue.removeFirst()
            visited += 1
            if (
                node.isVisibleToUser &&
                (normalize(node.text?.toString()) == expected ||
                    normalize(node.contentDescription?.toString()) == expected)
            ) {
                queue.forEach { it.recycle() }
                return node
            }
            for (index in 0 until node.childCount) {
                node.getChild(index)?.let(queue::addLast)
            }
            if (node !== root) node.recycle()
        }
        queue.forEach { it.recycle() }
        return null
    }

    private fun findEditable(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var visited = 0
        while (queue.isNotEmpty() && visited < MAX_NODES) {
            val node = queue.removeFirst()
            visited += 1
            if (node.isVisibleToUser && node.isEditable && !node.isPassword) {
                queue.forEach { it.recycle() }
                return node
            }
            for (index in 0 until node.childCount) {
                node.getChild(index)?.let(queue::addLast)
            }
            if (node !== root) node.recycle()
        }
        queue.forEach { it.recycle() }
        return null
    }

    private fun findSearchButton(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var visited = 0
        while (queue.isNotEmpty() && visited < MAX_NODES) {
            val node = queue.removeFirst()
            visited += 1
            val label = normalize(
                "${node.text?.toString().orEmpty()} ${node.contentDescription?.toString().orEmpty()}",
            )
            if (
                node.isVisibleToUser && node.isClickable &&
                ("search" in label || "find apps" in label)
            ) {
                queue.forEach { it.recycle() }
                return node
            }
            for (index in 0 until node.childCount) {
                node.getChild(index)?.let(queue::addLast)
            }
            if (node !== root) node.recycle()
        }
        queue.forEach { it.recycle() }
        return null
    }

    private fun swipeLauncher(): Boolean {
        val metrics: DisplayMetrics = resources.displayMetrics
        val path = Path().apply {
            moveTo(metrics.widthPixels * 0.5f, metrics.heightPixels * 0.82f)
            lineTo(metrics.widthPixels * 0.5f, metrics.heightPixels * 0.30f)
        }
        val completed = CountDownLatch(1)
        var succeeded = false
        val dispatched = dispatchGesture(
            GestureDescription.Builder()
                .addStroke(GestureDescription.StrokeDescription(path, 0, SWIPE_DURATION_MS))
                .build(),
            object : GestureResultCallback() {
                override fun onCompleted(gesture: GestureDescription?) {
                    succeeded = true
                    completed.countDown()
                }

                override fun onCancelled(gesture: GestureDescription?) {
                    completed.countDown()
                }
            },
            Handler(Looper.getMainLooper()),
        )
        return dispatched && completed.await(GESTURE_TIMEOUT_MS, TimeUnit.MILLISECONDS) && succeeded
    }

    private fun tapBounds(bounds: Rect): Boolean {
        if (bounds.isEmpty) return false
        val path = Path().apply {
            moveTo(bounds.exactCenterX(), bounds.exactCenterY())
            lineTo(bounds.exactCenterX() + 1f, bounds.exactCenterY() + 1f)
        }
        val completed = CountDownLatch(1)
        var succeeded = false
        val dispatched = dispatchGesture(
            GestureDescription.Builder()
                .addStroke(GestureDescription.StrokeDescription(path, 0, TAP_DURATION_MS))
                .build(),
            object : GestureResultCallback() {
                override fun onCompleted(gesture: GestureDescription?) {
                    succeeded = true
                    completed.countDown()
                }

                override fun onCancelled(gesture: GestureDescription?) {
                    completed.countDown()
                }
            },
            Handler(Looper.getMainLooper()),
        )
        return dispatched && completed.await(GESTURE_TIMEOUT_MS, TimeUnit.MILLISECONDS) && succeeded
    }

    private fun normalize(value: String?): String =
        value.orEmpty().trim().replace(Regex("\\s+"), " ").lowercase()

    fun execute(operation: String, arguments: JSONObject, apps: JSONArray): ActionResult {
        val appId = arguments.optString("app_id")
        val app = (0 until apps.length())
            .map { apps.getJSONObject(it) }
            .firstOrNull { it.optString("id") == appId }
            ?: return ActionResult.failed("ANDROID_APP_UNAVAILABLE", "This app is not installed or no longer launchable.")
        val packageName = app.optString("package_name")
        if (packageName.isBlank()) {
            return ActionResult.failed("ANDROID_APP_UNAVAILABLE", "The installed app package is unavailable.")
        }
        val root = rootInActiveWindow
            ?: return ActionResult.failed("ANDROID_SCREEN_UNAVAILABLE", "Android did not expose an active accessibility tree.")
        if (root.packageName?.toString() != packageName) {
            root.recycle()
            return ActionResult.failed(
                "ANDROID_FOREGROUND_APP_CHANGED",
                "The foreground app changed. Jalora stopped without interacting.",
            )
        }
        return try {
            if (
                operation in AUTHENTICATION_GUARDED_OPERATIONS &&
                hasVisibleAuthenticationInput(root)
            ) {
                return ActionResult.failed(
                    "ANDROID_AUTHENTICATION_REQUIRED",
                    "A password, PIN, or verification-code field is visible. Enter credentials and complete this step directly on the device.",
                )
            }
            when (operation) {
                "android.screen.inspect" -> inspect(root, app.optString("name"))
                "android.screen.back" -> back(root)
                "android.screen.click" -> click(root, arguments)
                "android.screen.scroll" -> scroll(root, arguments)
                "android.screen.type" -> type(root, arguments)
                else -> ActionResult.failed("ANDROID_OPERATION_UNSUPPORTED", "This Android operation is not supported.")
            }
        } finally {
            root.recycle()
        }
    }

    private fun inspect(root: AccessibilityNodeInfo, appName: String): ActionResult {
        val controls = JSONArray()
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var visited = 0
        var controlsTruncated = false
        while (queue.isNotEmpty() && visited < MAX_NODES) {
            val node = queue.removeFirst()
            visited += 1
            val name = nodeName(node)
            if (name.isNotBlank() || node.isClickable || node.isEditable) {
                if (controls.length() < MAX_CONTROLS) {
                    controls.put(
                        JSONObject()
                            .put("name", name.take(100))
                            .put("control_type", node.className?.toString()?.take(80) ?: "")
                            .put("automation_id", node.viewIdResourceName?.take(100) ?: "")
                            .put("enabled", node.isEnabled)
                            .put("visible", node.isVisibleToUser),
                    )
                } else {
                    controlsTruncated = true
                }
            }
            for (index in 0 until node.childCount) {
                node.getChild(index)?.let(queue::addLast)
            }
            if (node !== root) node.recycle()
        }
        queue.forEach { it.recycle() }
        val result = JSONObject()
            .put("ok", true)
            .put("app_name", appName)
            .put("status", "inspected")
            .put("control_count", visited)
            .put("truncated", visited >= MAX_NODES || controlsTruncated)
        return ActionResult(
            "succeeded",
            JSONObject()
                .put("operation_result", result)
                .put("accessible_controls", controls),
            null,
        )
    }

    private fun hasVisibleAuthenticationInput(root: AccessibilityNodeInfo): Boolean {
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var visited = 0
        while (queue.isNotEmpty() && visited < MAX_NODES) {
            val node = queue.removeFirst()
            visited += 1
            val labels = listOf(
                node.hintText?.toString().orEmpty(),
                node.contentDescription?.toString().orEmpty(),
                node.viewIdResourceName.orEmpty(),
            ).joinToString(" ")
            val sensitive = node.isVisibleToUser && (
                node.isPassword ||
                    (node.isEditable && node.isFocused &&
                        AUTHENTICATION_LABEL_PATTERN.containsMatchIn(labels))
            )
            for (index in 0 until node.childCount) {
                node.getChild(index)?.let(queue::addLast)
            }
            if (node !== root) node.recycle()
            if (sensitive) {
                queue.forEach { it.recycle() }
                return true
            }
        }
        val scanIncomplete = queue.isNotEmpty()
        queue.forEach { it.recycle() }
        return scanIncomplete
    }

    private fun click(root: AccessibilityNodeInfo, arguments: JSONObject): ActionResult {
        val target = uniqueNode(root, arguments)
            ?: return ActionResult.failed("ANDROID_CONTROL_NOT_UNIQUE", "The target must match exactly one accessible control.")
        if (!target.isEnabled || !target.isVisibleToUser || !target.isClickable) {
            if (target !== root) target.recycle()
            return ActionResult.failed("ANDROID_CONTROL_UNAVAILABLE", "The selected control is disabled, hidden, or not clickable.")
        }
        val before = signature(root)
        val accepted = target.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        if (target !== root) target.recycle()
        if (!accepted) {
            return ActionResult.failed("ANDROID_CLICK_FAILED", "Android did not accept the accessibility click.")
        }
        Thread.sleep(250)
        val afterRoot = rootInActiveWindow
            ?: return ActionResult.uncertain("ANDROID_CLICK_UNVERIFIED", "The click was accepted, but no active screen is available.")
        val after = try {
            if (afterRoot.packageName != root.packageName) {
                return ActionResult.uncertain("ANDROID_FOREGROUND_APP_CHANGED", "The foreground app changed; the click result is uncertain.")
            }
            signature(afterRoot)
        } finally {
            afterRoot.recycle()
        }
        return if (before == after) {
            ActionResult.uncertain("ANDROID_CLICK_UNVERIFIED", "The click was accepted, but no accessibility state change was observed.")
        } else {
            ActionResult.succeeded("clicked", verified = true)
        }
    }

    private fun back(root: AccessibilityNodeInfo): ActionResult {
        val before = signature(root)
        if (!performGlobalAction(GLOBAL_ACTION_BACK)) {
            return ActionResult.failed(
                "ANDROID_BACK_FAILED",
                "Android did not accept the back-navigation action.",
            )
        }
        Thread.sleep(250)
        val afterRoot = rootInActiveWindow
            ?: return ActionResult.uncertain(
                "ANDROID_BACK_UNVERIFIED",
                "Android accepted back navigation, but no active screen is available.",
            )
        val after = try {
            if (afterRoot.packageName != root.packageName) {
                return ActionResult.uncertain(
                    "ANDROID_FOREGROUND_APP_CHANGED",
                    "Back navigation changed the foreground app; the result is uncertain.",
                )
            }
            signature(afterRoot)
        } finally {
            afterRoot.recycle()
        }
        return if (before == after) {
            ActionResult.uncertain(
                "ANDROID_BACK_UNVERIFIED",
                "Android accepted back navigation, but no accessibility state change was observed.",
            )
        } else {
            ActionResult.succeeded("navigated_back", verified = true)
        }
    }

    private fun type(root: AccessibilityNodeInfo, arguments: JSONObject): ActionResult {
        val target = uniqueNode(root, arguments)
            ?: return ActionResult.failed("ANDROID_CONTROL_NOT_UNIQUE", "The target must match exactly one accessible control.")
        if (!target.isEnabled || !target.isVisibleToUser || !target.isEditable || target.isPassword) {
            if (target !== root) target.recycle()
            return ActionResult.failed("ANDROID_TEXT_TARGET_UNSUPPORTED", "Text may only be entered into a visible, enabled, non-password edit field.")
        }
        val text = arguments.optString("text")
        if (text.isEmpty() || text.length > 2000) {
            if (target !== root) target.recycle()
            return ActionResult.failed("ANDROID_TEXT_INVALID", "Text must contain between 1 and 2,000 characters.")
        }
        val bundle = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        val accepted = target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, bundle)
        val verified = target.text?.toString() == text
        if (target !== root) target.recycle()
        if (!accepted) {
            return ActionResult.failed("ANDROID_TEXT_INPUT_FAILED", "Android did not accept text in the selected edit field.")
        }
        if (!verified) {
            return ActionResult.uncertain(
                "ANDROID_TEXT_INPUT_UNVERIFIED",
                "Android accepted the text action but the selected field did not confirm the value.",
            )
        }
        return ActionResult.succeeded("text_entered", verified = verified)
    }

    private fun scroll(root: AccessibilityNodeInfo, arguments: JSONObject): ActionResult {
        val target = uniqueNode(root, arguments)
            ?: return ActionResult.failed("ANDROID_CONTROL_NOT_UNIQUE", "The target must match exactly one accessible control.")
        if (!target.isEnabled || !target.isVisibleToUser || !target.isScrollable) {
            if (target !== root) target.recycle()
            return ActionResult.failed("ANDROID_SCROLL_TARGET_UNSUPPORTED", "The selected control is disabled, hidden, or not scrollable.")
        }
        val direction = arguments.optString("direction")
        val scrollAction = when (direction) {
            "down" -> AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
            "up" -> AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
            else -> {
                if (target !== root) target.recycle()
                return ActionResult.failed("ANDROID_SCROLL_DIRECTION_INVALID", "The scroll direction must be up or down.")
            }
        }
        val before = signature(root)
        val accepted = target.performAction(scrollAction)
        if (target !== root) target.recycle()
        if (!accepted) {
            return ActionResult.failed("ANDROID_SCROLL_FAILED", "Android did not accept the accessibility scroll.")
        }
        Thread.sleep(250)
        val afterRoot = rootInActiveWindow
            ?: return ActionResult.uncertain("ANDROID_SCROLL_UNVERIFIED", "The scroll was accepted, but no active screen is available.")
        val after = try {
            if (afterRoot.packageName != root.packageName) {
                return ActionResult.uncertain("ANDROID_FOREGROUND_APP_CHANGED", "The foreground app changed; the scroll result is uncertain.")
            }
            signature(afterRoot)
        } finally {
            afterRoot.recycle()
        }
        return if (before == after) {
            ActionResult.uncertain("ANDROID_SCROLL_UNVERIFIED", "The scroll was accepted, but no accessibility state change was observed.")
        } else {
            ActionResult.succeeded("scrolled", verified = true)
        }
    }

    private fun uniqueNode(root: AccessibilityNodeInfo, arguments: JSONObject): AccessibilityNodeInfo? {
        val name = arguments.optString("name")
        val automationId = arguments.optString("automation_id")
        if (name.isBlank() && automationId.isBlank()) return null
        val matches = ArrayList<AccessibilityNodeInfo>()
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var visited = 0
        while (queue.isNotEmpty() && visited < MAX_NODES) {
            val node = queue.removeFirst()
            visited += 1
            val nameMatches = name.isBlank() || nodeName(node) == name
            val idMatches = automationId.isBlank() || node.viewIdResourceName == automationId
            if (nameMatches && idMatches) matches.add(node) else if (node !== root) node.recycle()
            for (index in 0 until node.childCount) {
                node.getChild(index)?.let(queue::addLast)
            }
        }
        queue.forEach { it.recycle() }
        if (matches.size != 1) {
            matches.forEach { it.recycle() }
            return null
        }
        return matches.single()
    }

    private fun signature(root: AccessibilityNodeInfo): List<String> {
        val values = ArrayList<String>()
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var visited = 0
        while (queue.isNotEmpty() && visited < MAX_NODES) {
            val node = queue.removeFirst()
            visited += 1
            values.add("${node.className}|${nodeName(node)}|${node.viewIdResourceName}")
            for (index in 0 until node.childCount) {
                node.getChild(index)?.let(queue::addLast)
            }
            if (node !== root) node.recycle()
        }
        queue.forEach { it.recycle() }
        return values
    }

    private fun nodeName(node: AccessibilityNodeInfo): String {
        if (node.isPassword) return "Secure input"
        return node.text?.toString()?.takeIf { it.isNotBlank() }
            ?: node.contentDescription?.toString().orEmpty()
    }

    companion object {
        private const val MAX_NODES = 300
        private const val MAX_CONTROLS = 40
        private const val MAX_LAUNCHER_SWIPES = 8
        private const val LAUNCHER_SETTLE_MS = 500L
        private const val SEARCH_SETTLE_MS = 300L
        private const val SWIPE_DURATION_MS = 400L
        private const val TAP_DURATION_MS = 80L
        private const val GESTURE_TIMEOUT_MS = 2_000L
        private val AUTHENTICATION_GUARDED_OPERATIONS = setOf(
            "android.screen.click",
            "android.screen.scroll",
            "android.screen.type",
        )
        private val AUTHENTICATION_LABEL_PATTERN = Regex(
            """\b(pin|m[- ]?pin|passcode|password|one[- ]time|otp|verification code|security code)\b""",
            RegexOption.IGNORE_CASE,
        )

        @Volatile
        var instance: JaloraAccessibilityService? = null
            private set
    }
}

data class ActionResult(
    val status: String,
    val evidence: JSONObject,
    val errorCode: String?,
) {
    companion object {
        fun failed(code: String, reason: String) = ActionResult(
            "failed",
            JSONObject().put("operation_result", JSONObject().put("ok", false).put("reason", reason)),
            code,
        )

        fun uncertain(code: String, reason: String) = ActionResult(
            "uncertain",
            JSONObject().put("reason", reason),
            code,
        )

        fun succeeded(status: String, verified: Boolean) = ActionResult(
            "succeeded",
            JSONObject().put(
                "operation_result",
                JSONObject().put("ok", true).put("status", status).put("verified", verified),
            ),
            null,
        )
    }
}
