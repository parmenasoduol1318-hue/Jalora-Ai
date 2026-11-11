from hashlib import sha256
from urllib.parse import parse_qs, urlsplit

from app.database.session import SessionLocal
from app.models.device import DeviceConnection


def _pair_request(client, headers, name="My phone"):
    return client.post(
        "/api/devices/pair-requests",
        headers=headers,
        json={
            "name": name,
            "platform": "android",
            "scopes": ["remote.browser.open_url"],
            "consent_confirmed": True,
        },
    )


def test_target_must_approve_pair_request_before_device_is_created(client, auth_headers):
    owner_headers = auth_headers("remote-pair-owner@test.com")
    response = _pair_request(client, owner_headers)

    assert response.status_code == 200, response.text
    pair = response.json()
    code = parse_qs(urlsplit(pair["pairing_url"]).fragment)["pair_code"][0]
    request_id = parse_qs(urlsplit(pair["pairing_url"]).fragment)["pair_request_id"][0]
    assert pair["status"] == "pending"

    pending = client.get(
        f"/api/devices/pair-requests/{request_id}", headers=owner_headers
    )
    assert pending.status_code == 200
    assert pending.json()["scopes"] == ["remote.browser.open_url"]

    other_user = auth_headers("remote-pair-other@test.com")
    assert client.get(
        f"/api/devices/pair-requests/{request_id}", headers=other_user
    ).status_code == 404
    assert client.get("/api/devices", headers=owner_headers).json() == []

    target_lookup = client.post(
        f"/api/devices/pair-requests/{request_id}/target",
        json={"pairing_code": code},
    )
    assert target_lookup.status_code == 200, target_lookup.text
    assert target_lookup.json()["name"] == "My phone"
    approved = client.post(
        f"/api/devices/pair-requests/{request_id}/approve",
        json={"confirm": True, "pairing_code": code},
    )
    assert approved.status_code == 200, approved.text
    data = approved.json()
    token = data["device_token"]
    with SessionLocal() as db:
        persisted = db.get(DeviceConnection, data["device"]["id"])
        assert persisted is not None
        assert persisted.token_hash == sha256(token.encode()).hexdigest()
    assert data["device"]["status"] == "active"
    assert data["device"]["scopes"] == ["remote.browser.open_url"]
    assert data["pair_request"]["status"] == "approved"
    assert data["pair_request"]["approved_device_id"] == data["device"]["id"]
    assert sha256(token.encode()).hexdigest() != token
    assert client.post(
        f"/api/devices/pair-requests/{request_id}/approve",
        json={"confirm": True, "pairing_code": code},
    ).status_code == 409


