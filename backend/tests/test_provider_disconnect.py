"""Per-user provider disconnect: settings persistence + providers API.

Disconnecting a provider never touches the environment key — it only records
the provider in the user's preferences so routing silently skips it.
"""
from __future__ import annotations

from app.services.model_router import AUTO_PRIORITY
from app.services.provider_state import PROVIDER_NAMES


def _auth_headers(client, email: str = "who@test.com"):
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "supersecret1", "name": "Who",
    })
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_provider_names_include_router_providers_and_workspace_agents():
    assert list(PROVIDER_NAMES[:-2]) == list(AUTO_PRIORITY)
    assert PROVIDER_NAMES[-2:] == ("copilot", "codex")
    assert "poolside" in PROVIDER_NAMES


def test_nvidia_per_user_key_is_accepted_and_never_returned(client):
    headers = _auth_headers(client, "nvidia-key@test.com")
    saved = client.post(
        "/api/settings/provider-key",
        headers=headers,
        json={"provider": "nvidia", "api_key": "nvidia-private-key"},
    )
    assert saved.status_code == 200, saved.text
    assert "nvidia-private-key" not in saved.text
    assert saved.json()["provider"] == "nvidia"

    settings = client.get("/api/settings", headers=headers)
    assert settings.status_code == 200
    assert "nvidia" in settings.json()["provider_keys"]
    keys = client.get("/api/settings/provider-keys", headers=headers)
    assert keys.status_code == 200
    assert any(
        key["provider"] == "nvidia"
        and key["url"] == "https://build.nvidia.com/"
        for key in keys.json()
    )

    deleted = client.delete("/api/settings/provider-key/nvidia", headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert "nvidia" not in client.get(
        "/api/settings", headers=headers
    ).json()["provider_keys"]


def test_poolside_per_user_key_is_accepted_and_never_returned(client):
    headers = _auth_headers(client, "poolside-key@test.com")
    saved = client.post(
        "/api/settings/provider-key",
        headers=headers,
        json={"provider": "poolside", "api_key": "poolside-secret"},
    )
    assert saved.status_code == 200, saved.text
    assert "poolside-secret" not in saved.text
    assert saved.json()["provider"] == "poolside"

    settings = client.get("/api/settings", headers=headers)
    assert settings.status_code == 200
    assert "poolside" in settings.json()["provider_keys"]
    keys = client.get("/api/settings/provider-keys", headers=headers)
    assert keys.status_code == 200
    assert any(key["provider"] == "poolside" for key in keys.json())

    deleted = client.delete("/api/settings/provider-key/poolside", headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert "poolside" not in client.get("/api/settings", headers=headers).json()["provider_keys"]


def test_disconnect_reconnect_round_trip(client):
    headers = _auth_headers(client)
    resp = client.patch("/api/settings", headers=headers, json={
        "disabled_providers": ["groq", "openai"],
    })
    assert resp.status_code == 200, resp.text
    # Persisted in canonical routing-priority order, duplicates dropped.
    assert resp.json()["disabled_providers"] == ["openai", "groq"]

    providers = client.get("/api/providers", headers=headers).json()
    assert providers["disabled_providers"] == ["openai", "groq"]
    assert providers["providers"]["openai"]["disabled"] is True
    assert providers["providers"]["openai"]["usable"] is False
    assert providers["providers"]["groq"]["disabled"] is True
    assert providers["providers"]["ollama"]["disabled"] is False
    assert providers["providers"]["ollama"]["usable"] is True  # keyless

    resp2 = client.patch("/api/settings", headers=headers, json={
        "disabled_providers": [],
    })
    assert resp2.status_code == 200
    assert resp2.json()["disabled_providers"] == []
    after = client.get("/api/providers", headers=headers).json()
    assert after["providers"]["openai"]["disabled"] is False
    assert after["providers"]["openai"]["usable"] is False  # no key in test env


def test_unknown_provider_rejected(client):
    headers = _auth_headers(client, "u2@test.com")
    resp = client.patch("/api/settings", headers=headers, json={
        "disabled_providers": ["wat"],
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "UNKNOWN_PROVIDER"


def test_disabled_providers_are_per_user(client):
    a = _auth_headers(client, "a@test.com")
    b = _auth_headers(client, "b@test.com")
    client.patch("/api/settings", headers=a, json={"disabled_providers": ["openai"]})
    da = client.get("/api/providers", headers=a).json()
    db = client.get("/api/providers", headers=b).json()
    assert "openai" in da["disabled_providers"]
    assert da["providers"]["openai"]["disabled"] is True
    assert "openai" not in db["disabled_providers"]
    assert db["providers"]["openai"]["disabled"] is False