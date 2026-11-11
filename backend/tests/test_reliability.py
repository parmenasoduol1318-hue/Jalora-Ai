"""Tests for the reliability module — timeout, retry, circuit breaker."""
import asyncio
import time

import pytest

from app.services.reliability import (
    async_timeout,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    RetryConfig,
    TimeoutError,
    call_with_circuit_breaker,
    get_circuit_breaker,
    reset_circuit_breaker,
    retry,
    with_retry,
)


def test_retry_config_rejects_invalid_bounds():
    with pytest.raises(ValueError, match="max_attempts"):
        RetryConfig(max_attempts=0)
    with pytest.raises(ValueError, match="delays"):
        RetryConfig(base_delay=-1)
    with pytest.raises(ValueError, match="exponential_base"):
        RetryConfig(exponential_base=0.5)


def test_circuit_breaker_config_rejects_invalid_bounds():
    with pytest.raises(ValueError, match="failure_threshold"):
        CircuitBreakerConfig(failure_threshold=0)
    with pytest.raises(ValueError, match="recovery_timeout"):
        CircuitBreakerConfig(recovery_timeout=-1)


class TestTimeout:
    """Tests for timeout functionality."""

    @pytest.mark.asyncio
    async def test_with_timeout_success(self):
        async def fast_func():
            await asyncio.sleep(0.1)
            return "done"

        result = await asyncio.wait_for(fast_func(), timeout=5.0)
        assert result == "done"

    @pytest.mark.asyncio
    async def test_with_timeout_raises_timeout(self):
        async def slow_func():
            await asyncio.sleep(10.0)
            return "done"

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slow_func(), timeout=0.1)

    @pytest.mark.asyncio
    async def test_async_timeout_context_bounds_operation(self):
        with pytest.raises(TimeoutError, match="bounded operation timed out"):
            async with async_timeout(0.01, "bounded operation"):
                await asyncio.sleep(0.05)


class TestRetry:
    """Tests for retry with exponential backoff."""

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self):
        call_count = 0

        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise TimeoutError("simulated failure")
            return "success"

        config = RetryConfig(max_attempts=5, base_delay=0.01, jitter=False)
        result = await retry(flaky_func, config, "test")
        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_raises_after_max_attempts(self):
        call_count = 0

        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise TimeoutError("always fails")

        config = RetryConfig(max_attempts=3, base_delay=0.01, jitter=False)
        with pytest.raises(TimeoutError):
            await retry(always_fail, config, "test")
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_with_jitter(self):
        """Test that jitter adds variability to delays."""
        delays = []

        async def delayed_retry():
            delays.append(time.monotonic())
            if len(delays) < 2:
                raise TimeoutError("fail")
            return "ok"

        config = RetryConfig(max_attempts=3, base_delay=0.5, jitter=True)
        # First call
        start = time.monotonic()
        try:
            await retry(delayed_retry, config, "test")
        except:
            pass
        elapsed = time.monotonic() - start
        # With jitter, delay should be between 0.25s and 0.75s (0.5 * 0.5 to 0.5 * 1.0)
        assert 0.2 <= elapsed <= 1.0

    @pytest.mark.asyncio
    async def test_retry_decorator(self):
        @with_retry(RetryConfig(max_attempts=3, base_delay=0.01, jitter=False))
        async def decorated_func():
            return "decorated"

        result = await decorated_func()
        assert result == "decorated"