def test_remote_url_action_requires_target_approval_and_reports_open_handoff(
    client, auth_headers
):
    owner_headers = auth_headers("remote-url-owner@test.com")
    pair = _pair_request(client, owner_headers, name="Music phone").json()
    code = parse_qs(urlsplit(pair["pairing_url"]).fragment)["pair_code"][0]
    request_id = parse_qs(urlsplit(pair["pairing_url"]).fragment)["pair_request_id"][0]
    approved_pair = client.post(
        f"/api/devices/pair-requests/{request_id}/approve",
        json={"confirm": True, "pairing_code": code},
    )
    assert approved_pair.status_code == 200
    device = approved_pair.json()["device"]
    device_headers = {
        "Authorization": f"Bearer {approved_pair.json()['device_token']}"
    }

    not_confirmed = client.post(
        f"/api/devices/{device['id']}/remote-commands",
        headers=owner_headers,
        json={"url": "https://www.youtube.com/results?search_query=Unanijua", "confirm": False},
    )
    assert not_confirmed.status_code == 400

    created = client.post(
        f"/api/devices/{device['id']}/remote-commands",
        headers=owner_headers,
        json={"url": "https://www.youtube.com/results?search_query=Unanijua", "confirm": True},
    )
    assert created.status_code == 202, created.text
    pending = created.json()
    assert pending["status"] == "paused_approval"
    approved = client.post(
        f"/api/approvals/{pending['approval']['id']}/approve",
        headers=owner_headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "completed"
    command = client.get(
        "/api/devices/remote-command-history", headers=owner_headers
    ).json()[0]
    assert command["status"] == "pending"

    target_queue = client.get(
        "/api/devices/remote-commands/target", headers=device_headers
    )
    assert [row["id"] for row in target_queue.json()] == [command["id"]]

    decision = client.post(
        f"/api/devices/remote-commands/{command['id']}/decision",
        headers=device_headers,
        json={"approve": True},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "approved"
    destination = urlsplit(decision.json()["url"])
    assert destination.hostname == "www.youtube.com"
    assert parse_qs(destination.query)["search_query"] == ["Unanijua"]

    opened = client.post(
        f"/api/devices/remote-commands/{command['id']}/opened",
        headers=device_headers,
        json={"opened": True},
    )
    assert opened.status_code == 200
    assert opened.json()["status"] == "opened"
    assert client.post(
        f"/api/devices/remote-commands/{command['id']}/decision",
        headers=device_headers,
        json={"approve": True},
    ).status_code == 409
    assert client.post(
        f"/api/devices/remote-commands/{command['id']}/opened",
        headers=device_headers,
        json={"opened": True},
    ).status_code == 409
    history = client.get(
        "/api/devices/remote-command-history", headers=owner_headers
    )
    assert history.json()[0]["status"] == "opened"
    revoked = client.delete(f"/api/devices/{device['id']}", headers=owner_headers)
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert client.get(
        "/api/devices/remote-commands/target", headers=device_headers
    ).status_code == 401


def test_device_pairing_request_requires_user_consent(client, auth_headers):
    headers = auth_headers("remote-pair-consent@test.com")
    response = client.post(
        "/api/devices/pair-requests",
        headers=headers,
        json={
            "name": "My tablet",
            "platform": "android",
            "scopes": ["remote.browser.open_url"],
            "consent_confirmed": False,
        },
    )
    assert response.status_code == 400


def test_current_ios_browser_can_be_registered_as_a_paired_device(client, auth_headers):
    headers = auth_headers("remote-pair-ios@test.com")
    response = client.post(
        "/api/devices/pair-requests",
        headers=headers,
        json={
            "name": "Current iOS device",
            "platform": "ios",
            "scopes": ["remote.browser.open_url"],
            "consent_confirmed": True,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["platform"] == "ios"
    assert response.json()["status"] == "pending"


def test_target_pairing_code_is_required_and_target_need_not_sign_in(
    client, auth_headers
):
    owner_headers = auth_headers("remote-pair-code-owner@test.com")
    pair = _pair_request(client, owner_headers).json()
    request_id = parse_qs(urlsplit(pair["pairing_url"]).fragment)["pair_request_id"][0]
    code = parse_qs(urlsplit(pair["pairing_url"]).fragment)["pair_code"][0]

    denied = client.post(
        f"/api/devices/pair-requests/{request_id}/target",
        json={"pairing_code": "wrong-code-that-is-long-enough"},
    )
    assert denied.status_code == 403

    rejected = client.post(
        f"/api/devices/pair-requests/{request_id}/reject",
        json={"pairing_code": code},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_remote_url_command_rejects_private_destinations(client, auth_headers):
    owner_headers = auth_headers("remote-url-private-owner@test.com")
    pair = _pair_request(client, owner_headers).json()
    code = parse_qs(urlsplit(pair["pairing_url"]).fragment)["pair_code"][0]
    request_id = parse_qs(urlsplit(pair["pairing_url"]).fragment)["pair_request_id"][0]
    device = client.post(
        f"/api/devices/pair-requests/{request_id}/approve",
        json={"confirm": True, "pairing_code": code},
    ).json()["device"]

    response = client.post(
        f"/api/devices/{device['id']}/remote-commands",
        headers=owner_headers,
        json={"url": "http://127.0.0.1/admin", "confirm": True},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REMOTE_URL"


def test_legacy_device_creation_cannot_skip_target_remote_approval(client, auth_headers):
    headers = auth_headers("remote-legacy-create@test.com")
    response = client.post(
        "/api/devices",
        headers=headers,
        json={
            "name": "Unapproved phone",
            "platform": "android",
            "scopes": ["remote.browser.open_url"],
            "consent_confirmed": True,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TARGET_DEVICE_APPROVAL_REQUIRED"


def test_remote_device_command_tool_is_confirmation_gated_and_routed():
    from app.tools import device_tools
    from app.tools.base import PermissionLevel
    from app.tools.routing import route_request

    spec = next(
        item for item in device_tools.registry.all()
        if item.name == "request_remote_url"
    )
    assert spec.permission == PermissionLevel.CONFIRM

    routing = route_request(
        "On my phone, play Unanijua by Bahati on YouTube."
    )
    assert "remote_device_control" in routing.signals
    assert "request_remote_url" in (routing.tool_names or [])
    assert not {"search_hotels", "book_hotel"} & set(routing.tool_names or [])
