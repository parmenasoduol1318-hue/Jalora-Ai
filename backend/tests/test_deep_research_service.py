import asyncio
from datetime import datetime

from app.errors import ToolResult
from app.services.deep_research import (
    MAX_SOURCES,
    MAX_TOTAL_CONTENT_CHARS,
    research,
)
from app.agents.graph import _extract_sources
from app.tools import deep_research_tools


def _search_results(*rows):
    async def search(query, max_results):
        return ToolResult.ok(
            "search complete",
            {"results": list(rows)[:max_results], "untrusted": True},
        )

    return search


def _opened_page(url, text, *, title="Example page", author=None):
    return ToolResult.ok(
        "page opened",
        {"url": url, "title": title, "text": text, "author": author},
    )


def test_stale_pages_are_identified_and_missing_publication_dates_are_unknown():
    rows = [
        {"title": "Dated report", "url": "https://reports.example/dated", "snippet": "Policy rate"},
        {"title": "Undated report", "url": "https://reports.example/undated", "snippet": "Policy rate"},
    ]

    async def open_page(url):
        if url.endswith("/dated"):
            return _opened_page(
                url,
                "Published: 2019-01-02. The policy rate remains at five percent according to this report.",
            )
        return _opened_page(
            url,
            "The policy rate remains at five percent according to this report with no date.",
        )

    result = asyncio.run(research(
        "policy rate",
        search_fn=_search_results(*rows),
        open_fn=open_page,
    ))

    assert [source["freshness"] for source in result["sources"]] == ["stale", "unknown"]
    assert result["sources"][0]["published_at"] == "2019-01-02"
    assert any("older than one year" in item for item in result["report"]["limitations"])
    assert any("Publication date was not established" in item for item in result["report"]["limitations"])


def test_inaccessible_page_is_not_cited_and_insufficient_evidence_is_honest():
    row = {"title": "Search snippet", "url": "https://example.test/missing", "snippet": "The policy rate is 5%."}

    async def inaccessible(url):
        return ToolResult.fail("HTTP_ERROR", "page unavailable", message="The page could not be opened.")

    result = asyncio.run(research(
        "policy rate",
        search_fn=_search_results(row),
        open_fn=inaccessible,
    ))

    assert result["status"] == "insufficient_evidence"
    assert result["sources"] == []
    assert result["claims"] == []
    assert result["errors"][0]["stage"] == "open"
    assert "search snippets are not evidence" in result["report"]["limitations"][0]


def test_missing_search_results_do_not_create_evidence():
    async def no_results(query, max_results):
        return ToolResult.ok("no results", {"results": []})

    result = asyncio.run(research(
        "policy rate",
        search_fn=no_results,
        open_fn=lambda url: _opened_page(url, ""),
    ))

    assert result["status"] == "insufficient_evidence"
    assert result["sources"] == []
    assert result["claims"] == []
    assert result["report"]["bibliography"] == []


def test_discourse_marker_is_removed_and_irrelevant_opened_pages_are_rejected():
    searched = []
    row = {
        "title": "FIRST California Northern State Championship",
        "url": "https://example.test/first",
        "snippet": "FIRST robotics event 2026",
    }

    async def search(query, max_results):
        searched.append(query)
        return await _search_results(row)(query, max_results)

    async def open_page(url):
        return _opened_page(
            url,
            "FIRST California Northern State Championship 2026 Add Match Videos! "
            "FIRST is the global Forum of Incident Response and Security Teams.",
        )

    result = asyncio.run(research(
        "First, why is Mombasa temperatures very high",
        search_fn=search,
        open_fn=open_page,
    ))

    assert searched == ["Mombasa temperatures high"]
    assert result["status"] == "insufficient_evidence"
    assert result["sources"] == []
    assert result["claims"] == []
    assert any(error["stage"] == "relevance" for error in result["errors"])


