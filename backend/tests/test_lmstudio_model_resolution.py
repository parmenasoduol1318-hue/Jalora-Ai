from __future__ import annotations

from app.config import Settings
from app.services import provider_extensions
from app.services.model_router import ModelRouter


def _router() -> ModelRouter:
    return ModelRouter(
        Settings(
            _env_file=None,
            default_provider="lmstudio",
            lmstudio_base_url="http://lmstudio/v1",
            lmstudio_model="stale-model",
        )
    )


def test_lmstudio_routes_to_loaded_chat_model(monkeypatch):
    monkeypatch.setattr(
        provider_extensions,
        "_loaded_lmstudio_models",
        lambda spec: [("qwen/qwen3-4b", {"qwen/qwen3-4b"})],
    )

    route = _router().route(message="research a topic")

    assert route.provider.name == "lmstudio"
    assert route.model == "qwen/qwen3-4b"


def test_lmstudio_keeps_configured_model_when_it_is_loaded(monkeypatch):
    monkeypatch.setattr(
        provider_extensions,
        "_loaded_lmstudio_models",
        lambda spec: [("stale-model", {"stale-model"})],
    )

    route = _router().route(message="research a topic")

    assert route.model == "stale-model"


def test_explicit_lmstudio_model_override_is_not_replaced(monkeypatch):
    monkeypatch.setattr(
        provider_extensions,
        "_loaded_lmstudio_models",
        lambda spec: [("qwen/qwen3-4b", {"qwen/qwen3-4b"})],
    )

    route = _router().route(
        message="research a topic",
        model_override="user-selected-model",
    )

    assert route.model == "user-selected-model"


def test_lmstudio_keeps_configured_model_when_discovery_finds_no_loaded_chat_model(
    monkeypatch,
):
    monkeypatch.setattr(provider_extensions, "_loaded_lmstudio_models", lambda spec: [])

    route = _router().route(message="research a topic")

    assert route.model == "stale-model"


def test_lmstudio_route_chain_uses_loaded_chat_model(monkeypatch):
    monkeypatch.setattr(
        provider_extensions,
        "_loaded_lmstudio_models",
        lambda spec: [("qwen/qwen3-4b", {"qwen/qwen3-4b"})],
    )

    routes = _router().route_chain(message="research a topic")

    lmstudio_route = next(
        route for route in routes if route.provider.name == "lmstudio"
    )
    assert lmstudio_route.model == "qwen/qwen3-4b"


def test_jan_without_a_base_url_is_not_a_fallback_provider():
    routes = _router().route_chain(message="research a topic")

    assert "jan" not in {route.provider.name for route in routes}


def test_jan_is_configured_when_a_base_url_is_provided():
    router = ModelRouter(
        Settings(
            _env_file=None,
            default_provider="auto",
            jan_base_url="http://jan/v1",
        )
    )

    assert router.is_configured("jan")


def test_lmstudio_discovery_uses_only_loaded_language_models(monkeypatch):
    requested = {}

    class Response:
        def raise_for_status(self):
            return None

        @staticmethod
        def json():
            return {
                "models": [
                    {
                        "type": "embedding",
                        "key": "text-embedding-model",
                        "loaded_instances": [{"id": "text-embedding-model"}],
                    },
                    {
                        "type": "llm",
                        "key": "downloaded-only",
                        "loaded_instances": [],
                    },
                    {
                        "type": "llm",
                        "key": "loaded-chat-model",
                        "loaded_instances": [{"id": "loaded-chat-model"}],
                    },
                ],
            }

    def fake_get(url, *, timeout):
        requested["url"] = url
        requested["timeout"] = timeout
        return Response()

    monkeypatch.setattr(provider_extensions.httpx, "get", fake_get)

    models = provider_extensions._loaded_lmstudio_models(_router().specs["lmstudio"])

    assert models == [("loaded-chat-model", {"loaded-chat-model"})]
    assert requested == {
        "url": "http://lmstudio/api/v1/models",
        "timeout": 2.0,
    }
