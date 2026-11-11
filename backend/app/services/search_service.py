"""Search provider abstraction (spec §6).

Providers: duckduckgo (DDGS multi-engine, keyless), tavily (API key), mock
(offline, deterministic — used in tests). Selection via JALORA_SEARCH_PROVIDER:
`auto` picks tavily when a key is configured, else duckduckgo.

Latency hardening: every provider call is hard-bounded by
``search_timeout_seconds`` from config (default 45s, previously hardcoded
15s), and identical recent queries are served from a small TTL cache so
repeat lookups (and retries) return instantly.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import threading
import time
import weakref
from dataclasses import dataclass
from typing import Awaitable, Callable

SEARCH_CACHE_TTL_SECONDS = 60.0
_SEARCH_CACHE_MAX_ENTRIES = 64
_DDGS_TEXT_BACKENDS = ("auto", "bing", "yahoo", "duckduckgo", "brave", "google")
_TRANSIENT_RETRIES = 1
_logger = logging.getLogger("jalora.search")
_provider_semaphores: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncio.Semaphore
] = weakref.WeakKeyDictionary()
_provider_semaphores_lock = threading.Lock()


def _search_timeout() -> float:
    """Return the configured per-search-provider timeout (was hardcoded 15s).

    Reads from ``JALORA_SEARCH_TIMEOUT_SECONDS`` / ``search_timeout_seconds``
    setting so callers can tune it without code changes.  Defaults to 45s to
    give DuckDuckGo/LivSearch enough headroom for complex job-search queries.
    """
    from app.config import get_settings
    try:
        return get_settings().search_timeout_seconds
    except Exception:  # noqa: BLE001 — settings not yet available during import
        return 45.0


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str

    def as_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


class SearchError(Exception):
    """Provider-level failure with a machine-readable code."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


# Thread-local DDGS instance for connection/session reuse across searches.
# DDGS wraps a ``requests.Session``; creating a new one per call forces a TLS
# handshake every time, which is a major source of search latency.
_ddgs_local = threading.local()


def _get_ddgs() -> "DDGS":
    """Return a thread-local DDGS instance, lazily creating it.

    Each thread in the pool gets its own session, avoiding the thread-safety
    issues of sharing a single ``requests.Session`` across threads while still
    reusing connections for repeat searches on the same thread.
    """
    ddgs = getattr(_ddgs_local, "instance", None)
    if ddgs is None:
        from ddgs import DDGS as _DDGS  # local alias avoids name clash

        ddgs = _DDGS()
        _ddgs_local.instance = ddgs
    return ddgs


def _clean_snippet(text: str) -> str:
    """Unescape HTML entities, strip residual tags, and truncate to 300 chars."""
    if not text:
        return ""
    cleaned = html.unescape(text)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > 300:
        # Truncate at a word boundary so we don't split mid-word.
        cleaned = cleaned[:300].rsplit(" ", 1)[0] + "…"
    return cleaned


def _dedupe_results(results: list[SearchResult]) -> list[SearchResult]:
    """Deduplicate by URL, keeping the entry with the longest snippet for each URL."""
    seen: dict[str, SearchResult] = {}
    for r in results:
        if r.url not in seen:
            seen[r.url] = r
        elif len(r.snippet) > len(seen[r.url].snippet):
            seen[r.url] = r
    return list(seen.values())


async def _duckduckgo(query: str, max_results: int) -> list[SearchResult]:
    def _run() -> list[dict]:
        ddgs = _get_ddgs()
        last_exc: Exception | None = None
        for backend in _DDGS_TEXT_BACKENDS:
            try:
                results = list(ddgs.text(
                    query,
                    max_results=max_results,
                    backend=backend,
                    region="wt-wt",
                ))
            except Exception as exc:  # noqa: BLE001 — try another public backend
                last_exc = exc
                continue
            if results:
                return results
        if last_exc is not None:
            raise last_exc
        return []

    results = await asyncio.to_thread(_run)
    cleaned = [
        SearchResult(
            title=_clean_snippet(r.get("title", "")),
            url=r.get("href") or r.get("url", ""),
            snippet=_clean_snippet(r.get("body", "")),
        )
        for r in results
    ]
    return _dedupe_results(cleaned)


async def _tavily(query: str, max_results: int) -> list[SearchResult]:
    from app.config import get_settings
    from app.services.model_router import get_async_client

    api_key = get_settings().tavily_api_key
    if not api_key:
        raise SearchError("PROVIDER_NOT_CONFIGURED", "tavily_api_key is not set")
    client = get_async_client()
    resp = await client.post(
        "https://api.tavily.com/search",
        json={"api_key": api_key, "query": query, "max_results": max_results},
        timeout=_search_timeout(),
    )
    if resp.status_code >= 400:
        raise SearchError(
            "PROVIDER_ERROR", f"HTTP {resp.status_code}: {resp.text[:300]}"
        )
    payload = resp.json()
    return [
        SearchResult(
            title=r.get("title", ""),
            url=r.get("url", ""),
            snippet=r.get("content", ""),
        )
        for r in payload.get("results", [])
    ]


