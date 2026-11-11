"""Phase L Redis-backed run registry tests (§58)."""
from __future__ import annotations

import os
from types import SimpleNamespace

from app.services import run_registry


def test_unavailable_redis_falls_back_to_in_memory(monkeypatch):
    class UnavailableRedisBackend:
        def __init__(self, _redis_url):
            self._r = self

        def ping(self):
            raise ConnectionError("Redis unavailable")

    monkeypatch.setattr(run_registry, "_force_in_memory", False)
    monkeypatch.setattr(
        run_registry, "get_settings",
        lambda: SimpleNamespace(redis_url="redis://unavailable"),
    )
    monkeypatch.setattr(run_registry, "_RedisBackend", UnavailableRedisBackend)
    run_registry._reset_backend()

    try:
        run_registry.register_active("user-offline", "run-1")
        assert run_registry.active_count("user-offline") == 1
        run_registry.unregister_active("user-offline", "run-1")
        assert run_registry.active_count("user-offline") == 0
    finally:
        run_registry._reset_backend()


def test_redis_disconnect_during_run_falls_back_to_in_memory(monkeypatch):
    class DisappearingRedisBackend:
        instances = 0

        def __init__(self, _redis_url):
            type(self).instances += 1
            self.instance = type(self).instances
            self._r = self

        def ping(self):
            if self.instance > 1:
                raise ConnectionError("Redis unavailable")

        def register_active(self, _user_id, _run_id):
            raise ConnectionError("Redis disconnected")

    monkeypatch.setattr(run_registry, "_force_in_memory", False)
    monkeypatch.setattr(
        run_registry, "get_settings",
        lambda: SimpleNamespace(redis_url="redis://flaky"),
    )
    monkeypatch.setattr(run_registry, "_RedisBackend", DisappearingRedisBackend)
    run_registry._reset_backend()

    try:
        run_registry.register_active("user-disconnected", "run-1")
        assert run_registry.active_count("user-disconnected") == 1
    finally:
        run_registry._reset_backend()


def test_in_memory_fallback_no_redis(monkeypatch):
    """The in-memory backend is exercised directly, independent of Redis."""
    monkeypatch.setattr(run_registry, "_force_in_memory", True)
    run_registry._reset_backend()
    run_registry.register_active("user-mem", "run-1")
    assert run_registry.active_count("user-mem") == 1
    assert run_registry.is_stopped("user-mem", "run-1") is False
    assert run_registry.is_paused("user-mem", "run-1") is False


def test_stop_run_in_memory(monkeypatch):
    monkeypatch.setattr(run_registry, "_force_in_memory", True)
    run_registry._reset_backend()
    run_registry.register_active("user-stop", "run-a")
    assert run_registry.stop_run("user-stop", "run-a") is True
    assert run_registry.is_stopped("user-stop", "run-a") is True


def test_stop_all_in_memory(monkeypatch):
    monkeypatch.setattr(run_registry, "_force_in_memory", True)
    run_registry._reset_backend()
    run_registry.register_active("user-bulk", "run-1")
    run_registry.register_active("user-bulk", "run-2")
    count = run_registry.stop_all("user-bulk")
    assert count == 2
    assert run_registry.is_stopped("user-bulk", "run-1") is True
    assert run_registry.is_stopped("user-bulk", "run-2") is True


def test_pause_clear_pause_in_memory(monkeypatch):
    monkeypatch.setattr(run_registry, "_force_in_memory", True)
    run_registry._reset_backend()
    run_registry.register_active("user-pause", "run-x")
    run_registry.request_pause("user-pause", "run-x")
    assert run_registry.is_paused("user-pause", "run-x") is True
    run_registry.clear_pause("user-pause", "run-x")
    assert run_registry.is_paused("user-pause", "run-x") is False


def test_unregister_active_in_memory(monkeypatch):
    monkeypatch.setattr(run_registry, "_force_in_memory", True)
    run_registry._reset_backend()
    run_registry.register_active("user-unreg", "run-1")
    assert run_registry.active_count("user-unreg") == 1
    run_registry.unregister_active("user-unreg", "run-1")
    assert run_registry.active_count("user-unreg") == 0


def test_redis_backend_when_available():
    """If redis is importable and a Redis server is reachable, test the
    Redis backend.  Otherwise skip gracefully."""
    redis_url = os.environ.get("JALORA_REDIS_URL")
    if not redis_url:
        import pytest

        pytest.skip("JALORA_REDIS_URL not set — Redis backend test skipped")
    run_registry._reset_backend()
    # Redis persists across test runs; wipe any leftover state for this test's
    # user so the assertions below are deterministic (a prior stop_run would
    # otherwise leave ``run-1`` in the stopped set and make is_stopped() True).
    _backend = run_registry._get_backend()
    if hasattr(_backend, "_r"):
        _backend._r.delete(
            *(_backend._key("user-redis", s) for s in ("active", "stopped", "paused"))
        )
    run_registry.register_active("user-redis", "run-1")
    assert run_registry.is_stopped("user-redis", "run-1") is False
    run_registry.stop_run("user-redis", "run-1")
    assert run_registry.is_stopped("user-redis", "run-1") is True
    run_registry._reset_backend()
