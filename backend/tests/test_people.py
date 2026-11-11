from app.services.search_service import SearchResult


def test_profile_is_private_until_opted_in_and_contact_is_separately_shared(
    client, auth_headers
):
    headers = auth_headers("member@test.com")

    profile = client.get("/api/people/profile", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["discoverable"] is False
    assert profile.json()["skills"] == []

    saved = client.put(
        "/api/people/profile",
        headers=headers,
        json={
            "courses": ["Computer Science"],
            "qualifications": ["BSc"],
            "skills": ["Python", "Data analysis"],
            "phone": "+254700000000",
            "discoverable": True,
            "share_email": True,
            "share_phone": False,
        },
    )
    assert saved.status_code == 200

    matches = client.get(
        "/api/people/search?query=python", headers=headers
    ).json()["results"]
    assert len(matches) == 1
    assert matches[0]["name"] == "Test User"
    assert matches[0]["email"] == "member@test.com"
    assert matches[0]["phone"] == ""
    assert matches[0]["skills"] == ["Python", "Data analysis"]

    hidden = client.put(
        "/api/people/profile",
        headers=headers,
        json={
            "skills": ["Python"],
            "discoverable": False,
            "share_email": True,
            "share_phone": True,
            "phone": "+254700000000",
        },
    )
    assert hidden.status_code == 200
    assert hidden.json()["share_email"] is False
    assert hidden.json()["share_phone"] is False
    assert client.get(
        "/api/people/search?query=python", headers=headers
    ).json()["results"] == []


def test_public_profile_search_only_returns_allowed_profile_hosts(
    client, auth_headers, monkeypatch
):
    headers = auth_headers("searcher@test.com")

    async def fake_search(query: str, max_results: int):
        return [
            SearchResult("LinkedIn profile", "https://www.linkedin.com/in/example", "Profile"),
            SearchResult("GitHub profile", "https://github.com/example", "Projects"),
            SearchResult("Lookalike host", "https://linkedin.com.example.org/in/fake", "Fake"),
            SearchResult("Unsafe protocol", "javascript://github.com/in/fake", "Fake"),
        ]

    monkeypatch.setattr("app.api.people.search_service.search", fake_search)
    response = client.get(
        "/api/people/search/external?skill=Python", headers=headers
    )

    assert response.status_code == 200
    result = response.json()
    assert [row["source"] for row in result["results"]] == [
        "linkedin.com",
        "github.com",
    ]
    assert "not been independently verified" in result["notice"]
