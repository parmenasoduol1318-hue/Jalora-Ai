package ai.jalora.companion

import android.accessibilityservice.AccessibilityService
import android.os.Bundle
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject
import java.util.ArrayDeque

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

    fun execute(operation: String, arguments: JSONObject, apps: JSONArray): ActionResult {
        val appId = arguments.optString("app_id")
        val app = (0 until apps.length())
            .map { apps.getJSONObject(it) }
            .firstOrNull { it.optString("id") == appId }
            ?: return ActionResult.failed("ANDROID_APP_NOT_ALLOWLISTED", "This app is not selected on this device.")
        val packageName = app.optString("package_name")
        if (packageName.isBlank()) {
            return ActionResult.failed("ANDROID_APP_NOT_ALLOWLISTED", "The selected app package is unavailable.")
        }
        val root = rootInActiveWindow
            ?: return ActionResult.failed("ANDROID_SCREEN_UNAVAILABLE", "Android did not expose an active accessibility tree.")
        if (root.packageName?.toString() != packageName) {
            root.recycle()
            return ActionResult.failed(
                "ANDROID_FOREGROUND_APP_CHANGED",
                "The foreground app is not the selected app. Jalora stopped without interacting.",
            )
        }
        return try {
            when (operation) {
                "android.screen.inspect" -> inspect(root, app.optString("name"))
                "android.screen.click" -> click(root, arguments)
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
        while (queue.isNotEmpty() && visited < MAX_NODES) {
            val node = queue.removeFirst()
            visited += 1
            val name = nodeName(node)
            if (name.isNotBlank() || node.isClickable || node.isEditable) {
                controls.put(
                    JSONObject()
                        .put("name", name.take(100))
                        .put("control_type", node.className?.toString()?.take(80) ?: "")
                        .put("automation_id", node.viewIdResourceName?.take(100) ?: "")
                        .put("enabled", node.isEnabled)
                        .put("visible", node.isVisibleToUser),
                )
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
            .put("truncated", visited >= MAX_NODES)
        return ActionResult(
            "succeeded",
            JSONObject()
                .put("operation_result", result)
                .put("accessible_controls", controls),
            null,
        )
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

    private fun nodeName(node: AccessibilityNodeInfo): String =
        node.text?.toString()?.takeIf { it.isNotBlank() }
            ?: node.contentDescription?.toString().orEmpty()

    companion object {
        private const val MAX_NODES = 300

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
