from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.context import ToolContext, set_tool_context
from app.services.creative import providers
from app.services.creative.providers import GenOutput
from app.tools import creative_tools


class _FakeProvider:
    name = "test-image-provider"
    ai = True

    def __init__(self, *, ai_output: bool) -> None:
        self.ai_output = ai_output

    def configured(self) -> bool:
        return True

    def generate(self, request) -> list[GenOutput]:
        return [
            GenOutput(
                data=b"generated-image",
                extension=".png",
                mime="image/png",
                meta={"ai": self.ai_output},
            )
        ]


def _configure_tool(monkeypatch, provider: _FakeProvider) -> None:
    monkeypatch.setattr(creative_tools, "_creative_disabled", lambda: None)
    settings = SimpleNamespace(image_provider="auto")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr(
        providers, "_image_providers", lambda _settings: [provider]
    )


@pytest.mark.asyncio
async def test_generate_image_returns_artifact_for_verified_ai_output(monkeypatch):
    from app.api.creative import _helpers
    from app.database import session

    _configure_tool(monkeypatch, _FakeProvider(ai_output=True))

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(session, "SessionLocal", FakeSession)
    monkeypatch.setattr(
        _helpers,
        "persist_bytes",
        lambda db, user_id, data, filename, **kwargs: SimpleNamespace(
            id="generated-asset", size_bytes=len(data)
        ),
    )
    monkeypatch.setattr(
        creative_tools,
        "_artifact_info",
        lambda asset, *, out: {
            "id": asset.id,
            "kind": "image",
            "url": "/api/creative/assets/generated-asset",
        },
    )
    set_tool_context(ToolContext(user_id="test-user"))
    try:
        result = await creative_tools.generate_image(
            prompt="A cup on a table"
        )
    finally:
        set_tool_context(None)

    assert result.success
    assert result.data["ai"] is True
    assert result.data["artifact"]["kind"] == "image"
    assert result.data["artifact"]["url"]


@pytest.mark.asyncio
async def test_generate_image_does_not_report_procedural_output_as_success(monkeypatch):
    from app.api.creative import _helpers
    from app.database import session

    _configure_tool(monkeypatch, _FakeProvider(ai_output=False))
    monkeypatch.setattr(
        session,
        "SessionLocal",
        lambda: pytest.fail("procedural output must not be persisted as success"),
    )
    monkeypatch.setattr(
        _helpers,
        "persist_bytes",
        lambda *args, **kwargs: pytest.fail(
            "procedural output must not be persisted as success"
        ),
    )
    set_tool_context(ToolContext(user_id="test-user"))
    try:
        result = await creative_tools.generate_image(
            prompt="A cup on a table"
        )
    finally:
        set_tool_context(None)

    assert not result.success
    assert result.error is not None
    assert result.error.code == "AI_IMAGE_GENERATION_UNAVAILABLE"
    assert not result.data.get("artifact")


def test_auto_image_generation_fails_over_to_another_ai_provider(monkeypatch):
    from app.services.creative import CreativeEngineError
    from app.services.creative.providers import GenRequest, generate_ai_image

    class FailingProvider(_FakeProvider):
        name = "openai"

        def generate(self, request):
            raise CreativeEngineError(
                "provider unavailable", "PROVIDER_QUOTA", provider=self.name
            )

    class AvailableProvider(_FakeProvider):
        name = "google"

    openai = FailingProvider(ai_output=True)
    google = AvailableProvider(ai_output=True)
    monkeypatch.setattr(
        providers, "_image_providers", lambda _settings: [openai, google]
    )
    settings = SimpleNamespace(image_provider="auto")

    outputs, selected, fallback = generate_ai_image(
        GenRequest(kind="image", prompt="A cup on a table"),
        "auto",
        settings,
    )

    assert selected is google
    assert outputs[0].meta["ai"] is True
    assert fallback == {
        "from": "openai",
        "reason": "PROVIDER_QUOTA",
        "to": "google",
    }


def test_image_generation_rejects_procedural_provider_output(monkeypatch):
    from app.services.creative import CreativeEngineError
    from app.services.creative.providers import GenRequest, generate_ai_image

    local = _FakeProvider(ai_output=False)
    local.name = "local"
    monkeypatch.setattr(
        providers, "_image_providers", lambda _settings: [local]
    )
    settings = SimpleNamespace(image_provider="local")

    with pytest.raises(CreativeEngineError) as exc_info:
        generate_ai_image(
            GenRequest(kind="image", prompt="A cup on a table"),
            "local",
            settings,
        )

    assert exc_info.value.code == "AI_IMAGE_GENERATION_UNAVAILABLE"


def test_ai_image_generation_skips_local_procedural_fallback(monkeypatch):
    from app.services.creative import CreativeEngineError
    from app.services.creative.providers import (
        GenRequest,
        LocalImageProvider,
        generate_ai_image,
    )

    local = LocalImageProvider()
    monkeypatch.setattr(providers, "_image_providers", lambda _settings: [local])
    monkeypatch.setattr(local, "_ai_generate", lambda _request: None)
    monkeypatch.setattr(
        local,
        "_procedural_generate",
        lambda _request: pytest.fail(
            "AI-only generation must not treat procedural art as a scene image"
        ),
    )
    settings = SimpleNamespace(image_provider="local")

    with pytest.raises(CreativeEngineError) as exc_info:
        generate_ai_image(
            GenRequest(kind="image", prompt="A cup on a table"),
            "local",
            settings,
        )

    assert exc_info.value.code == "AI_IMAGE_GENERATION_UNAVAILABLE"


def test_creative_api_reports_unavailable_ai_provider_without_success(monkeypatch):
    from app.api.creative.generate import _sync_generate
    from app.errors import ServiceUnavailableError
    from app.services.creative import CreativeEngineError

    def no_ai_output(*args, **kwargs):
        raise CreativeEngineError(
            "No AI image provider is available.",
            "AI_IMAGE_GENERATION_UNAVAILABLE",
            provider="local",
        )

    monkeypatch.setattr(providers, "generate_ai_image", no_ai_output)
    request = SimpleNamespace(
        prompt="A cup on a table",
        negative_prompt=None,
        aspect_ratio="1:1",
        seed=None,
        num_variations=1,
        duration=None,
        voice=None,
        model=None,
    )

    with pytest.raises(ServiceUnavailableError) as exc_info:
        _sync_generate(
            "image",
            request,
            db=object(),
            user=SimpleNamespace(id="test-user"),
            settings=SimpleNamespace(),
            project_id=None,
        )

    assert exc_info.value.code == "AI_IMAGE_GENERATION_UNAVAILABLE"
