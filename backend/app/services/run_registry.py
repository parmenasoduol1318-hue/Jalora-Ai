"""Registry of active, paused and stopped agent runs (spec §24 pause/resume, §25 stop).

Phase L (§58): when ``JALORA_REDIS_URL`` is set the registry is backed by Redis
so that emergency-stop / pause works across multiple workers.  When Redis is
not configured it falls back to the original in-process dict + lock, which is
sufficient for single-worker dev / test environments.

The public function-based API (``register_active``, ``is_stopped`` …) is
unchanged so that existing callers in ``graph.py``, ``chat_service.py`` and
``runs.py`` need no changes.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings

import redis as redis_lib  # noqa: E402

from app.config import get_settings  # noqa: E402

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# Test hook: when True, _get_backend() always returns the in-process
# _InMemoryBackend, bypassing Redis even when JALORA_REDIS_URL is set. This
# lets the unit tests exercise the in-memory code path deterministically
# without depending on a running Redis server. Only ever flipped by tests
# (via monkeypatch.setattr on this module attribute).
_force_in_memory = False


class _InMemoryBackend:
    """Original in-process backend — dicts guarded by a lock."""

    def __init__(self) -> None:
        self._active: dict[str, set[str]] = defaultdict(set)
        self._stopped: dict[str, set[str]] = defaultdict(set)
        self._paused: dict[str, set[str]] = defaultdict(set)

    def register_active(self, user_id: str, run_id: str) -> None:
        self._active[user_id].add(run_id)

    def unregister_active(self, user_id: str, run_id: str) -> None:
        self._active[user_id].discard(run_id)

    def stop_all(self, user_id: str) -> int:
        run_ids = set(self._active.get(user_id, set()))
        self._stopped[user_id].update(run_ids)
        return len(run_ids)

    def stop_run(self, user_id: str, run_id: str) -> bool:
        self._stopped[user_id].add(run_id)
        return True

    def is_stopped(self, user_id: str, run_id: str | None) -> bool:
        return bool(run_id) and run_id in self._stopped.get(user_id, set())

    def request_pause(self, user_id: str, run_id: str) -> None:
        self._paused[user_id].add(run_id)

    def clear_pause(self, user_id: str, run_id: str) -> None:
        self._paused[user_id].discard(run_id)

    def is_paused(self, user_id: str, run_id: str | None) -> bool:
        return bool(run_id) and run_id in self._paused.get(user_id, set())

    def active_count(self, user_id: str) -> int:
        return len(self._active.get(user_id, set()))


class _RedisBackend:
    """Redis-backed backend — safe for multi-worker deployments."""

    def __init__(self, redis_url: str) -> None:
        self._r = redis_lib.from_url(redis_url, decode_responses=True)

    @staticmethod
    def _key(user_id: str, suffix: str) -> str:
        return f"jalora:runs:{user_id}:{suffix}"

    def register_active(self, user_id: str, run_id: str) -> None:
        pipe = self._r.pipeline()
        pipe.sadd(self._key(user_id, "active"), run_id)
        # Ensure the other sets exist so is_stopped/is_paused return False
        pipe.sadd(self._key(user_id, "stopped"), "")  # noop if already empty
        pipe.sadd(self._key(user_id, "paused"), "")
        pipe.execute()

    def unregister_active(self, user_id: str, run_id: str) -> None:
        for suffix in ("active", "stopped", "paused"):
            self._r.srem(self._key(user_id, suffix), run_id)

    def stop_all(self, user_id: str) -> int:
        active_key = self._key(user_id, "active")
        stopped_key = self._key(user_id, "stopped")
        run_ids = self._r.smembers(active_key)
        if run_ids:
            pipe = self._r.pipeline()
            pipe.sadd(stopped_key, *run_ids)
            pipe.execute()
        return len(run_ids)

    def stop_run(self, user_id: str, run_id: str) -> bool:
        self._r.sadd(self._key(user_id, "stopped"), run_id)
        return True

    def is_stopped(self, user_id: str, run_id: str | None) -> bool:
        if not run_id:
            return False
        return bool(self._r.sismember(self._key(user_id, "stopped"), run_id))

    def request_pause(self, user_id: str, run_id: str) -> None:
        self._r.sadd(self._key(user_id, "paused"), run_id)

    def clear_pause(self, user_id: str, run_id: str) -> None:
        self._r.srem(self._key(user_id, "paused"), run_id)

    def is_paused(self, user_id: str, run_id: str | None) -> bool:
        if not run_id:
            return False
        return bool(self._r.sismember(self._key(user_id, "paused"), run_id))

    def active_count(self, user_id: str) -> int:
        return self._r.scard(self._key(user_id, "active"))


def _get_backend() -> "_InMemoryBackend | _RedisBackend":
    """Lazy-initialise the backend based on settings (cached singleton).

    Redis failures are treated as operationally significant: a bad or unreachable
    Redis instance should not silently masquerade as a healthy backend when the
    app is supposed to use the Redis-backed registry.
    """
    with _lock:
        cached = getattr(_get_backend, "_cache", None)
        if cached is not None:
            return cached
        settings = get_settings()
        if settings.redis_url and not _force_in_memory:
            try:
                backend = _RedisBackend(settings.redis_url)
                # Validate the connection immediately so we do not cache a backend
                # that will fail on the first real operation.
                backend._r.ping()
                cached = backend
            except Exception:
                logger.warning(
                    "Redis run registry unavailable; falling back to in-memory.",
                    exc_info=True,
                )
                cached = _InMemoryBackend()
        else:
            cached = _InMemoryBackend()
        _get_backend._cache = cached  # type: ignore[attr-defined]
        return cached


def _reset_backend() -> None:
    """Discard the cached backend (used in tests)."""
    with _lock:
        if hasattr(_get_backend, "_cache"):
            del _get_backend._cache  # type: ignore[attr-defined]


# -- public function API (unchanged interface) -------------------------------

def _call_backend(method_name: str, *args, **kwargs):
    """Invoke a registry backend method, recovering to in-memory if Redis dies.

    This protects long-lived app processes from a previously healthy Redis client
    that later becomes unreachable (the common case when Redis is not running or
    was restarted without the app being restarted).
    """
    backend = _get_backend()
    try:
        return getattr(backend, method_name)(*args, **kwargs)
    except Exception:
        if type(backend) is _RedisBackend:
            logger.warning(
                "Redis run registry backend became unavailable; falling back to in-memory.",
                exc_info=True,
            )
            _reset_backend()
            backend = _get_backend()
            return getattr(backend, method_name)(*args, **kwargs)
        raise


def register_active(user_id: str, run_id: str) -> None:
    _call_backend("register_active", user_id, run_id)


def unregister_active(user_id: str, run_id: str) -> None:
    _call_backend("unregister_active", user_id, run_id)


def stop_all(user_id: str) -> int:
    """Mark every active run of this user as stopped. Returns count."""
    return _call_backend("stop_all", user_id)


def stop_run(user_id: str, run_id: str) -> bool:
    """Mark a run as stopped."""
    return _call_backend("stop_run", user_id, run_id)


def is_stopped(user_id: str, run_id: str | None) -> bool:
    return bool(_call_backend("is_stopped", user_id, run_id))


def request_pause(user_id: str, run_id: str) -> None:
    """Ask a running run to pause at its next checkpoint (spec §24)."""
    _call_backend("request_pause", user_id, run_id)


def clear_pause(user_id: str, run_id: str) -> None:
    _call_backend("clear_pause", user_id, run_id)


def is_paused(user_id: str, run_id: str | None) -> bool:
    return bool(_call_backend("is_paused", user_id, run_id))


def active_count(user_id: str) -> int:
    return _call_backend("active_count", user_id)