async def _mock(query: str, max_results: int) -> list[SearchResult]:
    """Offline deterministic provider for tests and mock mode (§55–56)."""
    slug = "-".join(query.lower().split())[:48] or "query"
    return [
        SearchResult(
            title=f"Mock result about {query}",
            url=f"https://example.invalid/{slug}",
            snippet=f"Deterministic offline snippet for '{query}' (mock provider).",
        ),
        SearchResult(
            title=f"Mock second source for {query}",
            url=f"https://example.invalid/{slug}/2",
            snippet="Another offline snippet used when no provider is configured.",
        ),
    ][:max_results]


ProviderFn = Callable[[str, int], Awaitable[list[SearchResult]]]

_PROVIDERS: dict[str, ProviderFn] = {
    "duckduckgo": _duckduckgo,
    "tavily": _tavily,
    "mock": _mock,
}


def available_providers() -> list[str]:
    return sorted(_PROVIDERS)


def _resolve(name: str | None) -> tuple[str, ProviderFn]:
    from app.config import get_settings

    choice = (name or get_settings().search_provider or "auto").strip().lower()
    if choice == "auto":
        settings = get_settings()
        choice = "tavily" if settings.tavily_api_key else "duckduckgo"
    fn = _PROVIDERS.get(choice)
    if fn is None:
        raise SearchError(
            "PROVIDER_NOT_CONFIGURED",
            f"Unknown search provider '{choice}' (available: {', '.join(available_providers())})",
        )
    return choice, fn


def resolve_provider(name: str | None) -> ProviderFn:
    return _resolve(name)[1]


# --- small TTL cache for exact repeat lookups -------------------------------

_CACHE: dict[tuple[str, str, int], tuple[float, tuple[SearchResult, ...]]] = {}


def _cache_get(key: tuple[str, str, int]) -> list[SearchResult] | None:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    ts, results = entry
    if time.monotonic() - ts > SEARCH_CACHE_TTL_SECONDS:
        _CACHE.pop(key, None)
        return None
    return [SearchResult(r.title, r.url, r.snippet) for r in results]


def _cache_set(key: tuple[str, str, int], results: list[SearchResult]) -> None:
    _CACHE[key] = (time.monotonic(), tuple(results))
    if len(_CACHE) > _SEARCH_CACHE_MAX_ENTRIES:
        for stale in list(_CACHE)[: _SEARCH_CACHE_MAX_ENTRIES // 2]:
            _CACHE.pop(stale, None)


def clear_cache() -> None:
    """Drop all cached queries (exposed for tests / provider switch)."""
    _CACHE.clear()


def _provider_semaphore() -> asyncio.Semaphore:
    """Return a per-event-loop limiter shared by all search requests."""
    from app.config import get_settings

    loop = asyncio.get_running_loop()
    with _provider_semaphores_lock:
        semaphore = _provider_semaphores.get(loop)
        if semaphore is None:
            semaphore = asyncio.Semaphore(
                get_settings().public_contact_search_concurrency
            )
            _provider_semaphores[loop] = semaphore
        return semaphore


async def search(query: str, max_results: int = 5,
                 provider: str | None = None) -> list[SearchResult]:
    if not query or not query.strip():
        return []
    max_results = max(1, min(int(max_results), 20))
    requested = (provider or "").strip().lower()
    name, fn = _resolve(provider)
    candidates: list[tuple[str, ProviderFn]] = [(name, fn)]

    # Automatic search is allowed to fail over between configured real
    # providers. Explicit provider selection remains strict so configuration
    # and provider failures are visible to callers rather than being hidden.
    if requested in ("", "auto"):
        from app.config import get_settings

        settings = get_settings()
        for fallback_name in ("tavily", "duckduckgo"):
            if fallback_name == name or fallback_name not in _PROVIDERS:
                continue
            if fallback_name == "tavily" and not settings.tavily_api_key:
                continue
            candidates.append((fallback_name, _PROVIDERS[fallback_name]))

    last_error: SearchError | None = None
    for candidate_name, candidate_fn in candidates:
        key = (candidate_name, query.strip(), max_results)
        cached = _cache_get(key)
        if cached is not None:
            return cached
        for attempt in range(_TRANSIENT_RETRIES + 1):
            try:
                async with _provider_semaphore():
                    results = await asyncio.wait_for(
                        candidate_fn(query.strip(), max_results),
                        timeout=_search_timeout(),
                    )
                _cache_set(key, results)
                return results
            except asyncio.TimeoutError:
                last_error = SearchError(
                    "SEARCH_TIMEOUT",
                    f"Search provider '{candidate_name}' did not respond within "
                    f"{_search_timeout():.0f}s",
                )
                break
            except SearchError as exc:
                last_error = exc
            except Exception as exc:  # noqa: BLE001 - honest structural failure
                last_error = SearchError(
                    "SEARCH_FAILED", f"{type(exc).__name__}: {exc}"
                )
            if (
                last_error.code != "SEARCH_FAILED"
                or attempt >= _TRANSIENT_RETRIES
            ):
                break
            _logger.warning(
                "transient search failure from %s; retrying query (%s)",
                candidate_name,
                last_error.detail,
            )
            await asyncio.sleep(0.25 * (attempt + 1))
        if len(candidates) > 1:
            _logger.warning(
                "search provider %s failed; trying configured fallback (%s)",
                candidate_name, last_error.code if last_error else "unknown",
            )

    if last_error is not None:
        raise last_error
    raise SearchError("PROVIDER_NOT_CONFIGURED", "No search provider is configured")
