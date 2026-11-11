"""Phase L global rate-limit middleware tests (§74)."""
from __future__ import annotations

import time

from app.services.rate_limiter import (
    _InMemoryRateLimiter,
    _RedisRateLimiter,
    check_rate_limit,
    get_rate_limiter,
)


def test_in_memory_rate_limiter_allows_within_limit():
    limiter = _InMemoryRateLimiter()
    limit = 5
    results = [limiter.is_allowed("test-key", limit) for _ in range(limit)]
    assert all(results)


def test_in_memory_rate_limiter_blocks_over_limit():
    limiter = _InMemoryRateLimiter()
    limit = 3
    results = [limiter.is_allowed("test-key2", limit) for _ in range(limit + 2)]
    assert results[:limit] == [True, True, True]
    assert results[limit:] == [False, False]


def test_in_memory_rate_limiter_separate_keys():
    """Different keys have independent budgets."""
    limiter = _InMemoryRateLimiter()
    for _ in range(5):
        assert limiter.is_allowed("key-a", 5) is True
    # key-b is independent
    assert limiter.is_allowed("key-b", 5) is True


def test_in_memory_rate_limiter_zero_disables():
    """limit=0 (or negative) disables rate limiting."""
    limiter = _InMemoryRateLimiter()
    for _ in range(100):
        assert limiter.is_allowed("unlimited", 0) is True


def test_in_memory_rate_limiter_bounds_key_cardinality_and_expires_idle_keys():
    limiter = _InMemoryRateLimiter(max_keys=2)
    assert limiter.is_allowed("key-a", 1, window_seconds=0.01)
    assert limiter.is_allowed("key-b", 1, window_seconds=0.01)
    assert limiter.is_allowed("key-c", 1, window_seconds=0.01) is False
    assert len(limiter._windows) == 2

    time.sleep(0.02)
    assert limiter.is_allowed("key-c", 1, window_seconds=0.01)
    assert len(limiter._windows) == 1


def test_redis_rate_limit_fallback_keeps_state_after_backend_failure():
    class _FailingPipeline:
        def incr(self, _key):
            return self

        def expire(self, _key, _seconds):
            return self

        def execute(self):
            raise OSError("Redis unavailable")

    class _FailingRedis:
        def pipeline(self):
            return _FailingPipeline()

    limiter = _RedisRateLimiter.__new__(_RedisRateLimiter)
    limiter._r = _FailingRedis()
    limiter._fallback = _InMemoryRateLimiter()

    assert check_rate_limit(limiter, "user-a", 2) is True
    assert check_rate_limit(limiter, "user-a", 2) is True
    assert check_rate_limit(limiter, "user-a", 2) is False


def test_http_middleware_uses_memory_fallback_when_redis_fails(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    class _FailingRedis:
        def pipeline(self):
            raise OSError("Redis unavailable")

    limiter = _RedisRateLimiter.__new__(_RedisRateLimiter)
    limiter._r = _FailingRedis()
    limiter._fallback = _InMemoryRateLimiter()
    monkeypatch.setattr("app.main.get_rate_limiter", lambda redis_url=None: limiter)

    with TestClient(create_app()) as client:
        response = client.get("/api/auth/me")

    assert response.status_code == 401


def test_check_rate_limit_delegates_to_limiter():
    limiter = _InMemoryRateLimiter()
    assert check_rate_limit(limiter, "test-check", 2) is True
    assert check_rate_limit(limiter, "test-check", 2) is True
    assert check_rate_limit(limiter, "test-check", 2) is False


def test_check_rate_limit_zero_disabled():
    limiter = _InMemoryRateLimiter()
    for _ in range(10):
        assert check_rate_limit(limiter, "test-check2", 0) is True


def test_get_rate_limiter_falls_back_to_memory_when_no_redis():
    """When redis_url is None, returns in-memory limiter."""
    rl = get_rate_limiter(None)
    assert isinstance(rl, _InMemoryRateLimiter)


def test_get_list_read_endpoints_are_whitelisted_from_global_budget(monkeypatch):
    """Phase L §74 root-cause regression: read-only list endpoints must NEVER be
    starved by the shared per-IP budget (the source of the intermittent blank
    sidebar / schedules / workspace screens = a 429 rendered as an empty state).

    Build a fresh app whose limiter DENIES everything. Whitelisted GET reads
    (conversations/schedules/tasks/workspace/memory) must bypass the budget and
    reach the route handler (→ 401 without a token, but NEVER 429), while a
    non-whitelisted GET is still correctly 429-enforced. No auth needed: the
    whitelist is enforced in middleware, before the auth gate.
    """
    from fastapi.testclient import TestClient

    from app.main import create_app

    class _DenyLimiter:
        """Always exhausts the per-IP budget."""

        def is_allowed(
            self, key: str, limit: int, window_seconds: float = 60.0
        ) -> bool:
            return False

    # Force the middleware (built inside create_app) to use a deny-all limiter.
    monkeypatch.setattr(
        "app.main.get_rate_limiter",
        lambda redis_url=None: _DenyLimiter(),
    )

    app2 = create_app()
    with TestClient(app2) as c:
        # whitelisted GET list reads bypass the budget entirely. Unauthenticated
        # they hit the auth gate (401) — the contract under test is that they are
        # never 429'd, no matter how exhausted the budget is.
        for path in (
            "/api/conversations",
            "/api/schedules",
            "/api/tasks",
            "/api/workspace",
            "/api/memory",
            "/api/notifications/unread-count",
            "/api/approvals",
            "/api/devices/actions",
            "/api/devices/remote-commands/target",
            "/api/health",
        ):
            r = c.get(path)  # unauthenticated
            assert r.status_code != 429, (path, r.status_code, r.text)

        # a whitelisted read with a real route returns 200 when authed; here we
        # just confirm the path is wired (not 404) — 401 means "auth gate hit".
        s = c.get("/api/schedules")
        assert s.status_code in (200, 401)

        # a non-whitelisted GET read is still subject to the budget -> 429
        denied = c.get("/api/auth/me")  # unauthenticated
        assert denied.status_code == 429
        assert denied.json()["error"]["code"] == "RATE_LIMITED"
