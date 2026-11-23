package com.jalora.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CompanionConnectionPolicyTest {
    @Test
    fun connectAndResumeStartWorkersEvenWhenServiceWasStopped() {
        for (reason in listOf(
            CompanionStartReason.USER_CONNECT,
            CompanionStartReason.USER_RESUME,
        )) {
            assertTrue(
                CompanionConnectionPolicy.shouldStartWorkers(
                    reason = reason,
                    connectionEnabled = true,
                    running = false,
                    pollWorkerAlive = false,
                ),
            )
        }
    }

    @Test
    fun activityRestoresOnlyAnAuthorizedUnpausedConnection() {
        assertTrue(
            CompanionConnectionPolicy.shouldRestoreConnection(
                paired = true,
                connectionEnabled = true,
                pollingPaused = false,
            ),
        )
        assertFalse(
            CompanionConnectionPolicy.shouldRestoreConnection(
                paired = true,
                connectionEnabled = false,
                pollingPaused = false,
            ),
        )
        assertFalse(
            CompanionConnectionPolicy.shouldRestoreConnection(
                paired = true,
                connectionEnabled = true,
                pollingPaused = true,
            ),
        )
        assertFalse(
            CompanionConnectionPolicy.shouldRestoreConnection(
                paired = false,
                connectionEnabled = true,
                pollingPaused = false,
            ),
        )
    }

    @Test
    fun stickyRestartStartsOneWorkerOnlyWhenConnectionWasAuthorized() {
        assertFalse(
            CompanionConnectionPolicy.shouldStartWorkers(
                reason = CompanionStartReason.USER_RESUME,
                connectionEnabled = true,
                running = true,
                pollWorkerAlive = true,
            ),
        )
        assertTrue(
            CompanionConnectionPolicy.shouldStartWorkers(
                reason = CompanionStartReason.SYSTEM_RESTART,
                connectionEnabled = true,
                running = true,
                pollWorkerAlive = false,
            ),
        )
        assertFalse(
            CompanionConnectionPolicy.shouldStartWorkers(
                reason = CompanionStartReason.SYSTEM_RESTART,
                connectionEnabled = true,
                running = true,
                pollWorkerAlive = true,
            ),
        )
        assertFalse(
            CompanionConnectionPolicy.shouldStartWorkers(
                reason = CompanionStartReason.SYSTEM_RESTART,
                connectionEnabled = false,
                running = false,
                pollWorkerAlive = false,
            ),
        )
    }

    @Test
    fun networkRetryBackoffIsBoundedAndResetsAfterSuccess() {
        assertEquals(0L, CompanionConnectionPolicy.retryDelay(0))
        assertEquals(2_500L, CompanionConnectionPolicy.retryDelay(1))
        assertEquals(5_000L, CompanionConnectionPolicy.retryDelay(2))
        assertEquals(30_000L, CompanionConnectionPolicy.retryDelay(6))
        assertEquals(30_000L, CompanionConnectionPolicy.retryDelay(20))
    }

    @Test
    fun pollingIsUnavailableWhenItsLastSuccessfulRequestGrowsStale() {
        val now = 1_000_000L
        assertTrue(CompanionConnectionPolicy.isPollRecent(now - 89_000L, now))
        assertFalse(CompanionConnectionPolicy.isPollRecent(now - 91_000L, now))
        assertFalse(CompanionConnectionPolicy.isPollRecent(0L, now))
    }
}
