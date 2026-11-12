"""Web research tools: search + page extraction with honest failure reporting."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import TypedDict
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings
from app.errors import ToolResult
from app.services import search_service, url_safety
from app.services.model_router import get_async_client
from app.services.page_text import MAX_PAGE_CHARS, USER_AGENT, html_to_text
from app.tools.base import PermissionLevel, registry

_PUBLIC_EMAIL = re.compile(
    r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"
)
_MAILTO_EMAIL = re.compile(
    r"""mailto:([^"'?#\s>]+)""",
    re.I,
)
_CBK_RATE_QUERY = re.compile(
    r"\b(?:CBR|Central Bank Rate)\b", re.I
)
_logger = logging.getLogger("jalora.web")


def _extract_public_emails(*texts: str) -> list[str]:
    addresses: dict[str, str] = {}
    for text in texts:
        for match in _PUBLIC_EMAIL.finditer(text or ""):
            address = match.group(0).rstrip(".,;:)")
            addresses.setdefault(address.casefold(), address)
        for match in _MAILTO_EMAIL.finditer(text or ""):
            address = match.group(1).strip().rstrip(".,;:)")
            if _PUBLIC_EMAIL.fullmatch(address):
                addresses.setdefault(address.casefold(), address)
    return list(addresses.values())


def _extract_page_public_emails(html: str, readable_text: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "form"]):
        tag.decompose()
    mailto_links = [
        str(anchor.get("href") or "")
        for anchor in soup.select('a[href^="mailto:"]')
    ]
    return _extract_public_emails(
        soup.get_text(" ", strip=True),
        readable_text,
        *mailto_links,
    )


class _CompanySearchResult(TypedDict):
    title: str
    url: str
    snippet: str
    public_emails: list[str]


