from app.agents.graph import _normalize_app_launch_arguments
from app.services.device_intent import (
    is_common_mobile_app_name,
    parse_app_launch_request,
    resolve_app_launch_target,
)


def test_app_launch_parsing_separates_app_from_follow_up_instructions():
    cases = (
        (
            "Open my WhatsApp and tell me who sent unread messages",
            "WhatsApp",
            "tell me who sent unread messages",
        ),
        (
            "Open Facebook and check my notifications",
            "Facebook",
            "check my notifications",
        ),
        (
            "Open WhatsApp and then find unread messages",
            "WhatsApp",
            "find unread messages",
        ),
        (
            "Open Gmail, then summarise unread emails",
            "Gmail",
            "summarise unread emails",
        ),
        (
            "Open my email and read the newest message",
            "Gmail",
            "read the newest message",
        ),
        ("Open my browser", "Chrome", ""),
    )
    for request, expected_app, expected_follow_up in cases:
        parsed = parse_app_launch_request(request)
        assert parsed is not None
        assert parsed.app_name == expected_app
        assert parsed.follow_up == expected_follow_up


def test_app_launch_resolution_accepts_generic_unlisted_apps():
    parsed = resolve_app_launch_target(
        "my Unfamiliar Calendar and check the next event"
    )

    assert parsed is not None
    assert parsed.app_name == "Unfamiliar Calendar"
    assert parsed.follow_up == "check the next event"
    assert not is_common_mobile_app_name(parsed.app_name)


def test_app_launch_resolution_rejects_non_app_sentences():
    assert resolve_app_launch_target(
        "tell me who sent unread WhatsApp messages"
    ) is None
    assert parse_app_launch_request("Open any app") is None


def test_graph_normalizes_launcher_arguments_before_approval_or_execution():
    normalized, error = _normalize_app_launch_arguments(
        "open_paired_android_app",
        {
            "app_name": "my WhatsApp and tell me who sent unread messages",
        },
        "Open my WhatsApp and tell me who sent unread messages",
    )

    assert error is None
    assert normalized == {
        "app_name": "WhatsApp",
        "follow_up_request": "tell me who sent unread messages",
    }


def test_graph_rejects_unparsed_launcher_arguments():
    normalized, error = _normalize_app_launch_arguments(
        "open_paired_android_app",
        {"app_name": "who sent my WhatsApp messages"},
        "Open my WhatsApp and tell me who sent unread messages",
    )

    assert normalized["app_name"] == "who sent my WhatsApp messages"
    assert error is not None
    assert error.error is not None
    assert error.error.code == "INVALID_APP_NAME"