def test_relevant_page_excerpts_are_used_without_unrelated_page_prefix():
    row = {
        "title": "Mombasa coastal climate",
        "url": "https://climate.example/mombasa",
        "snippet": "Mombasa is hot",
    }

    async def open_page(url):
        return _opened_page(
            url,
            "A navigation banner with unrelated introductory text and menu items. "
            "Mombasa's coastal temperatures remain high because warm ocean currents "
            "and humid onshore winds reduce nighttime cooling.",
        )

    result = asyncio.run(research(
        "Why are Mombasa temperatures very high?",
        search_fn=_search_results(row),
        open_fn=open_page,
    ))

    assert result["status"] == "completed"
    assert result["sources"]
    assert "warm ocean currents" in result["sources"][0]["excerpt"]
    assert "navigation banner" not in result["sources"][0]["excerpt"]


def test_deep_research_tool_preserves_opened_page_citation_metadata(monkeypatch):
    evidence = {
        "status": "completed",
        "sources": [{
            "citation_id": "R1",
            "url": "https://climate.example/mombasa",
            "title": "Mombasa coastal climate",
            "excerpt": "Mombasa temperatures remain high.",
            "publisher": "climate.example",
            "author": "A. Writer",
            "retrieved_at": "2026-11-15T00:00:00+00:00",
            "published_at": "2026-11-14",
            "freshness": "current",
            "queries": ["coastal humidity"],
        }],
        "claims": [{"claim_id": "C1", "text": "Mombasa temperatures remain high."}],
    }

    async def fake_research(query, subquestions=None):
        return evidence

    monkeypatch.setattr(deep_research_tools, "research", fake_research)
    result = asyncio.run(deep_research_tools.deep_research("Mombasa climate"))

    assert result.success
    sources = _extract_sources("deep_research", result.data)
    assert sources[0]["citation_id"] == "R1"
    assert sources[0]["snippet"] == "Mombasa temperatures remain high."
    assert sources[0]["publisher"] == "climate.example"
    assert sources[0]["retrieved_at"] == "2026-11-15T00:00:00+00:00"
    assert sources[0]["queries"] == ["coastal humidity"]


def test_duplicate_urls_across_queries_are_opened_once():
    row1 = {"title": "First result", "url": "https://example.test/page#top", "snippet": "Policy"}
    row2 = {"title": "Duplicate", "url": "https://EXAMPLE.test/page/", "snippet": "Policy"}
    opened = []

    async def search(query, max_results):
        return ToolResult.ok("ok", {"results": [row1] if query == "first question" else [row2]})

    async def open_page(url):
        opened.append(url)
        return _opened_page(url, "The policy rate is reported by the official source at five percent.")

    result = asyncio.run(research(
        "policy rate",
        subquestions=["first question", "second question"],
        search_fn=search,
        open_fn=open_page,
    ))

    assert len(opened) == 1
    assert len(result["sources"]) == 1


def test_subquestions_share_the_source_budget_across_queries():
    searched = []
    opened = []

    async def search(query, max_results):
        searched.append(query)
        return ToolResult.ok("ok", {
            "results": [
                {
                    "title": f"Mombasa heat report {query} {index}",
                    "url": f"https://climate.example/{query}/{index}",
                    "snippet": "Mombasa heat",
                }
                for index in range(max_results)
            ],
        })

    async def open_page(url):
        opened.append(url)
        return _opened_page(
            url,
            "Mombasa is hot because coastal humidity slows nighttime cooling.",
        )

    result = asyncio.run(research(
        "Why is Mombasa hot?",
        subquestions=["coastal conditions", "urban heat", "regional climate"],
        max_sources=3,
        search_fn=search,
        open_fn=open_page,
    ))

    assert searched == ["coastal conditions", "urban heat", "regional climate"]
    assert len(result["sources"]) == 3
    assert {url.split("/")[3] for url in opened} == {
        "coastal conditions", "urban heat", "regional climate",
    }


def test_conflicting_numeric_claims_are_reported_as_unresolved():
    rows = [
        {"title": "Report A", "url": "https://a.example/rate", "snippet": ""},
        {"title": "Report B", "url": "https://b.example/rate", "snippet": ""},
    ]

    async def open_page(url):
        value = "5%" if url.startswith("https://a") else "7%"
        return _opened_page(url, f"The policy rate is {value} according to the central bank's latest published report.")

    result = asyncio.run(research(
        "policy rate",
        search_fn=_search_results(*rows),
        open_fn=open_page,
    ))

    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert conflict["resolution"] == "unresolved"
    assert len(conflict["citation_ids"]) == 2
    assert result["status"] == "partial"


