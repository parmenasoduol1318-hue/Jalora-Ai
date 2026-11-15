import asyncio

from app.agents.graph import _extract_sources
from app.tools import scientific_sources
from app.tools.base import registry
from app.tools.routing import tool_names_for_intent


class _Response:
    def __init__(self, payload, status_error=None):
        self.payload = payload
        self.status_error = status_error

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error

    def json(self):
        return self.payload


class _Client:
    def __init__(self, response):
        self.response = response
        self.request = None

    async def get(self, url, **kwargs):
        self.request = (url, kwargs)
        return self.response


def test_search_scientific_sources_returns_citable_metadata(monkeypatch):
    client = _Client(_Response({
        "message": {
            "items": [{
                "DOI": "10.1234/example.1",
                "title": ["Climate impacts on coastal ecosystems"],
                "author": [{"given": "A.", "family": "Researcher"}],
                "published": {"date-parts": [[2024, 3, 5]]},
                "container-title": ["Journal of Example Science"],
                "abstract": "<jats:p>Evidence from field studies.</jats:p>",
                "type": "journal-article",
                "URL": "https://api.crossref.org/works/10.1234/example.1",
            }]
        }
    }))
    monkeypatch.setattr(
        scientific_sources, "get_async_client", lambda **_: client
    )

    result = asyncio.run(
        scientific_sources.search_scientific_sources("  coastal   climate  ")
    )

    assert result.success
    assert result.data["query"] == "coastal climate"
    assert result.data["untrusted"] is True
    assert result.data["results"] == [{
        "title": "Climate impacts on coastal ecosystems",
        "url": "https://doi.org/10.1234/example.1",
        "doi": "10.1234/example.1",
        "authors": ["A. Researcher"],
        "year": 2024,
        "publication": "Journal of Example Science",
        "abstract": "Evidence from field studies.",
        "record_type": "journal-article",
        "index": "Crossref",
        "peer_reviewed": None,
    }]
    assert client.request[1]["params"]["rows"] == 15


def test_scientific_search_filters_off_topic_records(monkeypatch):
    client = _Client(_Response({
        "message": {
            "items": [
                {
                    "DOI": "10.1234/mombasa.1",
                    "title": ["High temperatures and climate in Mombasa"],
                    "abstract": "Measurements describe coastal heat.",
                    "type": "journal-article",
                },
                {
                    "DOI": "10.1234/unrelated.1",
                    "title": ["Climate change litigation turns up the heat"],
                    "abstract": "A discussion of corporate litigation.",
                    "type": "journal-article",
                },
                {
                    "DOI": "10.1234/other-place.1",
                    "title": ["Southern Ocean takes on the heat of climate change"],
                    "abstract": "Ocean temperature trends.",
                    "type": "journal-article",
                },
                {
                    "DOI": "10.1234/high-performance.1",
                    "title": [
                        "High performance organizational framework at the Kenyan Coast"
                    ],
                    "abstract": "Institutional outcomes in Mombasa.",
                    "type": "journal-article",
                },
                {
                    "DOI": "10.1234/high-schools.1",
                    "title": [
                        "Entry marks in high and low performing schools in Mombasa"
                    ],
                    "abstract": "School examination results.",
                    "type": "journal-article",
                },
            ]
        }
    }))
    monkeypatch.setattr(
        scientific_sources, "get_async_client", lambda **_: client
    )

    result = asyncio.run(scientific_sources.search_scientific_sources(
        "First, why is Mombasa temperatures very high?"
    ))

    assert result.success
    assert result.data["query"] == "Mombasa temperatures high"
    assert [row["doi"] for row in result.data["results"]] == [
        "10.1234/mombasa.1"
    ]
    assert client.request[1]["params"]["query.bibliographic"] == (
        "Mombasa temperatures high"
    )


def test_scientific_search_fails_closed_for_empty_query(monkeypatch):
    monkeypatch.setattr(
        scientific_sources,
        "get_async_client",
        lambda **_: (_ for _ in ()).throw(AssertionError("must not request")),
    )

    result = asyncio.run(scientific_sources.search_scientific_sources("  "))

    assert not result.success
    assert result.error.code == "INVALID_QUERY"


def test_scientific_tool_is_routable_and_contributes_research_sources():
    assert registry.get("search_scientific_sources") is not None
    assert "search_scientific_sources" in registry.select_for_task(
        "Find scholarly scientific papers about climate change impacts."
    )
    assert {
        "search_scientific_sources",
        "open_url",
    } <= set(tool_names_for_intent(
        "research",
        "Find scholarly scientific papers about climate change impacts.",
    ))

    sources = _extract_sources("search_scientific_sources", {
        "results": [{
            "title": "Climate impacts on coastal ecosystems",
            "url": "https://doi.org/10.1234/example.1",
            "abstract": "Evidence from field studies.",
        }]
    })
    assert sources == [{
        "title": "Climate impacts on coastal ecosystems",
        "url": "https://doi.org/10.1234/example.1",
        "snippet": "Evidence from field studies.",
        "source_type": "search_result",
    }]
