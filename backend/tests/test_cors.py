def test_cors_preflight_allows_configured_origin(client):
    resp = client.options("/api/chat", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    })
    assert resp.status_code in (200, 204)
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "POST" in resp.headers.get("access-control-allow-methods", "")


def test_cors_rejects_unknown_origin(client):
    resp = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert resp.headers.get("access-control-allow-origin") != "http://evil.example"


def test_frontend_is_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Jalora" in resp.text


def test_api_404_is_structured(client):
    resp = client.get("/api/nope")
    assert resp.status_code == 404
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_FOUND"
