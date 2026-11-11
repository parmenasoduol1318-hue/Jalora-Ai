"""Tool system guarantees: structured results, honest failures, no raw exceptions."""
import asyncio
import json

from app.context import ToolContext, set_tool_context
from app.tools import ToolResult, registry
from app.tools.user_input import UserInputArguments


def _parse(raw: str) -> dict:
    return json.loads(raw)


def test_registry_has_no_duplicate_and_manifest_is_public():
    names = [t.name for t in registry.all()]
    assert len(names) == len(set(names))
    manifest = registry.manifest()
    assert all({
        "name", "category", "permission", "description", "requires_internet",
        "tags", "input_schema", "output_schema", "side_effect", "preconditions",
        "idempotent", "retryable", "verification_tool", "compensation_tool",
        "required_signals", "default_for_actions", "always_available",
    } == set(entry) for entry in manifest)
    search = next(entry for entry in manifest if entry["name"] == "search_web")
    assert "query" in search["input_schema"]["properties"]
    assert "success" in search["output_schema"]["properties"]
    assert search["side_effect"] == "read_only"


def test_registry_manifest_uses_declared_capability_metadata():
    from app.tools.base import PermissionLevel, ToolRegistry

    isolated = ToolRegistry()

    def inspect_record(record_id: str) -> ToolResult:
        """Read one record."""
        return ToolResult.ok("Read.")

    isolated.register(
        inspect_record,
        permission=PermissionLevel.AUTO,
        category="test",
        side_effect="read_only",
        preconditions=["record_id is available"],
        idempotent=True,
        retryable=True,
        verification_tool="verify_record",
        compensation_tool=None,
    )
    item = isolated.manifest()[0]
    assert item["side_effect"] == "read_only"
    assert item["preconditions"] == ["record_id is available"]
    assert item["idempotent"] is True
    assert item["retryable"] is True
    assert item["verification_tool"] == "verify_record"
    assert item["compensation_tool"] is None
    assert item["input_schema"]["required"] == ["record_id"]


def test_open_url_invalid_url_is_structured_failure():
    spec = registry.get("open_url")
    result = _parse(asyncio.run(spec.tool.ainvoke({"url": "ftp://example.com"})))
    assert result["success"] is False
    assert result["error"]["code"] in ("INVALID_URL", "BLOCKED_SCHEME")
    assert "ftp" in result["error"]["details"]


def test_open_url_unreachable_host_is_structured_failure():
    spec = registry.get("open_url")
    result = _parse(asyncio.run(spec.tool.ainvoke(
        {"url": "http://nonexistent.invalid.example/"})))
    assert result["success"] is False
    assert result["error"]["code"] in ("NETWORK_ERROR", "HTTP_ERROR", "TIMEOUT")
    assert "Traceback" not in json.dumps(result)


def test_current_datetime_succeeds():
    spec = registry.get("current_datetime")
    result = _parse(asyncio.run(spec.tool.ainvoke({})))
    assert result["success"] is True
    assert "Current UTC time" in result["message"]


def test_unknown_tool_call_is_not_registered():
    assert registry.get("definitely_not_a_tool") is None
    # comms tools are real (P7) and always approval-gated, never silent:
    send_sms = registry.get("send_sms")
    assert send_sms is not None and send_sms.permission == 3


def test_structured_user_input_validates_choice_answers():
    prompt = UserInputArguments(
        title="Outreach style",
        question="Which email approach should I use?",
        input_type="choice",
        options=["Shared", "Tailored"],
        answer="Tailored",
    )
    assert prompt.answer == "Tailored"

    try:
        UserInputArguments(
            title="Outreach style",
            question="Which email approach should I use?",
            input_type="choice",
            options=["Shared", "Tailored"],
            answer="Something else",
        )
    except ValueError as exc:
        assert "Choose one" in str(exc)
    else:
        raise AssertionError("Invalid choice should not be accepted.")


def test_tool_without_context_fails_structurally():
    set_tool_context(None)
    spec = registry.get("search_memory")
    try:
        # context missing -> wrapper converts exception into structured failure
        result = _parse(asyncio.run(spec.tool.ainvoke({"query": "x"})))
        assert result["success"] is False
        assert result["error"]["code"] == "TOOL_EXCEPTION"
    finally:
        set_tool_context(None)