def test_claims_carry_stable_citations_and_retrieval_provenance():
    row = {"title": "Search title", "url": "https://news.example/story", "snippet": "Search-only snippet"}

    async def open_page(url):
        return _opened_page(
            url,
            "The policy rate is five percent according to the official report published today.",
            title="Opened page title",
            author="A. Writer",
        )

    result = asyncio.run(research(
        "policy rate",
        search_fn=_search_results(row),
        open_fn=open_page,
    ))

    source = result["sources"][0]
    claim = result["claims"][0]
    assert source["citation_id"] == "R1"
    assert source["title"] == "Opened page title"
    assert source["publisher"] == "news.example"
    assert source["author"] == "A. Writer"
    assert datetime.fromisoformat(source["retrieved_at"]).tzinfo is not None
    assert claim["citation_ids"] == ["R1"]
    assert result["citation_map"][claim["claim_id"]] == ["R1"]
    assert result["report"]["bibliography"][0]["citation_id"] == "R1"
    assert result["report"]["bibliography"][0]["author"] == "A. Writer"
    assert result["report"]["bibliography"][0]["freshness"] == "unknown"
    assert result["report"]["source_count"] == 1
    assert result["report"]["status"] == "completed"
    assert "Search-only snippet" not in claim["text"]
    assert claim["content_untrusted"] is True
    assert result["retrieved_content_untrusted"] is True
    assert "never instructions" in result["trust_boundary"]


def test_source_count_is_capped_even_when_search_returns_more_results():
    rows = [
        {"title": f"Page {n}", "url": f"https://example.test/{n}", "snippet": ""}
        for n in range(10)
    ]
    opened = []

    async def open_page(url):
        opened.append(url)
        return _opened_page(url, "The policy rate was described in this public report.")

    result = asyncio.run(research(
        "policy rate",
        max_sources=100,
        search_fn=_search_results(*rows),
        open_fn=open_page,
    ))

    assert len(opened) == MAX_SOURCES
    assert len(result["sources"]) == MAX_SOURCES
    assert result["limits"]["max_sources"] == MAX_SOURCES


def test_total_opened_content_is_capped():
    rows = [
        {"title": f"Page {n}", "url": f"https://example.test/{n}", "snippet": ""}
        for n in range(MAX_SOURCES)
    ]

    async def open_page(url):
        return _opened_page(
            url,
            "The policy rate is published in this official report. " * 300,
        )

    result = asyncio.run(research(
        "policy rate",
        search_fn=_search_results(*rows),
        open_fn=open_page,
    ))

    assert result["limits"]["content_chars_processed"] == MAX_TOTAL_CONTENT_CHARS
    assert result["limits"]["content_chars_processed"] <= MAX_TOTAL_CONTENT_CHARS
    assert result["limits"]["content_budget_reached"] is True
    assert result["status"] == "partial"


def test_search_timeout_returns_bounded_failure_without_claims():
    async def slow_search(query, max_results):
        await asyncio.sleep(1)
        return ToolResult.ok("late", {"results": []})

    result = asyncio.run(research(
        "policy rate",
        timeout_seconds=0.1,
        search_fn=slow_search,
        open_fn=lambda url: _opened_page(url, ""),
    ))

    assert result["status"] == "timed_out"
    assert result["sources"] == []
    assert result["claims"] == []
    assert result["errors"][0]["reason"] == "timeout"


def test_page_timeout_preserves_honest_error_and_never_uses_snippet_as_claim():
    row = {"title": "Page", "url": "https://example.test/slow", "snippet": "The policy rate is 5%."}

    async def slow_open(url):
        await asyncio.sleep(1)
        return _opened_page(url, "The policy rate is five percent.")

    result = asyncio.run(research(
        "policy rate",
        timeout_seconds=0.1,
        search_fn=_search_results(row),
        open_fn=slow_open,
    ))

    assert result["status"] == "timed_out"
    assert result["sources"] == []
    assert result["claims"] == []
    assert result["errors"][0]["stage"] == "open"