class TestCircuitBreaker:
    """Tests for circuit breaker functionality."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_closed_initially(self):
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2))
        assert breaker.state == CircuitState.CLOSED
        assert breaker.is_tripped() is False

    @pytest.mark.asyncio
    async def test_circuit_breaker_trips_after_failures(self):
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2))

        # First failure
        result = breaker.on_failure(TimeoutError("test"))
        assert breaker.failure_count == 1
        assert breaker.state == CircuitState.CLOSED
        assert result is False  # Not HALF_OPEN

        # Second failure - should trip
        result = breaker.on_failure(TimeoutError("test"))
        assert breaker.failure_count == 2
        assert breaker.state == CircuitState.OPEN
        assert result is False  # Still not HALF_OPEN
        assert breaker.is_tripped() is True

    @pytest.mark.asyncio
    async def test_circuit_breaker_reset_on_success(self):
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2))

        # Trip the circuit
        breaker.on_failure(TimeoutError("test"))
        breaker.on_failure(TimeoutError("test"))
        assert breaker.is_tripped() is True

        # Reset on success
        breaker.on_success()
        assert breaker.failure_count == 0
        assert breaker.state == CircuitState.CLOSED
        assert breaker.is_tripped() is False

    @pytest.mark.asyncio
    async def test_circuit_breaker_getter(self):
        name = "test_circuit"
        breaker = get_circuit_breaker(name)
        assert breaker.config.failure_threshold == 5  # default

        # Get same breaker again
        breaker2 = get_circuit_breaker(name)
        assert breaker is breaker2

    @pytest.mark.asyncio
    async def test_reset_circuit_breaker(self):
        name = "test_reset"
        breaker = get_circuit_breaker(name, CircuitBreakerConfig(failure_threshold=1))

        # Trip it
        breaker.on_failure(TimeoutError("test"))
        assert breaker.is_tripped() is True

        # Reset
        reset_circuit_breaker(name)
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0

    @pytest.mark.asyncio
    async def test_call_with_circuit_breaker_tripped(self):
        breaker = get_circuit_breaker("tripped_test")
        breaker.state = CircuitState.OPEN
        breaker.last_failure_time = time.monotonic()

        with pytest.raises(TimeoutError, match="OPEN"):
            await call_with_circuit_breaker("tripped_test", lambda: None)

    @pytest.mark.asyncio
    async def test_call_with_circuit_breaker_success(self):
        async def success_func():
            return "success"

        result = await call_with_circuit_breaker("success_test", success_func)
        assert result == "success"


class TestCircuitBreakerHalfOpen:
    """Tests for HALF_OPEN state behavior."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_half_open_recovery(self):
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0.1))

        # Trip the circuit
        breaker.on_failure(TimeoutError("test"))
        assert breaker.is_tripped() is True

        # Wait for recovery timeout
        await asyncio.sleep(0.15)

        # Check if we can execute (should be True in HALF_OPEN state)
        can_execute = breaker._can_execute()
        assert can_execute is True
        assert breaker.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_circuit_breaker_half_open_success(self):
        config = CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0.1)
        breaker = CircuitBreaker(config)

        # Trip the circuit
        breaker.on_failure(TimeoutError("test"))

        # Wait for recovery
        await asyncio.sleep(0.15)

        # Success should close it
        breaker.on_success()
        assert breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_only_one_half_open_probe_runs_at_a_time(self):
        name = f"half_open_probe_{id(self)}"
        config = CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0)
        reset_circuit_breaker(name)
        breaker = get_circuit_breaker(name, config)
        breaker.on_failure(TimeoutError("initial failure"))
        started = asyncio.Event()
        release = asyncio.Event()
        probe_calls = 0

        async def probe():
            nonlocal probe_calls
            probe_calls += 1
            started.set()
            await release.wait()
            return "recovered"

        async def duplicate_probe():
            nonlocal probe_calls
            probe_calls += 1
            return "duplicate"

        first = asyncio.create_task(call_with_circuit_breaker(name, probe, breaker.config))
        await started.wait()
        with pytest.raises(TimeoutError, match="OPEN or HALF_OPEN"):
            await call_with_circuit_breaker(name, duplicate_probe, breaker.config)
        assert probe_calls == 1
        release.set()

        assert await first == "recovered"
        assert breaker.state == CircuitState.CLOSED
        reset_circuit_breaker(name)


class TestIntegration:
    """Integration tests combining timeout and retry."""

    @pytest.mark.asyncio
    async def test_timeout_with_retry(self):
        """Test that timeout errors trigger retry."""
        attempt_count = 0

        async def eventually_success():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise TimeoutError("timeout")
            return "success"

        result = await retry(
            eventually_success,
            RetryConfig(max_attempts=5, base_delay=0.01, jitter=False),
            "test"
        )
        assert result == "success"
        assert attempt_count == 3