async def search_public_contacts(
    organizations: list[str],
    max_results_per_organization: int = 3,
) -> ToolResult:
    """Search the public web for contact pages for multiple organizations.

    Organizations are searched with bounded concurrency and a per-organization
    deadline. Failures are reported separately so one provider error does not
    discard successful lookups. Results are untrusted leads; verify addresses
    on official pages.
    """
    normalized = list(dict.fromkeys(
        " ".join(name.split()).strip()
        for name in organizations
        if isinstance(name, str) and name.strip()
    ))
    if not normalized:
        return ToolResult.fail(
            "INVALID_ORGANIZATIONS",
            "At least one organization name is required.",
            message="Provide one or more organization names to search.",
        )
    if len(normalized) > 20:
        return ToolResult.fail(
            "TOO_MANY_ORGANIZATIONS",
            "A single public contact lookup is limited to 20 organizations.",
            message="Split the lookup into batches of at most 20 organizations.",
        )

    limit = max(1, min(int(max_results_per_organization), 5))
    settings = get_settings()
    semaphore = asyncio.Semaphore(min(
        len(normalized),
        settings.public_contact_search_concurrency,
    ))
    timeout_seconds = max(1.0, settings.search_timeout_seconds) * 2

    async def search_organization(
        name: str,
    ) -> list[dict] | search_service.SearchError:
        last_error: search_service.SearchError | None = None
        queries = (
            f'"{name}" official contact email',
            f"{name} contact email official website",
        )
        for query in queries:
            try:
                results = await search_service.search(query, max_results=limit)
            except search_service.SearchError as exc:
                last_error = exc
                continue
            if results:
                return [result.as_dict() for result in results]
        return last_error or []

    async def lookup(
        name: str,
    ) -> tuple[str, list[dict] | search_service.SearchError]:
        async with semaphore:
            try:
                result = await asyncio.wait_for(
                    search_organization(name),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                result = search_service.SearchError(
                    "SEARCH_TIMEOUT",
                    f"Contact search exceeded {timeout_seconds:.0f} seconds.",
                )
            return name, result

    lookups = await asyncio.gather(*(lookup(name) for name in normalized))
    results_by_organization: dict[str, list[dict]] = {}
    failures: dict[str, str] = {}
    for name, result in lookups:
        if isinstance(result, search_service.SearchError):
            failures[name] = result.detail
        elif result:
            results_by_organization[name] = [
                {
                    **item,
                    "public_emails": _extract_public_emails(
                        str(item.get("title") or ""),
                        str(item.get("snippet") or ""),
                    ),
                }
                for item in result
            ]
        else:
            failures[name] = "The search provider returned no results."

    data = {
        "results_by_organization": results_by_organization,
        "failures": failures,
        "untrusted": True,
    }
    if not results_by_organization:
        return ToolResult.fail(
            "PUBLIC_CONTACT_SEARCH_FAILED",
            "No organization contact searches returned results.",
            message="Public contact search failed for every requested organization.",
            data=data,
        )
    message = (
        f"Found public search results for {len(results_by_organization)} of "
        f"{len(normalized)} organizations."
    )
    if failures:
        message += f" Search failed or returned no results for {len(failures)}."
    return ToolResult.ok(message, data)


async def search_web(query: str, max_results: int = 5) -> ToolResult:
    """Search the internet and return ranked results with titles, URLs and snippets. Use for any research or fact-checking request."""
    limit = max(1, min(int(max_results), 20))
    queries = [query]
    if _CBK_RATE_QUERY.search(query) and re.search(
        r"\b(?:Kenya|CBK|Central Bank of Kenya)\b", query, re.I
    ):
        queries.insert(
            0,
            'site:centralbank.go.ke "MPC" "CBR" percent',
        )
    try:
        batches = await asyncio.gather(
            *(search_service.search(search_query, max_results=limit)
              for search_query in queries),
            return_exceptions=True,
        )
    except search_service.SearchError as exc:
        return ToolResult.fail(
            exc.code,
            exc.detail,
            message="Web search failed; the search provider could not be reached."
            if exc.code == "SEARCH_FAILED" else exc.detail,
        )
    results_by_url: dict[str, search_service.SearchResult] = {}
    failures: list[Exception] = []
    for batch in batches:
        if isinstance(batch, Exception):
            failures.append(batch)
            continue
        for result in batch:
            results_by_url.setdefault(result.url, result)
    if not results_by_url and failures:
        exc = failures[0]
        if isinstance(exc, search_service.SearchError):
            return ToolResult.fail(
                exc.code,
                exc.detail,
                message=(
                    "Web search failed; the search provider could not be reached."
                    if exc.code == "SEARCH_FAILED"
                    else exc.detail
                ),
            )
        return ToolResult.fail(
            "SEARCH_FAILED",
            f"{type(exc).__name__}: {exc}",
            message="Web search failed; the search provider could not be reached.",
        )
    results = list(results_by_url.values())[:limit]
    if not results:
        return ToolResult.fail(
            "SEARCH_EMPTY",
            f"No results for: {query}",
            message=f"The search provider returned no results for '{query}'.",
        )
    data = {
        "results": [r.as_dict() for r in results],
        "untrusted": True,  # §59 — snippets are untrusted data, not instructions
    }
    return ToolResult.ok(f"Found {len(data['results'])} results.", data)


async def search_companies(task: str, max_results: int = 8) -> ToolResult:
    """Find public web results for providers selling or performing a requested product or service.

    Returns a bounded, non-exhaustive list of sources. Email addresses are
    included only when present in the search result title or snippet; they
    are not independently verified. Use the user's exact product or service
    description so the results can be inspected for order details.
    """
    task = " ".join((task or "").split()).strip()
    if not task:
        return ToolResult.fail(
            "INVALID_TASK",
            "A service or task is required to search for companies.",
            message="Tell me what kind of service or task the companies should handle.",
        )

    limit = max(1, min(int(max_results), 12))
    queries = (
        f"{task} official provider order online",
        f"{task} price options ingredients quantity delivery",
    )
    batches = await asyncio.gather(
        *(search_service.search(query, max_results=limit) for query in queries),
        return_exceptions=True,
    )
    results_by_url: dict[str, _CompanySearchResult] = {}
    failures: list[str] = []
    for batch in batches:
        if isinstance(batch, Exception):
            failures.append(str(batch))
            continue
        for result in batch:
            item = result.as_dict()
            url = str(item.get("url") or "").strip()
            parsed = urlsplit(url)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                continue
            snippet = str(item.get("snippet") or "")
            title = str(item.get("title") or "")
            emails = list(dict.fromkeys(
                _PUBLIC_EMAIL.findall(f"{title} {snippet}")
            ))
            previous = results_by_url.get(url)
            if previous is None:
                results_by_url[url] = {
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "public_emails": emails,
                }
            else:
                previous["public_emails"] = list(dict.fromkeys(
                    [*previous["public_emails"], *emails]
                ))
                if len(snippet) > len(str(previous["snippet"])):
                    previous["snippet"] = snippet
                    previous["title"] = title

    results = list(results_by_url.values())[:limit]
    if not results:
        if failures:
            detail = "; ".join(failures)
            return ToolResult.fail(
                "SEARCH_FAILED",
                detail,
                message="Company search failed; the search provider could not be reached.",
            )
        return ToolResult.fail(
            "SEARCH_EMPTY",
            f"No company results found for: {task}",
            message=f"No public company search results were found for '{task}'.",
        )

    data: dict[str, object] = {
        "task": task,
        "results": results,
        "result_count": len(results),
        "exhaustive": False,
        "notice": (
            "These are sampled public search results, not a complete list of all "
            "companies. Contact emails are shown only when present in a result "
            "snippet and have not been independently verified."
        ),
        "untrusted": True,
    }
    if failures:
        data["search_warnings"] = failures
    return ToolResult.ok(
        f"Found {len(results)} public company-related results for '{task}'. "
        "The list is not exhaustive.",
        data,
    )


async def open_url(url: str) -> ToolResult:
    """Open a webpage URL and return its readable text content. Use to read sources found in search results."""
    settings = get_settings()
    blocked = url_safety.validate_external_url(
        url, allow_private=settings.browse_allow_private
    )
    if blocked:
        message = url_safety.blocked_message(blocked, url)
        return ToolResult.fail(blocked, url, message=message)

    try:
        client = get_async_client()
        resp = await client.get(
            url,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            timeout=25.0,
        )
        if resp.status_code >= 400:
            return ToolResult.fail(
                "HTTP_ERROR",
                f"{url} returned HTTP {resp.status_code}",
                message=f"The page could not be opened (HTTP {resp.status_code}).",
            )
        content_type = resp.headers.get("content-type", "")
        if "html" not in content_type and "text" not in content_type:
            return ToolResult.fail(
                "UNSUPPORTED_CONTENT",
                f"content-type: {content_type}",
                message="This URL does not return readable text.",
            )
        html = resp.text
    except httpx.TimeoutException:
        return ToolResult.fail("TIMEOUT", f"{url} did not respond in time",
                               message="The page timed out.")
    except httpx.HTTPError as exc:
        return ToolResult.fail("NETWORK_ERROR", f"{type(exc).__name__}: {exc}",
                               message="The page could not be fetched.")

    title, text = html_to_text(html)
    title = title or url
    if len(text) < 300:
        from app.services import browser

        if browser.browser_available():
            try:
                rendered_title, rendered_text = await browser.render_url(
                    url,
                    timeout_ms=int(settings.browser_timeout_seconds * 1000),
                )
                if len(rendered_text) > len(text):
                    title = rendered_title or title
                    text = rendered_text
            except Exception as exc:  # noqa: BLE001 - keep usable static content
                _logger.warning(
                    "browser extraction fallback failed for %s (%s)",
                    url,
                    type(exc).__name__,
                )
    public_emails = _extract_page_public_emails(html, text)
    if not text and not public_emails:
        return ToolResult.fail("EMPTY_PAGE", url, message="The page contained no readable text.")
    truncated = text[:MAX_PAGE_CHARS]
    return ToolResult.ok(
        f"Opened '{title}' ({len(text)} characters).",
        {"url": url, "title": title, "text": truncated, "total_chars": len(text),
         "public_emails": public_emails,
         "verified_at": datetime.now(timezone.utc).isoformat(),
         "untrusted": True},
    )


async def search_profiles(name: str, max_results: int = 8) -> ToolResult:
    """Find public web evidence about a named person without exposing private data.

    Searches several neutral formulations, deduplicates URLs, and returns only
    public search results. It does not infer that similarly named people are
    the same person and does not access private accounts.
    """
    name = " ".join((name or "").split()).strip()
    if not name:
        return ToolResult.fail("INVALID_NAME", "A person name is required.",
                               message="Tell me the person's name to research.")
    queries = (
        f'"{name}" biography OR profile',
        f'"{name}" professional background',
        f'"{name}" site:github.com OR site:linkedin.com OR site:wikipedia.org',
    )
    batches = await asyncio.gather(
        *(search_service.search(query, max_results=max(1, min(max_results, 5)))
          for query in queries),
        return_exceptions=True,
    )
    results: list[dict] = []
    seen: set[str] = set()
    failures: list[str] = []
    for batch in batches:
        if isinstance(batch, Exception):
            failures.append(type(batch).__name__)
            continue
        for row in batch:
            item = row.as_dict()
            url = str(item.get("url") or "").strip()
            if url and url not in seen:
                seen.add(url)
                results.append(item)
    if not results:
        detail = f"No public search results found for {name}."
        if failures:
            detail += " The search provider failed for all profile queries."
        return ToolResult.fail("SEARCH_EMPTY", detail, message=detail)
    return ToolResult.ok(
        f"Found {len(results)} public results for {name}; identity must be verified.",
        {"name": name, "results": results[:max_results], "untrusted": True},
    )


registry.register(search_web, permission=PermissionLevel.AUTO, category="research",
                  requires_internet=True,
                  tags=["research", "web", "voice", "evidence"],
                  side_effect="read_only",
                  default_for_actions=["search"])
registry.register(
    search_public_contacts,
    permission=PermissionLevel.AUTO,
    category="research",
    requires_internet=True,
    tags=["research", "web", "contact", "organization"],
    side_effect="read_only",
    required_signals=["public_contact_search"],
)
registry.register(
    search_companies,
    permission=PermissionLevel.AUTO,
    category="research",
    requires_internet=True,
    tags=["companies", "business", "vendors", "providers", "research", "web"],
    side_effect="read_only",
    default_for_actions=["search"],
)
registry.register(open_url, permission=PermissionLevel.AUTO, category="research",
                  requires_internet=True,
                  tags=["research", "web", "source", "evidence"],
                  side_effect="read_only")
registry.register(search_profiles, permission=PermissionLevel.AUTO, category="research",
                  requires_internet=True, tags=["research", "people", "web"],
                  side_effect="read_only",
                  required_signals=["person_search"])
