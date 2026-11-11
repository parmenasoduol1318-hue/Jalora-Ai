"""Global rate-limit middleware backend (Phase L, sec 74).

Supports Redis (multi-worker safe) and in-memory fallback (single worker).
The middleware itself is wired up in ``app/main.py``.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

redis_lib = None  # lazily imported


def _maybe_redis():
    global redis_lib
    if redis_lib is None:
        try:
            import redis as _r

            redis_lib = _r
        except ImportError:
            return None
    return redis_lib


class _InMemoryRateLimiter:
    """Original sliding-window in-memory limiter."""

    def __init__(self, max_keys: int = 10_000) -> None:
        if max_keys < 1:
            raise ValueError("max_keys must be positive")
        self._lock = threading.Lock()
        self._windows: dict[str, deque[float]] = {}
        self._window_seconds: dict[str, float] = {}
        self._max_keys = max_keys

    def _remove_expired(self, now: float) -> None:
        for key in list(self._windows):
            queue = self._windows[key]
            duration = self._window_seconds[key]
            while queue and now - queue[0] >= duration:
                queue.popleft()
            if not queue:
                del self._windows[key]
                del self._window_seconds[key]

    def is_allowed(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        if limit <= 0:
            return True
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        now = time.monotonic()
        with self._lock:
            q = self._windows.get(key)
            if q is None:
                if len(self._windows) >= self._max_keys:
                    self._remove_expired(now)
                if len(self._windows) >= self._max_keys:
                    return False
                q = deque()
                self._windows[key] = q
            self._window_seconds[key] = window_seconds
            while q and now - q[0] >= window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


class _RedisRateLimiter:
    """Redis sliding-window rate limiter using INCR + EXPIRE."""

    def __init__(self, redis_url: str) -> None:
        r = _maybe_redis()
        if r is None:
            raise RuntimeError("redis package not installed")
        self._r = r.from_url(redis_url, decode_responses=True)
        self._fallback = _InMemoryRateLimiter()

    def is_allowed(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        if limit <= 0:
            return True
        pipe = self._r.pipeline()
        now = time.time()
        # Use a fixed window: truncate to the current window boundary.
        window_key = f"{key}:{int(now / window_seconds)}"
        pipe.incr(window_key)
        pipe.expire(window_key, int(window_seconds))
        results = pipe.execute()
        count = results[0]  # INCR return value
        return count <= limit


def get_rate_limiter(redis_url: str | None = None) -> "_InMemoryRateLimiter | _RedisRateLimiter":
    """Return a rate limiter instance, preferring Redis when available.

    Redis outages are handled explicitly so the app does not keep silently
    creating a "configured but unusable" limiter instance.
    """
    if redis_url:
        try:
            limiter = _RedisRateLimiter(redis_url)
            limiter._r.ping()
            return limiter
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Redis rate limiter unavailable; falling back to in-memory.",
                exc_info=True,
            )
    return _InMemoryRateLimiter()


def check_rate_limit(
    limiter: "_InMemoryRateLimiter | _RedisRateLimiter",
    key: str,
    limit_per_min: int,
) -> bool:
    """Returns True if the request is allowed, False if rate-limited."""
    if limit_per_min <= 0:
        return True
    try:
        return limiter.is_allowed(key, limit_per_min, window_seconds=60.0)
    except Exception:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning("Rate limiter backend failed; retrying with in-memory fallback.", exc_info=True)
        if hasattr(limiter, "_r"):
            fallback = getattr(limiter, "_fallback", None)
            if fallback is None:
                fallback = _InMemoryRateLimiter()
                limiter._fallback = fallback
            return fallback.is_allowed(key, limit_per_min, window_seconds=60.0)
        raise
