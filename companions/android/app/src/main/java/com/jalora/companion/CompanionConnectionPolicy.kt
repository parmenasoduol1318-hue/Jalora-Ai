package com.jalora.companion

internal enum class CompanionStartReason {
    USER_CONNECT,
    USER_RESUME,
    SYSTEM_RESTART,
}

internal object CompanionConnectionPolicy {
    fun shouldRestoreConnection(
        paired: Boolean,
        connectionEnabled: Boolean,
        pollingPaused: Boolean,
    ): Boolean = paired && connectionEnabled && !pollingPaused

    fun shouldStartWorkers(
        reason: CompanionStartReason,
        connectionEnabled: Boolean,
        running: Boolean,
        pollWorkerAlive: Boolean,
    ): Boolean {
        if (running && pollWorkerAlive) return false
        return reason != CompanionStartReason.SYSTEM_RESTART || connectionEnabled
    }

    fun retryDelay(failures: Int): Long {
        if (failures <= 0) return 0L
        val exponent = (failures - 1).coerceAtMost(4)
        return (2_500L * (1L shl exponent)).coerceAtMost(30_000L)
    }

    fun isPollRecent(lastPollAtMillis: Long, nowMillis: Long): Boolean =
        lastPollAtMillis > 0L &&
            nowMillis >= lastPollAtMillis &&
            nowMillis - lastPollAtMillis <= 90_000L
}
