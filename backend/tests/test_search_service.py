"""Search latency hardening: hard timeout, TTL cache, result clamping."""
from __future__ import annotations

import asyncio

import pytest

from app.services import search_service


@pytest.fixture(autouse=True)
def _clean_cache():
    search_service.clear_cache()
    yield
    search_service.clear_cache()


def _patch_provider(monkeypatch, fn):
    monkeypatch.setattr(search_service, "_PROVIDERS", {"mock": fn})


def test_search_caches_identical_queries(monkeypatch):
    calls: list[tuple[str, int]] = []

    async def counting(query: str, max_results: int):
        calls.append((query, max_results))
        return await search_service._mock(query, max_results)

    _patch_provider(monkeypatch, counting)
    a = asyncio.run(search_service.search("dashboard feedback", max_results=3, provider="mock"))
    b = asyncio.run(search_service.search("dashboard feedback", max_results=3, provider="mock"))
    assert calls == [("dashboard feedback", 3)]  # provider called exactly once
    assert [r.title for r in a] == [r.title for r in b]
    assert a[0] is not b[0]  # callers get independent copies


def test_search_cache_bypasses_on_different_readiness(monkeypatch):
    calls: list[int] = []

    async def counting(query: str, max_results: int):
        calls.append(max_results)
        return await search_service._mock(query, max_results)

    _patch_provider(monkeypatch, counting)
    asyncio.run(search_service.search("q", max_results=3, provider="mock"))
    asyncio.run(search_service.search("q", max_results=7, provider="mock"))
    assert len(calls) == 2  # different max_results -> different cache key


def test_search_times_out_gracefully(monkeypatch):
    async def slow(query: str, max_results: int):
        await asyncio.sleep(5)
        return []

    _patch_provider(monkeypatch, slow)
    monkeypatch.setattr(search_service, "_search_timeout", lambda: 0.05)
    with pytest.raises(search_service.SearchError) as exc:
        asyncio.run(search_service.search("x", provider="mock"))
    assert exc.value.code == "SEARCH_TIMEOUT"


def test_search_retries_transient_provider_failure(monkeypatch):
    calls = 0

    async def flaky(query: str, max_results: int):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary network failure")
        return await search_service._mock(query, max_results)

    _patch_provider(monkeypatch, flaky)
    results = asyncio.run(search_service.search("public contacts", provider="mock"))

    assert calls == 2
    assert results


def test_search_does_not_retry_non_transient_provider_errors(monkeypatch):
    calls = 0

    async def unavailable(query: str, max_results: int):
        nonlocal calls
        calls += 1
        raise search_service.SearchError(
            "PROVIDER_NOT_CONFIGURED", "provider is not configured"
        )

    _patch_provider(monkeypatch, unavailable)
    with pytest.raises(search_service.SearchError) as exc:
        asyncio.run(search_service.search("public contacts", provider="mock"))

    assert calls == 1
    assert exc.value.code == "PROVIDER_NOT_CONFIGURED"


def test_search_limits_concurrent_provider_calls(monkeypatch):
    active = 0
    peak_active = 0

    async def concurrent(query: str, max_results: int):
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        try:
            await asyncio.sleep(0.01)
            return await search_service._mock(query, max_results)
        finally:
            active -= 1

    _patch_provider(monkeypatch, concurrent)
    from app.config import get_settings

    async def run_many():
        return await asyncio.gather(*(
            search_service.search(f"query {index}", provider="mock")
            for index in range(8)
        ))

    results = asyncio.run(run_many())

    assert len(results) == 8
    assert peak_active <= get_settings().public_contact_search_concurrency


def test_search_clamps_max_results(monkeypatch):
    seen: dict[str, int] = {}

    async def rec(query: str, max_results: int):
        seen["max_results"] = max_results
        return []

    _patch_provider(monkeypatch, rec)
    asyncio.run(search_service.search("clamp", max_results=500, provider="mock"))
    assert seen["max_results"] == 20


def test_search_empty_query_never_hits_provider(monkeypatch):
    hit = False

    async def boom(query: str, max_results: int):
        nonlocal hit
        hit = True
        raise AssertionError("must not be called")

    _patch_provider(monkeypatch, boom)
    assert asyncio.run(search_service.search("   ", provider="mock")) == []
    assert not hit


def test_clear_cache_drops_entries(monkeypatch):
    calls: list[str] = []

    async def counting(query: str, max_results: int):
        calls.append(query)
        return await search_service._mock(query, max_results)

    _patch_provider(monkeypatch, counting)
    asyncio.run(search_service.search("once", provider="mock"))
    search_service.clear_cache()
    asyncio.run(search_service.search("once", provider="mock"))
    assert calls == ["once", "once"]


def test_ddgs_search_falls_back_to_available_text_backend(monkeypatch):
    observed_backends: list[str] = []

    class FakeDDGS:
        def text(
            self, query: str, *, max_results: int, backend: str, region: str
        ):
            observed_backends.append(backend)
            assert region == "wt-wt"
            if backend in {"auto", "bing"}:
                raise RuntimeError("No results found.")
            if backend == "yahoo":
                return [{
                    "title": "Official contact",
                    "href": "https://organization.example/contact",
                    "body": "Public contact details.",
                }]
            raise AssertionError(f"Unexpected backend call: {backend}")

    monkeypatch.setattr(search_service, "_get_ddgs", FakeDDGS)
    results = asyncio.run(search_service._duckduckgo("public contact", 3))

    assert [result.url for result in results] == [
        "https://organization.example/contact",
    ]
    assert observed_backends == ["auto", "bing", "yahoo"]


def test_ddgs_search_tries_another_backend_after_empty_results(monkeypatch):
    observed_backends: list[str] = []

    class FakeDDGS:
        def text(
            self, query: str, *, max_results: int, backend: str, region: str
        ):
            observed_backends.append(backend)
            assert region == "wt-wt"
            if backend == "auto":
                return []
            if backend == "bing":
                return [{
                    "title": "Contact page",
                    "href": "https://organization.example/contact",
                    "body": "Email the organization.",
                }]
            raise AssertionError(f"Unexpected backend call: {backend}")

    monkeypatch.setattr(search_service, "_get_ddgs", FakeDDGS)
    results = asyncio.run(search_service._duckduckgo("email", 1))

    assert len(results) == 1
    assert observed_backends == ["auto", "bing"]