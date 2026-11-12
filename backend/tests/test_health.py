def test_health_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["database"] == "ok"
    assert data["env"] == "test"


def test_root_explains_backend_endpoints_when_frontend_is_not_bundled(
    monkeypatch, tmp_path
):
    from fastapi.testclient import TestClient

    from app import main

    monkeypatch.setattr(main, "REACT_DIST", tmp_path / "missing-frontend")
    with TestClient(main.create_app()) as test_client:
        response = test_client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "service": "Jalora AI",
        "status": "running",
        "health": "/api/health",
        "api_docs": "/api/docs",
    }


def test_health_returns_service_unavailable_when_database_probe_fails(client, monkeypatch):
    from app.api.system import get_db

    class _UnavailableDatabase:
        def execute(self, _query):
            raise OSError("database unavailable")

    def unavailable_db():
        yield _UnavailableDatabase()

    monkeypatch.setitem(client.app.dependency_overrides, get_db, unavailable_db)

    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "error: OSError"


def test_providers_require_auth(client):
    resp = client.get("/api/providers")
    assert resp.status_code == 401


def test_providers_never_leak_secrets(client, auth_headers):
    resp = client.get("/api/providers", headers=auth_headers())
    assert resp.status_code == 200
    body = resp.text
    assert "api_key" not in body.lower() or '"api_key": null' not in body.lower()
    data = resp.json()
    providers = data["providers"]
    assert data["disabled_providers"] == []
    for name, info in providers.items():
        assert set(info.keys()) == {"configured", "model", "keyless", "disabled", "usable", "logo"}
        for key in ("configured", "keyless", "disabled", "usable"):
            assert isinstance(info[key], bool)
        assert info["usable"] == (info["configured"] and not info["disabled"])


def test_tools_manifest_lists_permission_levels(client):
    resp = client.get("/api/tools")
    assert resp.status_code == 200
    tools = resp.json()["tools"]
    names = {t["name"] for t in tools}
    assert {"search_web", "open_url", "store_memory", "delete_memory", "read_document"} <= names
    by_name = {t["name"]: t for t in tools}
    assert by_name["search_web"]["permission"] == 1
    assert by_name["store_memory"]["permission"] == 2
    assert by_name["delete_memory"]["permission"] == 3
