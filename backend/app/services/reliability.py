"""Reliability primitives for Jalora — timeout wrappers, retries, circuit breakers.

Phase 4: Hardening - provides resilience patterns for external service calls
and long-running operations.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum, auto
from functools import wraps
from typing import Any, Callable, Coroutine, TypeVar

logger = logging.getLogger("jalora.reliability")

F = TypeVar("F", bound=Callable[..., Any])
AF = TypeVar("AF", bound=Callable[..., Coroutine[Any, Any, Any]])


# ---------------------------------------------------------------------------
# Timeout utilities
# ---------------------------------------------------------------------------

class TimeoutError(Exception):
    """Raised when an operation exceeds its time budget."""
    pass


@asynccontextmanager
async def async_timeout(seconds: float, operation_name: str = "operation"):
    """Async context manager for timeout-bounded operations.
    
    Usage:
        async with async_timeout(30.0, "ollama_call"):
            result = await ollama.generate(...)
    """
    try:
        async with asyncio.timeout(seconds):
            yield
    except asyncio.TimeoutError as exc:
        raise TimeoutError(f"{operation_name} timed out after {seconds}s") from exc


def with_timeout(coro_func: AF, timeout_seconds: float, operation_name: str = "operation") -> AF:
    """Decorator that wraps a coroutine function with a timeout."""
    @wraps(coro_func)
    async def wrapper(*args, **kwargs):
        try:
            return await asyncio.wait_for(
                coro_func(*args, **kwargs),
                timeout=timeout_seconds
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"{operation_name} timed out after {timeout_seconds}s")
    return wrapper  # type: ignore


# ---------------------------------------------------------------------------
# Retry utilities with exponential backoff
# ---------------------------------------------------------------------------

@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    max_attempts: int = 3
    base_delay: float = 0.5  # seconds
    max_delay: float = 30.0  # seconds
    exponential_base: float = 2.0
    jitter: bool = True
    retry_on: tuple[type[Exception], ...] = (TimeoutError, ConnectionError, OSError)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("retry delays must not be negative")
        if self.exponential_base < 1:
            raise ValueError("exponential_base must be at least one")


def _calculate_delay(attempt: int, config: RetryConfig) -> float:
    """Calculate delay with exponential backoff and optional jitter."""
    delay = min(config.base_delay * (config.exponential_base ** (attempt - 1)), config.max_delay)
    if config.jitter:
        # Add up to 50% jitter
        import random
        delay *= (0.5 + random.random() * 0.5)
    return delay


async def retry(
    func: AF,
    config: RetryConfig | None = None,
    operation_name: str = "operation",
) -> Any:
    """Execute a coroutine with retry logic and exponential backoff.
    
    Args:
        func: The coroutine function to execute
        config: Retry configuration
        operation_name: Name for logging purposes
        
    Returns:
        The result of the function
        
    Raises:
        The last exception if all retries are exhausted
    """
    if config is None:
        config = RetryConfig()
    
    last_exception: Exception | None = None
    
    for attempt in range(1, config.max_attempts + 1):
        try:
            return await func()
        except config.retry_on as e:
            last_exception = e
            if attempt < config.max_attempts:
                delay = _calculate_delay(attempt, config)
                logger.warning(
                    "%s attempt %d/%d failed: %s. Retrying in %.2fs",
                    operation_name, attempt, config.max_attempts, e, delay
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "%s failed after %d attempts: %s",
                    operation_name, config.max_attempts, e
                )
    
    raise last_exception


def with_retry(config: RetryConfig | None = None, operation_name: str = "operation") -> Callable[[AF], AF]:
    """Decorator that adds retry logic to an async function."""
    def decorator(func: AF) -> AF:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            async def _call():
                return await func(*args, **kwargs)
            return await retry(_call, config, operation_name or func.__name__)
        return wrapper  # type: ignore
    return decorator


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class CircuitState(Enum):
    """State of a circuit breaker."""
    CLOSED = auto()    # Normal operation
    OPEN = auto()      # Tripped, rejecting calls
    HALF_OPEN = auto() # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior."""
    failure_threshold: int = 5
    recovery_timeout: float = 30.0  # seconds
    expected_exceptions: tuple[type[Exception], ...] = (TimeoutError, ConnectionError, OSError)

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be at least one")
        if self.recovery_timeout < 0:
            raise ValueError("recovery_timeout must not be negative")


