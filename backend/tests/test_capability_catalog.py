from pathlib import Path

from app.services.capability_catalog import capability_catalog
from app.tools import registry


def test_catalog_is_authenticated_and_discloses_live_provider_boundary(
    client, auth_headers,
):
    assert client.get("/api/capabilities").status_code == 401

    response = client.get("/api/capabilities", headers=auth_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert "do not establish live provider availability" in body["outcome_notice"]
    assert "shared registry and policy" in body["execution"]
    assert body["capabilities"]


def test_each_catalog_scope_maps_to_registered_tools_and_existing_tests():
    cards = capability_catalog()
    backend = Path(__file__).parents[1]
    ids = {card["id"] for card in cards}
    assert {
        "web_research",
        "email",
        "messaging_and_calls",
        "contacts",
        "files_and_documents",
        "calendar_and_reminders",
        "device_media",
        "travel",
        "payments",
        "coding_workspace",
    } <= ids

    for card in cards:
        assert card["status"] == "registered"
        assert card["live_provider_verified"] is False
        assert card["limitations"]
        assert card["tests"]
        assert all((backend / test_path).is_file() for test_path in card["tests"])
        assert card["tools"]
        for operation in card["tools"]:
            registered = registry.get(operation["name"])
            assert registered is not None
            assert operation["input_fields"] == sorted(
                registered.tool.args_schema.model_json_schema()
                .get("properties", {})
            )
            assert operation["permission_level"] == int(registered.permission)
            assert operation["side_effect"] == registered.side_effect
            assert operation["approval_required_by_default"] == (
                int(registered.permission) >= 3
            )


def test_external_actions_are_not_listed_as_approval_free():
    cards = capability_catalog()
    external_tools = {
        operation["name"]: operation
        for card in cards
        for operation in card["tools"]
        if operation["name"] in {
            "send_email",
            "send_sms",
            "send_whatsapp",
            "place_voice_call",
            "create_event",
            "delete_event",
            "send_payment",
            "book_flight",
            "book_reservation",
            "delete_document",
        }
    }

    assert external_tools
    assert all(
        operation["approval_required_by_default"]
        for operation in external_tools.values()
    )