def test_caller_limits_can_be_stricter_than_service_defaults():
    row = {"title": "Page", "url": "https://example.test/short", "snippet": ""}

    async def open_page(url):
        return _opened_page(url, "The policy rate is published in this official report.")

    result = asyncio.run(research(
        "policy rate",
        timeout_seconds=0,
        max_page_chars=20,
        search_fn=_search_results(row),
        open_fn=open_page,
    ))

    assert result["status"] == "timed_out"
    assert result["limits"]["timeout_seconds"] == 0
    assert result["limits"]["max_page_chars"] == 20
    assert result["sources"] == []


def test_subquestion_specific_sources_report_query_coverage():
    subquestions = [
        "coastal humidity and night cooling",
        "urban heat island mitigation methods",
    ]

    async def search(query, max_results):
        row = (
            {
                "title": "Coastal humidity and cooling",
                "url": "https://coast.example/climate",
                "snippet": "",
            }
            if query.startswith("coastal")
            else {
                "title": "Urban heat island mitigation",
                "url": "https://city.example/heat",
                "snippet": "",
            }
        )
        return ToolResult.ok("ok", {"results": [row]})

    async def open_page(url):
        if "coast.example" in url:
            text = "Coastal humidity slows night cooling near Mombasa's shore."
        else:
            text = "Urban heat islands trap warmth in cities; tree cover reduces temperatures."
        return _opened_page(url, text)

    result = asyncio.run(research(
        "Explain these two distinct conditions.",
        subquestions=subquestions,
        max_sources=2,
        search_fn=search,
        open_fn=open_page,
    ))

    assert result["status"] == "completed"
    assert [item["status"] for item in result["query_coverage"]] == [
        "sourced", "sourced",
    ]
    assert [source["queries"] for source in result["sources"]] == [
        [result["subquestions"][0]], [result["subquestions"][1]],
    ]
    assert [claim["queries"] for claim in result["claims"]] == [
        [result["subquestions"][0]], [result["subquestions"][1]],
    ]


def test_missing_subquestion_evidence_makes_research_partial():
    subquestions = ["coastal humidity and night cooling", "urban heat mitigation"]
    row = {
        "title": "Mombasa coastal temperatures",
        "url": "https://coast.example/climate",
        "snippet": "",
    }

    async def search(query, max_results):
        return ToolResult.ok(
            "ok",
            {"results": [row] if query.startswith("coastal") else []},
        )

    async def open_page(url):
        return _opened_page(
            url,
            "Coastal humidity keeps Mombasa temperatures high by slowing night cooling.",
        )

    result = asyncio.run(research(
        "Why are Mombasa temperatures high?",
        subquestions=subquestions,
        search_fn=search,
        open_fn=open_page,
    ))

    assert result["status"] == "partial"
    assert [item["status"] for item in result["query_coverage"]] == [
        "sourced", "insufficient_evidence",
    ]
    assert any("No opened-page evidence covered" in item for item in result["report"]["limitations"])


def test_corroboration_requires_matching_claims_from_distinct_publishers():
    rows = [
        {"title": "Report A", "url": "https://a.example/one", "snippet": ""},
        {"title": "Report A copy", "url": "https://a.example/two", "snippet": ""},
        {"title": "Report B", "url": "https://b.example/report", "snippet": ""},
    ]

    async def open_page(url):
        return _opened_page(
            url,
            "The policy rate is 5 percent according to the central bank's latest report.",
        )

    result = asyncio.run(research(
        "policy rate",
        search_fn=_search_results(*rows),
        open_fn=open_page,
    ))

    assert len(result["corroborations"]) == 1
    corroboration = result["corroborations"][0]
    assert corroboration["distinct_publisher_count"] == 2
    assert corroboration["publishers"] == ["a.example", "b.example"]
    assert len(corroboration["citation_ids"]) == 2