@dataclass
class CircuitBreaker:
    """Circuit breaker for fallible operations.
    
    Prevents cascading failures by temporarily blocking calls to a failing service.
    """
    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    failure_count: int = field(default=0, init=False)
    last_failure_time: float | None = field(default=None, init=False)
    state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _probe_in_flight: bool = field(default=False, init=False, repr=False)
    _lock: threading.RLock = field(
        default_factory=threading.RLock, init=False, repr=False
    )
    
    def _can_execute(self) -> bool:
        """Check if a call is allowed."""
        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            if self.state == CircuitState.OPEN:
                if (
                    self.last_failure_time is None
                    or time.monotonic() - self.last_failure_time
                    < self.config.recovery_timeout
                ):
                    return False
                self.state = CircuitState.HALF_OPEN
            if self.state == CircuitState.HALF_OPEN and not self._probe_in_flight:
                self._probe_in_flight = True
                return True
            return False
    
    def on_success(self) -> None:
        """Record a successful call."""
        with self._lock:
            self.failure_count = 0
            self.state = CircuitState.CLOSED
            self.last_failure_time = None
            self._probe_in_flight = False
    
    def on_failure(self, exc: Exception) -> bool:
        """Record failure; a failed probe reopens the circuit and is never retried."""
        with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self._probe_in_flight = False
                self.state = CircuitState.OPEN
                self.last_failure_time = time.monotonic()
                if isinstance(exc, self.config.expected_exceptions):
                    self.failure_count += 1
                return False
            if not isinstance(exc, self.config.expected_exceptions):
                return False

            self.failure_count += 1
            self.last_failure_time = time.monotonic()

            if self.failure_count >= self.config.failure_threshold:
                self.state = CircuitState.OPEN
                logger.warning(
                    "Circuit breaker OPEN after %d failures",
                    self.failure_count,
                )
            return False

    def on_cancelled(self) -> None:
        """Release a cancelled recovery probe without allowing concurrent probes."""
        with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self._probe_in_flight = False
                self.state = CircuitState.OPEN
                self.last_failure_time = time.monotonic()
    
    def is_tripped(self) -> bool:
        """Check if the circuit breaker is currently tripped (OPEN)."""
        with self._lock:
            return self.state == CircuitState.OPEN


# Global circuit breaker registry
_circuit_breakers: dict[str, CircuitBreaker] = {}
_circuit_breakers_lock = threading.Lock()


def get_circuit_breaker(name: str, config: CircuitBreakerConfig | None = None) -> CircuitBreaker:
    """Get or create a named circuit breaker."""
    with _circuit_breakers_lock:
        if name not in _circuit_breakers:
            _circuit_breakers[name] = CircuitBreaker(config=config or CircuitBreakerConfig())
        return _circuit_breakers[name]


async def call_with_circuit_breaker(
    name: str,
    func: AF,
    config: CircuitBreakerConfig | None = None,
) -> Any:
    """Call a function with circuit breaker protection.
    
    Args:
        name: Circuit breaker name
        func: Function to call
        config: Optional config (creates new breaker if none exists)
        
    Returns:
        Function result
        
    Raises:
        TimeoutError: If the circuit breaker is OPEN or already has a recovery probe
        Exception: The original exception if the call fails
    """
    breaker = get_circuit_breaker(name, config)
    
    if not breaker._can_execute():
        raise TimeoutError(f"Circuit breaker for '{name}' is OPEN or HALF_OPEN")
    
    try:
        result = await func()
        breaker.on_success()
        return result
    except asyncio.CancelledError:
        breaker.on_cancelled()
        raise
    except Exception as e:
        breaker.on_failure(e)
        raise


def reset_circuit_breaker(name: str) -> None:
    """Reset a circuit breaker to CLOSED state."""
    with _circuit_breakers_lock:
        breaker = _circuit_breakers.get(name)
    if breaker is not None:
        breaker.on_success()


# ---------------------------------------------------------------------------
# Combined decorator for production use
# ---------------------------------------------------------------------------

def resilient(
    timeout: float = 60.0,
    retry_attempts: int = 3,
    retry_base_delay: float = 0.5,
    circuit_breaker_name: str | None = None,
) -> Callable[[AF], AF]:
    """Combined decorator for production resilience.
    
    Applies timeout, retry, and optional circuit breaker to async functions.
    
    Usage:
        @resilient(timeout=30, retry_attempts=3, circuit_breaker_name="ollama")
        async def generate_text(prompt: str):
            ...
    """
    config = RetryConfig(
        max_attempts=retry_attempts,
        base_delay=retry_base_delay,
    )
    
    def decorator(func: AF) -> AF:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            async def _operation():
                try:
                    return await asyncio.wait_for(
                        func(*args, **kwargs),
                        timeout=timeout
                    )
                except asyncio.TimeoutError as e:
                    raise TimeoutError(f"{func.__name__} timed out after {timeout}s") from e
            
            if circuit_breaker_name:
                breaker_config = CircuitBreakerConfig()
                return await call_with_circuit_breaker(
                    circuit_breaker_name,
                    _operation,
                    breaker_config
                )
            else:
                return await retry(_operation, config, func.__name__)
        
        return wrapper  # type: ignore
    return decorator


__all__ = [
    "TimeoutError",
    "async_timeout",
    "with_timeout",
    "retry",
    "with_retry",
    "RetryConfig",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitState",
    "get_circuit_breaker",
    "call_with_circuit_breaker",
    "reset_circuit_breaker",
    "resilient",
]