package ai.jalora.companion

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.app.KeyguardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.ColorSpace
import android.graphics.Path
import android.graphics.Rect
import android.hardware.HardwareBuffer
import android.os.Bundle
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.DisplayMetrics
import android.util.Base64
import android.view.Display
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.util.ArrayDeque
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executor
import java.util.concurrent.TimeUnit
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions

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

    fun launchAppThroughLauncher(appName: String): ActionResult {
        val keyguard = getSystemService(KEYGUARD_SERVICE) as? KeyguardManager
        if (keyguard?.isKeyguardLocked == true) {
            return ActionResult.failed(
                "ANDROID_DEVICE_LOCKED",
                "Unlock the phone before asking Jalora to control its screen.",
            )
        }
        if (appName.isBlank()) {
            return ActionResult.failed("ANDROID_APP_NAME_INVALID", "Enter an app name to search for.")
        }
        val expectedPackage = matchingLauncherPackage(appName)
        val launcherPackage = resolveLauncherPackage()
            ?: return ActionResult.failed(
                "ANDROID_LAUNCHER_UNAVAILABLE",
                "Android did not expose a default home app to the companion.",
            )
        if (!performGlobalAction(GLOBAL_ACTION_HOME)) {
            return ActionResult.failed("ANDROID_HOME_UNAVAILABLE", "Android could not open the home screen.")
        }
        if (!waitForLauncher(launcherPackage)) {
            return ActionResult.failed(
                "ANDROID_LAUNCHER_UNAVAILABLE",
                "Android did not switch to its home screen. Jalora stopped before tapping an app.",
            )
        }
        var appDrawerOpened = false
        var searchAttempted = false
        var searchButtonOpened = false
        repeat(MAX_LAUNCHER_SWIPES + 1) {
            if (hasLeftLauncher(launcherPackage)) {
                return launchReceipt(appName, expectedPackage)
            }
            val root = rootInActiveWindow
            if (root != null) {
                val appNode = findExactLabel(root, appName)
                if (appNode != null) {
                    val bounds = Rect().also(appNode::getBoundsInScreen)
                    val clicked = appNode.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                        || tapBounds(bounds)
                    if (appNode !== root) appNode.recycle()
                    root.recycle()
                    if (clicked && waitForForegroundChange(launcherPackage)) {
                        return launchReceipt(appName, expectedPackage)
                    }
                    return ActionResult.uncertain(
                        "ANDROID_LAUNCH_UNVERIFIED",
                        "The launcher item for $appName was activated, but the foreground screen did not change.",
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
                            if (hasLeftLauncher(launcherPackage)) {
                                return launchReceipt(appName, expectedPackage)
                            }
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
                                    if (clicked && waitForForegroundChange(launcherPackage)) {
                                        return launchReceipt(appName, expectedPackage)
                                    }
                                    return ActionResult.uncertain(
                                        "ANDROID_LAUNCH_UNVERIFIED",
                                        "The launcher search result for $appName was activated, but the foreground screen did not change.",
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
            "The launcher did not expose an app named $appName on the home screen, in the app drawer, or in search.",
        )
    }

    private fun launchReceipt(
        appName: String,
        expectedPackage: String?,
    ): ActionResult {
        val screen = inspectActiveScreen()
        val foregroundPackage = activePackage()
        if (
            screen.status != "succeeded" ||
            expectedPackage == null ||
            foregroundPackage != expectedPackage
        ) {
            val reason = when {
                screen.status != "succeeded" ->
                    "The resulting screen could not be read."
                expectedPackage == null ->
                    "The requested app could not be matched to one installed launcher app."
                else ->
                    "Android foreground package $foregroundPackage did not match the requested app."
            }
            val evidence = JSONObject(screen.evidence.toString())
            evidence.put(
                "operation_result",
                JSONObject()
                    .put("ok", false)
                    .put("status", "app_open_unverified")
                    .put("app_name", appName)
                    .put("verified", false)
                    .put("reason", reason),
            )
            return ActionResult(
                "uncertain",
                evidence,
                "ANDROID_LAUNCH_UNVERIFIED",
            )
        }
        val evidence = JSONObject(screen.evidence.toString())
        evidence.put(
            "operation_result",
            JSONObject()
                .put("ok", true)
                .put("status", "app_opened")
                .put("app_name", appName)
                .put("verified", true),
        )
        return ActionResult("succeeded", evidence, null)
    }

    private fun matchingLauncherPackage(appName: String): String? {
        val expected = normalize(appName)
        val matches = CompanionApi.installedApplications(this)
            .let { applications ->
                (0 until applications.length()).mapNotNull { index ->
                    applications.optJSONObject(index)
                        ?.takeIf { normalize(it.optString("name")) == expected }
                        ?.optString("package_name")
                        ?.takeIf { it.isNotBlank() }
                }
            }
        return matches.singleOrNull()
    }

    private fun activePackage(): String? {
        val root = rootInActiveWindow ?: return null
        return try {
            root.packageName?.toString()
        } finally {
            root.recycle()
        }
    }

    private fun resolveLauncherPackage(): String? {
        val homeIntent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
        val resolved = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            packageManager.resolveActivity(
                homeIntent,
                PackageManager.ResolveInfoFlags.of(
                    PackageManager.MATCH_DEFAULT_ONLY.toLong(),
                ),
            )
        } else {
            @Suppress("DEPRECATION")
            packageManager.resolveActivity(homeIntent, PackageManager.MATCH_DEFAULT_ONLY)
        }
        return resolved?.activityInfo?.packageName
    }

    private fun waitForLauncher(launcherPackage: String): Boolean {
        repeat(FOREGROUND_CHANGE_ATTEMPTS) {
            if (activePackage() == launcherPackage) return true
            Thread.sleep(FOREGROUND_CHANGE_INTERVAL_MS)
        }
        return false
    }

    private fun hasLeftLauncher(launcherPackage: String): Boolean {
        val current = activePackage()
        return current != null &&
            current != launcherPackage &&
            current != packageName
    }

    private fun waitForForegroundChange(launcherPackage: String): Boolean {
        repeat(FOREGROUND_CHANGE_ATTEMPTS) {
            if (hasLeftLauncher(launcherPackage)) return true
            Thread.sleep(FOREGROUND_CHANGE_INTERVAL_MS)
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
            val parent = node.parent
            val hasClickableTarget = node.isClickable || parent?.isClickable == true
            parent?.recycle()
            if (
                node.isVisibleToUser &&
                hasClickableTarget &&
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
        val centerX = bounds.exactCenterX()
        val centerY = bounds.exactCenterY()
        val path = Path().apply {
            moveTo(centerX, centerY)
            lineTo(centerX + 1f, centerY + 1f)
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
        return executeOnActiveScreen(operation, arguments, packageName, app.optString("name"))
    }

    fun executeActive(operation: String, arguments: JSONObject): ActionResult =
        executeOnActiveScreen(operation, arguments, null, "")

    private fun executeOnActiveScreen(
        operation: String,
        arguments: JSONObject,
        expectedPackage: String?,
        appName: String,
    ): ActionResult {
        val keyguard = getSystemService(KEYGUARD_SERVICE) as? KeyguardManager
        if (keyguard?.isKeyguardLocked == true) {
            return ActionResult.failed(
                "ANDROID_DEVICE_LOCKED",
                "Unlock the phone before asking Jalora to control its screen.",
            )
        }
        val root = rootInActiveWindow
            ?: return ActionResult.failed("ANDROID_SCREEN_UNAVAILABLE", "Android did not expose an active accessibility tree.")
        if (expectedPackage != null && root.packageName?.toString() != expectedPackage) {
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
            val action = when (operation) {
                "android.screen.inspect" -> inspect(root, appName)
                "android.screen.back" -> back(root)
                "android.screen.notifications" -> openSystemPanel(
                    root,
                    GLOBAL_ACTION_NOTIFICATIONS,
                    "notifications",
                )
                "android.screen.quick_settings" -> openSystemPanel(
                    root,
                    GLOBAL_ACTION_QUICK_SETTINGS,
                    "quick_settings",
                )
                "android.screen.app_switcher" -> openSystemPanel(
                    root,
                    GLOBAL_ACTION_RECENTS,
                    "app_switcher",
                )
                "android.screen.key" -> pressKey(root, arguments)
                "android.screen.set_brightness" -> setBrightness(root, arguments)
                "android.screen.click" -> click(root, arguments)
                "android.screen.long_press" -> longPress(root, arguments)
                "android.screen.tap" -> tap(root, arguments)
                "android.screen.scroll" -> scroll(root, arguments)
                "android.screen.swipe" -> swipeScreen(root, arguments.optString("direction"))
                "android.screen.type" -> type(root, arguments)
                else -> ActionResult.failed("ANDROID_OPERATION_UNSUPPORTED", "This Android operation is not supported.")
            }
            if (operation == "android.screen.inspect" || action.status == "failed") {
                action
            } else {
                withRefreshedScreen(action, appName)
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
                if (controls.length() < ACCESSIBILITY_CONTROL_LIMIT) {
                    val bounds = Rect().also(node::getBoundsInScreen)
                    controls.put(
                        JSONObject()
                            .put("name", name.take(80))
                            .put("control_type", node.className?.toString()?.take(40) ?: "")
                            .put("automation_id", node.viewIdResourceName?.take(60) ?: "")
                            .put("enabled", node.isEnabled)
                            .put("visible", node.isVisibleToUser)
                            .put("scrollable", node.isScrollable)
                            .put("bounds", normalizedBounds(bounds)),
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
        val screenOcr = if (hasVisibleAuthenticationInput(root)) {
            ScreenOcr(
                "",
                emptyList(),
                "redacted",
                "Visible authentication input prevents OCR capture.",
                false,
            )
        } else {
            recognizeScreenText()
        }
        var ocrControlsTruncated = screenOcr.truncated
        screenOcr.controls.forEach { control ->
            if (controls.length() < MAX_CONTROLS) {
                controls.put(control)
            } else {
                ocrControlsTruncated = true
            }
        }
        val metrics = resources.displayMetrics
        val result = JSONObject()
            .put("ok", true)
            .put("app_name", appName)
            .put("status", "inspected")
            .put("control_count", visited)
            .put(
                "truncated",
                visited >= MAX_NODES || controlsTruncated || ocrControlsTruncated,
            )
            .put("screen_width", metrics.widthPixels.coerceIn(1, MAX_SCREEN_DIMENSION))
            .put("screen_height", metrics.heightPixels.coerceIn(1, MAX_SCREEN_DIMENSION))
            .put("ocr_status", screenOcr.status)
        return ActionResult(
            "succeeded",
            JSONObject()
                .put("operation_result", result)
                .put("accessible_controls", controls)
                .put("screen_ocr_text", screenOcr.text)
                .put("screen_ocr_status", screenOcr.status)
                .put("screen_ocr_reason", screenOcr.reason),
                null,
        ).also { result ->
                screenOcr.imageBase64?.let {
                    result.evidence.put("screen_image_base64", it)
                }
        }
    }

    private fun normalizedBounds(bounds: Rect): JSONObject {
        val width = resources.displayMetrics.widthPixels.coerceIn(1, MAX_SCREEN_DIMENSION)
        val height = resources.displayMetrics.heightPixels.coerceIn(1, MAX_SCREEN_DIMENSION)
        val left = bounds.left.coerceIn(0, width)
        val top = bounds.top.coerceIn(0, height)
        return JSONObject()
            .put("left", left)
            .put("top", top)
            .put("right", bounds.right.coerceIn(left, width))
            .put("bottom", bounds.bottom.coerceIn(top, height))
    }

    private fun withRefreshedScreen(action: ActionResult, appName: String): ActionResult {
        val screen = inspectActiveScreen()
        if (screen.status != "succeeded") {
            return ActionResult.uncertain(
                "ANDROID_POST_ACTION_SCREEN_UNAVAILABLE",
                "The action was attempted, but Jalora could not inspect the resulting screen.",
            )
        }
        val evidence = JSONObject(action.evidence.toString())
        for (key in listOf(
            "accessible_controls",
            "screen_ocr_text",
            "screen_ocr_status",
            "screen_ocr_reason",
            "screen_image_base64",
        )) {
            if (screen.evidence.has(key)) {
                evidence.put(key, screen.evidence.get(key))
            }
        }
        val actionResult = evidence.optJSONObject("operation_result")
        actionResult?.put("app_name", appName)
        return ActionResult(action.status, evidence, action.errorCode)
    }

    private fun inspectActiveScreen(): ActionResult {
        val root = rootInActiveWindow
            ?: return ActionResult.failed(
                "ANDROID_SCREEN_UNAVAILABLE",
                "Android did not expose an active accessibility tree.",
            )
        return try {
            inspect(root, "")
        } finally {
            root.recycle()
        }
    }

    private fun recognizeScreenText(): ScreenOcr {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            return ScreenOcr(
                "",
                emptyList(),
                "unavailable",
                "Android screenshot capture requires Android 11 or later.",
                false,
            )
        }
        val captured = captureScreenshot()
        val bitmap = captured.first
            ?: return ScreenOcr("", emptyList(), "unavailable", captured.second, false)
        val imageBase64 = encodeScreenImage(bitmap)
        val recognizer = try {
            TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
        } catch (error: IllegalStateException) {
            return ScreenOcr(
                "",
                emptyList(),
                "unavailable",
                "On-device text recognition could not start: ${error.message.orEmpty()}".take(
                    MAX_OCR_REASON_CHARS,
                ),
                false,
                imageBase64,
            )
        }
        return try {
            val completed = CountDownLatch(1)
            var recognized: com.google.mlkit.vision.text.Text? = null
            var failure: String? = null
            recognizer.process(InputImage.fromBitmap(bitmap, 0))
                .addOnSuccessListener {
                    recognized = it
                    completed.countDown()
                }
                .addOnFailureListener {
                    failure = "On-device text recognition failed."
                    completed.countDown()
                }
            if (!completed.await(OCR_TIMEOUT_MS, TimeUnit.MILLISECONDS)) {
                return ScreenOcr(
                    "",
                    emptyList(),
                    "unavailable",
                    "On-device text recognition timed out.",
                    false,
                    imageBase64,
                )
            }
            val text = recognized
                ?: return ScreenOcr(
                    "",
                    emptyList(),
                    "unavailable",
                    failure ?: "On-device text recognition returned no result.",
                    false,
                    imageBase64,
                )
            val allControls = text.textBlocks
                .flatMap { it.lines }
                .mapNotNull { line ->
                    val bounds = line.boundingBox ?: return@mapNotNull null
                    if (line.text.isBlank()) return@mapNotNull null
                    JSONObject()
                        .put("name", line.text.take(80))
                        .put("control_type", "OCRText")
                        .put("automation_id", "")
                        .put("enabled", true)
                        .put("visible", true)
                        .put("scrollable", false)
                        .put("bounds", normalizedBounds(bounds))
                }
            val controls = allControls.take(MAX_OCR_CONTROLS)
            ScreenOcr(
                text.text.take(MAX_OCR_CHARS),
                controls,
                "available",
                "",
                text.text.length > MAX_OCR_CHARS ||
                    allControls.size > MAX_OCR_CONTROLS,
                imageBase64,
            )
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
            ScreenOcr(
                "",
                emptyList(),
                "unavailable",
                "On-device text recognition was interrupted.",
                false,
                imageBase64,
            )
        } catch (error: IllegalArgumentException) {
            ScreenOcr(
                "",
                emptyList(),
                "unavailable",
                "On-device text recognition rejected the screenshot: ${error.message.orEmpty()}"
                    .take(MAX_OCR_REASON_CHARS),
                false,
                imageBase64,
            )
        } catch (error: IllegalStateException) {
            ScreenOcr(
                "",
                emptyList(),
                "unavailable",
                "On-device text recognition failed: ${error.message.orEmpty()}"
                    .take(MAX_OCR_REASON_CHARS),
                false,
                imageBase64,
            )
        } finally {
            recognizer.close()
            bitmap.recycle()
        }
    }

    private fun encodeScreenImage(bitmap: Bitmap): String? {
        var width = bitmap.width.coerceAtMost(MAX_SCREEN_IMAGE_WIDTH)
        var height = (bitmap.height.toFloat() * width / bitmap.width)
            .toInt()
            .coerceAtLeast(1)
        repeat(MAX_SCREEN_IMAGE_ATTEMPTS) { attempt ->
            val scaled = if (width == bitmap.width) {
                bitmap
            } else {
                Bitmap.createScaledBitmap(bitmap, width, height, true)
            }
            val output = ByteArrayOutputStream()
            val quality = (MAX_SCREEN_IMAGE_QUALITY - attempt * 12).coerceAtLeast(36)
            val encoded = try {
                scaled.compress(Bitmap.CompressFormat.JPEG, quality, output)
            } finally {
                if (scaled !== bitmap) scaled.recycle()
            }
            if (encoded) {
                val bytes = output.toByteArray()
                val base64 = Base64.encodeToString(bytes, Base64.NO_WRAP)
                if (base64.length <= MAX_SCREEN_IMAGE_BASE64_CHARS) return base64
            }
            width = (width * SCREEN_IMAGE_SCALE_FACTOR).toInt().coerceAtLeast(240)
            height = (height * SCREEN_IMAGE_SCALE_FACTOR).toInt().coerceAtLeast(240)
        }
        return null
    }

    private fun captureScreenshot(): Pair<Bitmap?, String> {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            return null to "Android screenshot capture requires Android 11 or later."
        }
        val completed = CountDownLatch(1)
        var bitmap: Bitmap? = null
        var failure = "Android did not return a screenshot."
        try {
            takeScreenshot(
                Display.DEFAULT_DISPLAY,
                Executor { runnable -> runnable.run() },
                object : TakeScreenshotCallback {
                    override fun onSuccess(screenshot: ScreenshotResult) {
                        val hardwareBuffer: HardwareBuffer = screenshot.hardwareBuffer
                        try {
                            val hardwareBitmap = Bitmap.wrapHardwareBuffer(
                                hardwareBuffer,
                                screenshot.colorSpace as ColorSpace?,
                            )
                            bitmap = hardwareBitmap?.copy(Bitmap.Config.ARGB_8888, false)
                            hardwareBitmap?.recycle()
                            if (bitmap == null) failure = "Android returned an unusable screenshot."
                        } catch (error: IllegalArgumentException) {
                            failure = (
                                "Android returned an unusable screenshot: ${error.message.orEmpty()}"
                            ).take(MAX_OCR_REASON_CHARS)
                        } catch (error: SecurityException) {
                            failure = (
                                "Android denied screenshot access: ${error.message.orEmpty()}"
                            ).take(MAX_OCR_REASON_CHARS)
                        } catch (error: IllegalStateException) {
                            failure = (
                                "Android screenshot capture is unavailable: ${error.message.orEmpty()}"
                            ).take(MAX_OCR_REASON_CHARS)
                        } finally {
                            hardwareBuffer.close()
                            completed.countDown()
                        }
                    }

                    override fun onFailure(errorCode: Int) {
                        failure = when (errorCode) {
                            AccessibilityService.ERROR_TAKE_SCREENSHOT_NO_ACCESSIBILITY_ACCESS ->
                                "Android denied screenshot access. Re-enable Jalora Accessibility and update the companion."
                            AccessibilityService.ERROR_TAKE_SCREENSHOT_SECURE_WINDOW ->
                                "Android blocked screenshots for this secure app screen."
                            AccessibilityService.ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT ->
                                "Android rejected the screenshot because the previous capture was too recent."
                            AccessibilityService.ERROR_TAKE_SCREENSHOT_INVALID_DISPLAY ->
                                "Android could not capture the requested display."
                            AccessibilityService.ERROR_TAKE_SCREENSHOT_INVALID_WINDOW ->
                                "Android could not capture the active window."
                            else -> "Android screenshot capture failed (code $errorCode)."
                        }
                        completed.countDown()
                    }
                },
            )
        } catch (error: SecurityException) {
            return null to (
                "Android denied screenshot access: ${error.message.orEmpty()}"
            ).take(MAX_OCR_REASON_CHARS)
        } catch (error: IllegalStateException) {
            return null to (
                "Android screenshot capture is unavailable: ${error.message.orEmpty()}"
            ).take(MAX_OCR_REASON_CHARS)
        } catch (error: IllegalArgumentException) {
            return null to (
                "Android screenshot request is invalid: ${error.message.orEmpty()}"
            ).take(MAX_OCR_REASON_CHARS)
        }
        val completedInTime = try {
            completed.await(SCREENSHOT_TIMEOUT_MS, TimeUnit.MILLISECONDS)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
            return null to "Android screenshot capture was interrupted."
        }
        if (!completedInTime) {
            return null to "Android screenshot capture timed out."
        }
        return bitmap to failure
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

    private fun longPress(root: AccessibilityNodeInfo, arguments: JSONObject): ActionResult {
        val target = uniqueNode(root, arguments)
            ?: return ActionResult.failed(
                "ANDROID_CONTROL_NOT_UNIQUE",
                "The target must match exactly one accessible control.",
            )
        if (!target.isEnabled || !target.isVisibleToUser) {
            if (target !== root) target.recycle()
            return ActionResult.failed(
                "ANDROID_CONTROL_UNAVAILABLE",
                "The selected control is disabled or hidden.",
            )
        }
        val bounds = Rect().also(target::getBoundsInScreen)
        val before = signature(root)
        val accepted = target.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK) ||
            longPressBounds(bounds)
        if (target !== root) target.recycle()
        if (!accepted) {
            return ActionResult.failed(
                "ANDROID_LONG_PRESS_FAILED",
                "Android did not accept the long-press action.",
            )
        }
        Thread.sleep(250)
        val afterRoot = rootInActiveWindow
            ?: return ActionResult.uncertain(
                "ANDROID_LONG_PRESS_UNVERIFIED",
                "Android accepted the long press, but no active screen is available.",
            )
        val after = try {
            signature(afterRoot)
        } finally {
            afterRoot.recycle()
        }
        return if (before == after) {
            ActionResult.uncertain(
                "ANDROID_LONG_PRESS_UNVERIFIED",
                "Android accepted the long press, but no accessibility state change was observed.",
            )
        } else {
            ActionResult.succeeded("long_pressed", verified = true)
        }
    }

    private fun longPressBounds(bounds: Rect): Boolean {
        if (bounds.isEmpty) return false
        val centerX = bounds.exactCenterX()
        val centerY = bounds.exactCenterY()
        val path = Path().apply {
            moveTo(centerX, centerY)
            lineTo(centerX + 1f, centerY + 1f)
        }
        return dispatchScreenGesture(path, LONG_PRESS_DURATION_MS)
    }

    private fun pressKey(
        root: AccessibilityNodeInfo,
        arguments: JSONObject,
    ): ActionResult {
        if (arguments.optString("key") != "Enter" ||
            Build.VERSION.SDK_INT < Build.VERSION_CODES.R
        ) {
            return ActionResult.failed(
                "ANDROID_KEY_UNSUPPORTED",
                "This Android version or command does not support the requested key.",
            )
        }
        val target = findFocusedNode(root)
            ?: return ActionResult.failed(
                "ANDROID_KEY_TARGET_UNAVAILABLE",
                "Enter requires a focused accessible control.",
            )
        val accepted = target.performAction(
            AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id,
        )
        if (target !== root) target.recycle()
        if (!accepted) {
            return ActionResult.failed(
                "ANDROID_KEY_FAILED",
                "Android did not accept Enter on the focused control.",
            )
        }
        Thread.sleep(250)
        val afterRoot = rootInActiveWindow
            ?: return ActionResult.uncertain(
                "ANDROID_KEY_UNVERIFIED",
                "Android accepted Enter, but the resulting screen is unavailable.",
            )
        val changed = try {
            signature(afterRoot) != signature(root)
        } finally {
            afterRoot.recycle()
        }
        return if (changed) {
            ActionResult.succeeded("enter_pressed", verified = true)
        } else {
            ActionResult.uncertain(
                "ANDROID_KEY_UNVERIFIED",
                "Android accepted Enter, but no accessibility state change was observed.",
            )
        }
    }

    private fun findFocusedNode(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var visited = 0
        while (queue.isNotEmpty() && visited < MAX_NODES) {
            val node = queue.removeFirst()
            visited += 1
            if (node.isFocused) {
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

    private fun setBrightness(
        root: AccessibilityNodeInfo,
        arguments: JSONObject,
    ): ActionResult {
        val target = uniqueNode(root, arguments)
            ?: return ActionResult.failed(
                "ANDROID_BRIGHTNESS_CONTROL_UNAVAILABLE",
                "The brightness control must match exactly one accessible slider.",
            )
        val percentage = arguments.optInt("percentage", -1)
        val range = target.rangeInfo
        if (
            !target.isEnabled ||
            !target.isVisibleToUser ||
            range == null ||
            percentage !in 0..100
        ) {
            if (target !== root) target.recycle()
            return ActionResult.failed(
                "ANDROID_BRIGHTNESS_CONTROL_UNAVAILABLE",
                "The visible brightness control does not expose an adjustable range.",
            )
        }
        val value = range.min + (range.max - range.min) * percentage / 100f
        val accepted = target.performAction(
            AccessibilityNodeInfo.AccessibilityAction.ACTION_SET_PROGRESS.id,
            Bundle().apply {
                putFloat(
                    AccessibilityNodeInfo.ACTION_ARGUMENT_PROGRESS_VALUE,
                    value,
                )
            },
        )
        if (target !== root) target.recycle()
        if (!accepted) {
            return ActionResult.failed(
                "ANDROID_BRIGHTNESS_FAILED",
                "Android did not accept the brightness adjustment.",
            )
        }
        Thread.sleep(250)
        val afterRoot = rootInActiveWindow
            ?: return ActionResult.uncertain(
                "ANDROID_BRIGHTNESS_UNVERIFIED",
                "Android accepted the brightness adjustment, but the updated control is unavailable.",
            )
        val verified = try {
            val updatedTarget = uniqueNode(afterRoot, arguments)
            if (updatedTarget == null) {
                false
            } else {
                val updatedRange = updatedTarget.rangeInfo
                val matches = updatedRange != null &&
                    kotlin.math.abs(updatedRange.current - value) <= 0.5f
                if (updatedTarget !== afterRoot) updatedTarget.recycle()
                matches
            }
        } finally {
            afterRoot.recycle()
        }
        return if (verified) {
            ActionResult.succeeded("brightness_adjusted", verified = true)
        } else {
            ActionResult.uncertain(
                "ANDROID_BRIGHTNESS_UNVERIFIED",
                "Android accepted the brightness adjustment, but the control did not confirm the value.",
            )
        }
    }

    private fun tap(root: AccessibilityNodeInfo, arguments: JSONObject): ActionResult {
        val rawX = arguments.opt("x")
        val rawY = arguments.opt("y")
        if (rawX !is Number || rawY !is Number) {
            return ActionResult.failed(
                "ANDROID_TAP_COORDINATES_INVALID",
                "Coordinate tapping requires integer screen coordinates.",
            )
        }
        val x = rawX.toInt()
        val y = rawY.toInt()
        val metrics = resources.displayMetrics
        if (
            rawX.toDouble() != x.toDouble() ||
            rawY.toDouble() != y.toDouble() ||
            x !in 0 until metrics.widthPixels ||
            y !in 0 until metrics.heightPixels
        ) {
            return ActionResult.failed(
                "ANDROID_TAP_COORDINATES_INVALID",
                "The requested tap point is outside the current screen.",
            )
        }
        val before = signature(root)
        if (!tapBounds(Rect(x, y, x + 1, y + 1))) {
            return ActionResult.failed(
                "ANDROID_TAP_FAILED",
                "Android did not accept the coordinate tap.",
            )
        }
        Thread.sleep(250)
        val afterRoot = rootInActiveWindow
            ?: return ActionResult.uncertain(
                "ANDROID_TAP_UNVERIFIED",
                "Android accepted the tap, but the resulting screen is unavailable.",
            )
        val after = try {
            if (afterRoot.packageName != root.packageName) {
                return ActionResult.uncertain(
                    "ANDROID_FOREGROUND_APP_CHANGED",
                    "The tap changed the foreground app; the result is uncertain.",
                )
            }
            signature(afterRoot)
        } finally {
            afterRoot.recycle()
        }
        return if (before == after) {
            ActionResult.uncertain(
                "ANDROID_TAP_UNVERIFIED",
                "Android accepted the tap, but no accessibility state change was observed.",
            )
        } else {
            ActionResult.succeeded("tapped", verified = true)
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
        val name = arguments.optString("name")
        val automationId = arguments.optString("automation_id")
        if (name.isBlank() && automationId.isBlank()) {
            return swipeScreen(root, arguments.optString("direction"))
        }
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

    private fun swipeScreen(root: AccessibilityNodeInfo, direction: String): ActionResult {
        val metrics: DisplayMetrics = resources.displayMetrics
        val startX: Float
        val startY: Float
        val endX: Float
        val endY: Float
        when (direction) {
            "down" -> {
                startX = metrics.widthPixels * 0.5f
                startY = metrics.heightPixels * 0.30f
                endX = startX
                endY = metrics.heightPixels * 0.75f
            }
            "up" -> {
                startX = metrics.widthPixels * 0.5f
                startY = metrics.heightPixels * 0.75f
                endX = startX
                endY = metrics.heightPixels * 0.30f
            }
            "left" -> {
                startX = metrics.widthPixels * 0.80f
                startY = metrics.heightPixels * 0.5f
                endX = metrics.widthPixels * 0.20f
                endY = startY
            }
            "right" -> {
                startX = metrics.widthPixels * 0.20f
                startY = metrics.heightPixels * 0.5f
                endX = metrics.widthPixels * 0.80f
                endY = startY
            }
            else -> return ActionResult.failed(
                "ANDROID_SWIPE_DIRECTION_INVALID",
                "The swipe direction must be up, down, left, or right.",
            )
        }
        val path = Path().apply {
            moveTo(startX, startY)
            lineTo(endX, endY)
        }
        val before = signature(root)
        if (!dispatchScreenGesture(path, SWIPE_DURATION_MS)) {
            return ActionResult.failed(
                "ANDROID_SWIPE_FAILED",
                "Android did not accept the screen swipe.",
            )
        }
        Thread.sleep(250)
        val afterRoot = rootInActiveWindow
            ?: return ActionResult.uncertain(
                "ANDROID_SWIPE_UNVERIFIED",
                "Android accepted the swipe, but the resulting screen is unavailable.",
            )
        val after = try {
            if (afterRoot.packageName != root.packageName) {
                return ActionResult.uncertain(
                    "ANDROID_FOREGROUND_APP_CHANGED",
                    "The swipe changed the foreground app; the result is uncertain.",
                )
            }
            signature(afterRoot)
        } finally {
            afterRoot.recycle()
        }
        return if (before == after) {
            ActionResult.uncertain(
                "ANDROID_SWIPE_UNVERIFIED",
                "Android accepted the swipe, but no accessibility state change was observed.",
            )
        } else {
            ActionResult.succeeded("swiped", verified = true)
        }
    }

    private fun dispatchScreenGesture(path: Path, durationMs: Long): Boolean {
        val completed = CountDownLatch(1)
        var succeeded = false
        val dispatched = dispatchGesture(
            GestureDescription.Builder()
                .addStroke(
                    GestureDescription.StrokeDescription(path, 0, durationMs.coerceIn(1, 5_000)),
                )
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
        return dispatched &&
            completed.await(GESTURE_TIMEOUT_MS, TimeUnit.MILLISECONDS) &&
            succeeded
    }

    private fun openSystemPanel(
        root: AccessibilityNodeInfo,
        globalAction: Int,
        resultName: String,
    ): ActionResult {
        val before = signature(root)
        if (!performGlobalAction(globalAction)) {
            return ActionResult.failed(
                "ANDROID_SYSTEM_PANEL_UNAVAILABLE",
                "Android did not accept the request to open $resultName.",
            )
        }
        Thread.sleep(300)
        val afterRoot = rootInActiveWindow
            ?: return ActionResult.uncertain(
                "ANDROID_SYSTEM_PANEL_UNVERIFIED",
                "Android accepted the request, but the resulting system panel is unavailable.",
            )
        val after = try {
            signature(afterRoot)
        } finally {
            afterRoot.recycle()
        }
        return if (before == after) {
            ActionResult.uncertain(
                "ANDROID_SYSTEM_PANEL_UNVERIFIED",
                "Android accepted the request, but the system panel change was not observed.",
            )
        } else {
            ActionResult.succeeded("${resultName}_opened", verified = true)
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
        private const val ACCESSIBILITY_CONTROL_LIMIT = 24
        private const val MAX_OCR_CONTROLS = 16
        private const val MAX_OCR_CHARS = 5_000
        private const val MAX_OCR_REASON_CHARS = 400
        private const val MAX_SCREEN_IMAGE_BASE64_CHARS = 48_000
        private const val MAX_SCREEN_IMAGE_WIDTH = 1_280
        private const val MAX_SCREEN_IMAGE_ATTEMPTS = 6
        private const val MAX_SCREEN_IMAGE_QUALITY = 76
        private const val SCREEN_IMAGE_SCALE_FACTOR = 0.75f
        private const val MAX_SCREEN_DIMENSION = 10_000
        private const val SCREENSHOT_TIMEOUT_MS = 5_000L
        private const val OCR_TIMEOUT_MS = 8_000L
        private const val MAX_LAUNCHER_SWIPES = 8
        private const val LAUNCHER_SETTLE_MS = 500L
        private const val FOREGROUND_CHANGE_ATTEMPTS = 20
        private const val FOREGROUND_CHANGE_INTERVAL_MS = 250L
        private const val SEARCH_SETTLE_MS = 300L
        private const val SWIPE_DURATION_MS = 400L
        private const val TAP_DURATION_MS = 80L
        private const val LONG_PRESS_DURATION_MS = 800L
        private const val GESTURE_TIMEOUT_MS = 2_000L
        private val AUTHENTICATION_GUARDED_OPERATIONS = setOf(
            "android.screen.click",
            "android.screen.long_press",
            "android.screen.tap",
            "android.screen.scroll",
            "android.screen.swipe",
            "android.screen.type",
            "android.screen.key",
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

private data class ScreenOcr(
    val text: String,
    val controls: List<JSONObject>,
    val status: String,
    val reason: String,
    val truncated: Boolean,
    val imageBase64: String? = null,
)

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
