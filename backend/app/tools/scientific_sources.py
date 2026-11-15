"""Provider-backed scholarly metadata search for science research."""
from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import Any

import httpx

from app.config import get_settings
from app.errors import ToolResult
from app.services.model_router import get_async_client
from app.services import search_service
from app.tools.base import PermissionLevel, registry
from app.tools.web import (
    _SEARCH_TERM_FAMILIES,
    _normalize_search_query,
    _search_result_relevance,
    _search_terms,
)

logger = logging.getLogger("jalora.scientific_sources")
_CROSSREF_WORKS_URL = "https://api.crossref.org/works"
_MAX_QUERY_CHARS = 500
_MAX_RESULTS = 10
_NON_ENTITY_QUERY_STARTS = frozenset({
    "can", "compare", "could", "current", "did", "does", "do", "explain",
    "find", "first", "give", "how", "identify", "is", "latest", "please",
    "research", "search", "show", "summarize", "tell", "what", "when",
    "where", "which", "who", "why",
})


def _plain_abstract(value: str) -> str:
    text = re.sub(r"<[^>]*>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _published_year(item: dict[str, Any]) -> int | None:
    for field in ("published", "published-print", "published-online", "created"):
        date = item.get(field)
        parts = date.get("date-parts") if isinstance(date, dict) else None
        if isinstance(parts, list) and parts and isinstance(parts[0], list):
            year = parts[0][0] if parts[0] else None
            if isinstance(year, int):
                return year
    return None


def _query_entity_anchors(query: str) -> set[str]:
    anchors = {
        match.casefold()
        for match in re.findall(r"\b(?:[A-Z][a-z]{2,}|[A-Z]{2,})\b", query)
    }
    if query.split() and query.split()[0].casefold() in _NON_ENTITY_QUERY_STARTS:
        anchors.discard(query.split()[0].casefold())
    return anchors


def _has_query_topic_evidence(query: str, record_text: str) -> bool:
    query_terms = _search_terms(query)
    record_terms = _search_terms(record_text)
    return all(
        not query_terms.intersection(family)
        or record_terms.intersection(family)
        for family in _SEARCH_TERM_FAMILIES
    )


async def search_scientific_sources(
    query: str,
    max_results: int = 5,
) -> ToolResult:
    """Search Crossref scholarly records; verify source pages before citing.

    Crossref metadata does not establish peer-review status or scientific
    consensus. Returned records are untrusted research leads.
    """
    normalized_query = _normalize_search_query(query or "")
    if not normalized_query:
        return ToolResult.fail(
            "INVALID_QUERY",
            "A scientific literature search query is required.",
            message="Provide a scientific topic or research question.",
        )
    if len(normalized_query) > _MAX_QUERY_CHARS:
        return ToolResult.fail(
            "QUERY_TOO_LONG",
            f"Scientific search queries are limited to {_MAX_QUERY_CHARS} characters.",
            message="Shorten the scientific search query and try again.",
        )

    limit = max(1, min(int(max_results), _MAX_RESULTS))
    timeout = max(5.0, get_settings().search_timeout_seconds)
    client = get_async_client(timeout=timeout)
    try:
        response = await asyncio.wait_for(
            client.get(
                _CROSSREF_WORKS_URL,
                params={
                    "query.bibliographic": normalized_query,
                    "rows": min(limit * 3, 30),
                    "select": (
                        "DOI,title,author,published,published-print,"
                        "published-online,container-title,URL,abstract,type"
                    ),
                },
                timeout=timeout,
            ),
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except asyncio.TimeoutError:
        return ToolResult.fail(
            "SCIENCE_SEARCH_TIMEOUT",
            "Crossref did not respond before the configured search timeout.",
            message="Scholarly search timed out; no literature results were verified.",
        )
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Crossref search failed: %s", type(exc).__name__)
        return ToolResult.fail(
            "SCIENCE_SEARCH_FAILED",
            f"Crossref search failed ({type(exc).__name__}).",
            message="Scholarly search is unavailable; no literature results were verified.",
        )

    message = payload.get("message") if isinstance(payload, dict) else None
    items = message.get("items") if isinstance(message, dict) else None
    if not isinstance(items, list):
        return ToolResult.fail(
            "SCIENCE_SEARCH_INVALID_RESPONSE",
            "Crossref returned an invalid work-list response.",
            message="Scholarly search returned an invalid response.",
        )

    results: list[dict[str, Any]] = []
    entity_anchors = _query_entity_anchors(normalized_query)
    minimum_relevance = min(
        2,
        len(re.findall(r"[a-z0-9]+", normalized_query.casefold())),
    )
    for item in items:
        if not isinstance(item, dict):
            continue
        titles = item.get("title")
        title = next(
            (value.strip() for value in titles
             if isinstance(value, str) and value.strip()),
            "",
        ) if isinstance(titles, list) else ""
        if not title:
            continue
        doi = str(item.get("DOI") or "").strip()
        raw_url = str(item.get("URL") or "").strip()
        url = f"https://doi.org/{doi}" if doi else raw_url
        abstract = _plain_abstract(str(item.get("abstract") or ""))
        record_text = f"{title} {abstract}"
        if (
            not url
            or any(
                re.search(rf"\b{re.escape(anchor)}\b", record_text, re.I) is None
                for anchor in entity_anchors
            )
            or not _has_query_topic_evidence(normalized_query, record_text)
            or _search_result_relevance(
                normalized_query,
                search_service.SearchResult(title=title, url=url, snippet=abstract),
            ) < minimum_relevance
        ):
            continue
        authors = item.get("author")
        author_names = [
            " ".join(part for part in (
                str(author.get("given") or "").strip(),
                str(author.get("family") or "").strip(),
            ) if part)
            for author in authors
            if isinstance(author, dict)
        ] if isinstance(authors, list) else []
        journals = item.get("container-title")
        journal = next(
            (value.strip() for value in journals
             if isinstance(value, str) and value.strip()),
            "",
        ) if isinstance(journals, list) else ""
        results.append({
            "title": title,
            "url": url,
            "doi": doi,
            "authors": author_names,
            "year": _published_year(item),
            "publication": journal,
            "abstract": abstract,
            "record_type": str(item.get("type") or ""),
            "index": "Crossref",
            "peer_reviewed": None,
        })
        if len(results) >= limit:
            break

    if not results:
        return ToolResult.fail(
            "SCIENCE_SEARCH_EMPTY",
            f"Crossref returned no usable records for '{normalized_query}'.",
            message="No scholarly records were found for that query.",
            data={"query": normalized_query, "untrusted": True},
        )
    return ToolResult.ok(
        f"Found {len(results)} Crossref records. Peer-review status and "
        "scientific consensus are not established by this metadata.",
        {
            "query": normalized_query,
            "index": "Crossref",
            "results": results,
            "untrusted": True,
        },
    )


registry.register(
    search_scientific_sources,
    permission=PermissionLevel.AUTO,
    category="research",
    requires_internet=True,
    tags=[
        "science", "scientific", "academic", "scholarly", "research", "paper",
        "evidence", "web",
    ],
    side_effect="read_only",
)
