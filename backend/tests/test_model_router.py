"""Model router selection: override, pinning, complexity, vision (spec §4)."""
from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.config import Settings
from app.errors import BadRequestError, ModelNotConfiguredError
from app.services import model_router as mr
from app.services import ollama_memory
from app.services.model_router import ModelRouter, estimate_complexity

_PROVIDER_KEY_ENV = (
    "JALORA_OPENAI_API_KEY", "JALORA_ANTHROPIC_API_KEY", "JALORA_GOOGLE_API_KEY",
    "JALORA_GROQ_API_KEY", "JALORA_DEEPSEEK_API_KEY", "JALORA_MISTRAL_API_KEY",
    "JALORA_OPENROUTER_API_KEY", "JALORA_TAVILY_API_KEY", "JALORA_DEFAULT_PROVIDER",
    "JALORA_POOLSIDE_API_KEY",
)

# ``app.services.provider_extensions`` (imported via ``app.main`` in conftest)
# prepends the local providers LM Studio and Jan ahead of Ollama in
# AUTO_PRIORITY, so the primary keyless fallback is now LM Studio with Ollama as
# the secondary local provider.
LOCAL_FALLBACK = "ollama"
LOCAL_KEYLESS = ("lmstudio", "jan", "ollama")


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch):
    """Tests must not see keys from the developer's shell or jalora/.env."""
    for name in _PROVIDER_KEY_ENV:
        monkeypatch.delenv(name, raising=False)


def _settings(**overrides) -> Settings:
    base: dict = {name.removeprefix("JALORA_").lower(): None
                  for name in _PROVIDER_KEY_ENV
                  if name != "JALORA_DEFAULT_PROVIDER"}
    base["default_provider"] = "auto"
    base.update(overrides)
    return Settings(_env_file=None, **base)


# ---------------------------------------------------------------------------


def test_unknown_provider_override_rejected():
    router = ModelRouter(_settings())
    with pytest.raises(BadRequestError):
        router.route(provider_override="nope")


def test_unconfigured_provider_override_falls_back():
    # A soft preference for a provider that is not configured must not fail
    # the turn: route silently to the next available provider (local, keyless).
    router = ModelRouter(_settings())
    route = router.route(provider_override="openai")
    assert route.provider.name == LOCAL_FALLBACK


def test_provider_override_selects_spec():
    router = ModelRouter(_settings(openai_api_key="sk-test"))
    route = router.route(provider_override="openai")
    assert route.provider.name == "openai"
    assert route.reason == "user override"


def test_poolside_provider_uses_official_openai_compatible_endpoint():
    router = ModelRouter(_settings(poolside_api_key="poolside-test-key"))
    route = router.route(provider_override="poolside")
    assert route.provider.name == "poolside"
    assert route.provider.base_url == "https://inference.poolside.ai/v1"
    assert route.provider.api_key == "poolside-test-key"
    assert route.model == "laguna-s-2.1"


def test_pinned_default_provider_wins():
    router = ModelRouter(_settings(default_provider="groq", groq_api_key="gsk-test"))
    route = router.route(message="hello")
    assert route.provider.name == "groq"
    assert route.reason == "configured default provider"


def test_pinned_default_provider_unconfigured_falls_back():
    # A pinned default that is not configured is a soft hint: auto-routing
    # takes over (local providers are keyless), so open-source-only setups work.
    router = ModelRouter(_settings(default_provider="groq"))
    route = router.route(message="hello")
    assert route.provider.name == LOCAL_FALLBACK


def test_auto_picks_first_configured_by_priority():
    router = ModelRouter(_settings(anthropic_api_key="a", openai_api_key="sk-x"))
    route = router.route(message="hello")
    assert route.provider.name == "openai"
    assert route.reason.startswith("auto (simple, text)")


def test_complexity_uses_strong_model():
    router = ModelRouter(_settings(openai_api_key="sk-x"))
    simple = router.route(message="hi")
    complex_ = router.route(message="research and compare architectures step by step")
    assert simple.model == "gpt-4o-mini"
    assert complex_.model == "gpt-4o"


def test_auto_falls_back_to_local_when_no_keys():
    # Local providers are keyless, so auto routing still works honestly offline.
    router = ModelRouter(_settings())
    route = router.route(message="hello")
    assert route.provider.name == LOCAL_FALLBACK


# --- per-user disconnect (provider disabled) -------------------------------


def test_disabled_provider_skipped_in_auto():
    router = ModelRouter(_settings(openai_api_key="sk-x", anthropic_api_key="a-a"))
    route = router.route(message="hello", disabled=["openai"])
    assert route.provider.name == "anthropic"


def test_disabled_provider_override_falls_back():
    # Even an explicit override is a soft hint when the user has disconnected
    # the provider: fall through to auto-routing instead of erroring.
    router = ModelRouter(_settings(openai_api_key="sk-x"))
    route = router.route(provider_override="openai", disabled=["openai"])
    assert route.provider.name == LOCAL_FALLBACK


def test_pinned_default_provider_disabled_skipped():
    router = ModelRouter(_settings(default_provider="groq", groq_api_key="gsk-test"),
                         )
    route = router.route(message="hello", disabled=["groq"])
    assert route.provider.name == LOCAL_FALLBACK


def test_all_providers_disabled_raises():
    router = ModelRouter(_settings())
    with pytest.raises(ModelNotConfiguredError):
        router.route(message="hello", disabled=list(LOCAL_KEYLESS))


# --- request headers (no empty Authorization for keyless providers) ---------


def test_ollama_model_sends_no_auth_header():
    router = ModelRouter(_settings())
    model = router.get_model(router.route(provider_override="ollama"))
    assert model.spec.name == "ollama"
    headers = model._headers
    assert "Authorization" not in headers
    assert all(headers.values())  # no empty header values


def test_keyed_provider_sends_bearer_header():
    router = ModelRouter(_settings(openai_api_key="sk-test"))
    model = router.get_model(router.route(provider_override="openai"))
    assert model._headers["Authorization"] == "Bearer sk-test"


# --- vision routing (modality="image") -------------------------------------


def test_image_keeps_vision_capable_chosen_provider():
    router = ModelRouter(_settings(openai_api_key="sk-x"))
    route = router.route(message="describe", modality="image")
    assert route.provider.name == "openai"


def test_image_keeps_google_when_google_is_the_route():
    router = ModelRouter(_settings(google_api_key="g-x"))
    route = router.route(message="describe", modality="image")
    assert route.provider.name == "google"


def test_image_switches_to_vision_capable_provider():
    # deepseek wins text priority but has no vision; only local ollama remains.
    router = ModelRouter(_settings(deepseek_api_key="d-x"))
    text = router.route(message="hello")
    assert text.provider.name == "deepseek"
    image = router.route(message="describe", modality="image")
    assert image.provider.name == "ollama"


def test_image_respects_private_routing():
    # Privacy preference must not be silently broken by vision routing.
    router = ModelRouter(_settings(openai_api_key="sk-x"))
    route = router.route(message="describe", prefer_private=True, modality="image")
    assert route.provider.name == "ollama"


# ---------------------------------------------------------------------------


def test_estimate_complexity_markers_and_length():
    assert estimate_complexity("hello there") == "simple"
    assert estimate_complexity("please research and compare the options") == "complex"
    assert estimate_complexity("x" * 601) == "complex"


# --- memory-safe Ollama fallback -------------------------------------------


def _ollama_model(model: str = "qwen2.5:1.5b") -> mr.JaloraChatModel:
    spec = mr.ProviderSpec(
        "ollama", "openai", "http://ollama/v1", None,
        "qwen2.5:7b", "qwen2.5:7b", requires_key=False,
    )
    return mr.JaloraChatModel(spec=spec, model_name=model)


def _default_ladder(monkeypatch):
    monkeypatch.setattr(
        ollama_memory, "model_tiers",
        lambda: ["qwen2.5:7b", "qwen2.5:1.5b", "qwen2.5:0.5b"],
    )


def test_attempt_models_non_ollama_single():
    spec = mr.ProviderSpec(
        "openai", "openai", "https://api.openai.com/v1", "sk-x",
        "gpt-4o-mini", "gpt-4o",
    )
    model = mr.JaloraChatModel(spec=spec, model_name="gpt-4o-mini")
    assert model._ollama_attempt_models("gpt-4o-mini") == ["gpt-4o-mini"]


def test_shared_http_clients_are_singletons():
    """Connection pools are reused: exactly one async + one sync client
    per process instead of a fresh instance per model call."""
    mr.reset_http_clients()
    try:
        a = mr.get_async_client()
        b = mr.get_async_client()
        assert a is b
        s1 = mr.get_sync_client()
        s2 = mr.get_sync_client()
        assert s1 is s2
        assert a is not s1
    finally:
        mr.reset_http_clients()


def test_attempt_models_ollama_starts_at_current_tier(monkeypatch):
    _default_ladder(monkeypatch)
    assert _ollama_model("qwen2.5:1.5b")._ollama_attempt_models("qwen2.5:1.5b") == [
        "qwen2.5:1.5b", "qwen2.5:0.5b",  # not the whole ladder: no going "up"
    ]


def test_attempt_models_custom_ollama_gets_no_fallback():
    model = _ollama_model("custom:tiny")
    assert model._ollama_attempt_models("custom:tiny") == ["custom:tiny"]


def test_retryable_ollama_markers():
    model = _ollama_model()
    assert model._retryable_ollama('{"error":"out-of-memory"}') is True
    assert model._retryable_ollama(
        "ggml_backend_cpu_buffer_type_alloc_buffer: failed to allocate buffer"
    ) is True
    assert model._retryable_ollama("std::bad_alloc") is True
    assert model._retryable_ollama('{"error":"model qwen2.5:7b not found"}') is True
    assert model._retryable_ollama('{"error":"bad request"}') is False


def test_retryable_ollama_never_for_cloud():
    spec = mr.ProviderSpec(
        "groq", "openai", "https://api.groq.com/openai/v1", "gsk-x", "m", "m",
    )
    model = mr.JaloraChatModel(spec=spec, model_name="m")
    assert model._retryable_ollama('{"error":"out-of-memory"}') is False
    assert model._retryable_ollama('{"error":"model not found"}') is False


class _FakeStream:
    def __init__(self, status_code, lines=(), body="", json_data=None):
        self.status_code = status_code
        self._lines = list(lines)
        self._body = body
        self._json_data = json_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aread(self):
        return self._body.encode()

    def aiter_lines(self):
        async def _it():
            for line in self._lines:
                yield line
        return _it()

    @property
    def text(self):
        return self._body

    def json(self):
        return self._json_data or {}


class _FakeStreamClient:
    """Scripted httpx client: one response per stream()/post() call."""

    def __init__(self, *responses):
        self._queue = list(responses)
        self.calls: list[tuple[str, str, dict | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append((method, url, dict(json) if json is not None else None))
        return self._queue.pop(0)

    async def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, dict(json) if json is not None else None))
        return self._queue.pop(0)


async def _collect_text(gen):
    parts = []
    async for chunk in gen:
        parts.append(chunk.message.content or "")
    return "".join(parts)


def test_adjacent_json_tool_arguments_expand_into_distinct_calls():
    raw_arguments = '{"query":"Transparency International Kenya"}{"query":"Code for Africa"}'

    calls = mr._parse_tool_calls([{
        "id": "call-contacts",
        "function": {"name": "search_web", "arguments": raw_arguments},
    }])

    assert [call["args"] for call in calls] == [
        {"query": "Transparency International Kenya"},
        {"query": "Code for Africa"},
    ]
    assert [call["id"] for call in calls] == [
        "call-contacts_part_1",
        "call-contacts_part_2",
    ]


def test_invalid_tool_arguments_remain_explicitly_invalid():
    raw_arguments = '{"query":"Transparency International Kenya"}{"query":'

    calls = mr._parse_tool_calls([{
        "id": "call-invalid",
        "function": {"name": "search_web", "arguments": raw_arguments},
    }])

    assert len(calls) == 1
    assert calls[0]["args"] == {"_raw": raw_arguments}


def test_streamed_adjacent_json_arguments_expand_before_tool_execution(monkeypatch):
    _default_ladder(monkeypatch)
    arguments = '{"query":"Transparency International Kenya"}{"query":"Code for Africa"}'
    event = {
        "choices": [{
            "delta": {
                "tool_calls": [{
                    "index": 0,
                    "id": "call-streamed-contacts",
                    "function": {
                        "name": "search_web",
                        "arguments": arguments,
                    },
                }],
            },
        }],
    }
    client = _FakeStreamClient(_FakeStream(
        200,
        lines=[f"data: {json.dumps(event)}", "data: [DONE]"],
    ))
    monkeypatch.setattr(mr, "get_async_client", lambda timeout=120.0: client)
    model = _ollama_model("qwen2.5:1.5b")

    async def collect_chunks():
        return [
            chunk async for chunk in
            model._astream_openai({"model": "qwen2.5:1.5b"})
        ]

    chunks = asyncio.run(collect_chunks())
    complete_calls = chunks[-1].message.response_metadata["complete_tool_calls"]

    assert [call["args"] for call in complete_calls] == [
        {"query": "Transparency International Kenya"},
        {"query": "Code for Africa"},
    ]
    assert len({call["id"] for call in complete_calls}) == 2


def test_astream_ollama_retries_once_on_oom(monkeypatch):
    _default_ladder(monkeypatch)
    monkeypatch.setattr(
        ollama_memory, "pick_ollama_model",
        lambda: ollama_memory.ModelChoice("qwen2.5:0.5b", "low-memory"),
    )
    monkeypatch.setattr(ollama_memory, "unload", lambda name: _noop_unload(name))
    client = _FakeStreamClient(
        _FakeStream(500, body='{"error":"out-of-memory, try again in a few minutes"}'),
        _FakeStream(
            200,
            lines=[
                'data: {"choices":[{"delta":{"content":"hi"},"index":0}]}',
                "data: [DONE]",
            ],
        ),
    )
    monkeypatch.setattr(mr, "get_async_client", lambda timeout=120.0: client)

    model = _ollama_model("qwen2.5:1.5b")
    text = asyncio.run(_collect_text(model._astream_openai({"model": "qwen2.5:1.5b"})))
    assert text == "hi"
    assert [c[2].get("model") for c in client.calls] == ["qwen2.5:1.5b", "qwen2.5:0.5b"]


def test_astream_ollama_non_retryable_raises(monkeypatch):
    _default_ladder(monkeypatch)
    client = _FakeStreamClient(_FakeStream(400, body='{"error":"bad request"}'))
    monkeypatch.setattr(mr, "get_async_client", lambda timeout=120.0: client)

    model = _ollama_model("qwen2.5:1.5b")
    with pytest.raises(RuntimeError, match="API error 400"):
        asyncio.run(_collect_text(model._astream_openai({"model": "qwen2.5:1.5b"})))
    assert len(client.calls) == 1


def test_astream_cloud_error_not_retried(monkeypatch):
    spec = mr.ProviderSpec(
        "groq", "openai", "https://api.groq.com/openai/v1", "gsk-x", "m", "m",
    )
    model = mr.JaloraChatModel(spec=spec, model_name="m")
    client = _FakeStreamClient(_FakeStream(500, body='{"error":"internal"}'))
    monkeypatch.setattr(mr, "get_async_client", lambda timeout=120.0: client)
    with pytest.raises(RuntimeError, match="groq API error 500"):
        asyncio.run(_collect_text(model._astream_openai({"model": "m"})))
    assert len(client.calls) == 1


def test_astream_reduces_output_budget_after_tpm_rejection(monkeypatch):
    spec = mr.ProviderSpec(
        "groq", "openai", "https://api.groq.com/openai/v1", "gsk-x", "m", "m",
    )
    model = mr.JaloraChatModel(spec=spec, model_name="m")
    client = _FakeStreamClient(
        _FakeStream(
            413,
            body=(
                '{"error":{"code":"rate_limit_exceeded","message":'
                '"Limit 8000, Requested 8243 tokens per minute"}}'
            ),
        ),
        _FakeStream(
            200,
            lines=[
                'data: {"choices":[{"delta":{"content":"recovered"},"index":0}]}',
                "data: [DONE]",
            ],
        ),
    )
    monkeypatch.setattr(mr, "get_async_client", lambda timeout=120.0: client)

    text = asyncio.run(_collect_text(
        model._astream_openai({"model": "m", "max_tokens": 4096})
    ))
    assert text == "recovered"
    assert client.calls[0][2]["max_tokens"] == 4096
    assert 256 <= client.calls[1][2]["max_tokens"] < 4096


def test_astream_reduces_output_budget_to_remaining_provider_credit(monkeypatch):
    spec = mr.ProviderSpec(
        "openrouter", "openai", "https://openrouter.ai/api/v1", "or-test", "m", "m",
    )
    model = mr.JaloraChatModel(spec=spec, model_name="m")
    client = _FakeStreamClient(
        _FakeStream(
            402,
            body=(
                '{"error":{"code":402,"message":'
                '"You requested up to 8192 tokens, but can only afford 1451."}}'
            ),
        ),
        _FakeStream(
            200,
            lines=[
                'data: {"choices":[{"delta":{"content":"recovered"},"index":0}]}',
                "data: [DONE]",
            ],
        ),
    )
    monkeypatch.setattr(mr, "get_async_client", lambda timeout=120.0: client)

    text = asyncio.run(_collect_text(
        model._astream_openai({"model": "m", "max_tokens": 8192})
    ))

    assert text == "recovered"
    assert [call[2]["max_tokens"] for call in client.calls] == [8192, 1451]


def test_astream_does_not_retry_non_tpm_rate_limit(monkeypatch):
    spec = mr.ProviderSpec(
        "groq", "openai", "https://api.groq.com/openai/v1", "gsk-x", "m", "m",
    )
    model = mr.JaloraChatModel(spec=spec, model_name="m")
    client = _FakeStreamClient(
        _FakeStream(
            413,
            body='{"error":{"code":"rate_limit_exceeded","message":"Too many requests"}}',
        )
    )
    monkeypatch.setattr(mr, "get_async_client", lambda timeout=120.0: client)
    with pytest.raises(RuntimeError, match="API error 413"):
        asyncio.run(_collect_text(model._astream_openai({"model": "m"})))
    assert len(client.calls) == 1


def test_astream_does_not_retry_when_used_tpm_leaves_no_input_budget(monkeypatch):
    spec = mr.ProviderSpec(
        "groq", "openai", "https://api.groq.com/openai/v1", "gsk-x", "m", "m",
    )
    model = mr.JaloraChatModel(spec=spec, model_name="m")
    client = _FakeStreamClient(_FakeStream(
        429,
        body=(
            '{"error":{"code":"rate_limit_exceeded","message":'
            '"Limit 8000, Used 7501, Requested 7200 tokens per minute"}}'
        ),
    ))
    monkeypatch.setattr(mr, "get_async_client", lambda timeout=120.0: client)
    with pytest.raises(RuntimeError, match="API error 429"):
        asyncio.run(_collect_text(model._astream_openai({"model": "m", "max_tokens": 4096})))
    assert len(client.calls) == 1


async def _noop_unload(name):
    return False


def test_agenerate_ollama_retries_once_on_oom(monkeypatch):
    _default_ladder(monkeypatch)
    monkeypatch.setattr(
        ollama_memory, "pick_ollama_model",
        lambda: ollama_memory.ModelChoice("qwen2.5:0.5b", "low-memory"),
    )
    monkeypatch.setattr(ollama_memory, "unload", lambda name: _noop_unload(name))
    client = _FakeStreamClient(
        _FakeStream(500, body='{"error":"out-of-memory"}'),
        _FakeStream(200, json_data={
            "choices": [{
                "message": {"content": "hi", "role": "assistant"},
                "finish_reason": "stop",
                "index": 0,
            }],
            "model": "qwen2.5:0.5b",
        }),
    )
    monkeypatch.setattr(mr, "get_async_client", lambda timeout=120.0: client)

    model = _ollama_model("qwen2.5:1.5b")
    result = asyncio.run(model._agenerate([]))
    assert result.generations[0].message.content == "hi"
    assert [c[2].get("model") for c in client.calls] == ["qwen2.5:1.5b", "qwen2.5:0.5b"]


def test_astream_ollama_falls_back_to_next_model_on_empty(monkeypatch):
    """An empty (contentless) tier must fall back to the next model."""
    _default_ladder(monkeypatch)
    client = _FakeStreamClient(
        _FakeStream(200, lines=["data: [DONE]"]),  # no content at all
        _FakeStream(
            200,
            lines=[
                'data: {"choices":[{"delta":{"content":"recovered"},"index":0}]}',
                "data: [DONE]",
            ],
        ),
    )
    monkeypatch.setattr(mr, "get_async_client", lambda timeout=120.0: client)

    model = _ollama_model("qwen2.5:1.5b")
    text = asyncio.run(_collect_text(model._astream_openai({"model": "qwen2.5:1.5b"})))
    assert text == "recovered"
    assert [c[2].get("model") for c in client.calls] == ["qwen2.5:1.5b", "qwen2.5:0.5b"]


def test_astream_empty_cloud_stream_yields_nothing(monkeypatch):
    """A single-attempt cloud provider with an empty stream yields no chunks
    (the graph layer turns this into a friendly warning + retry, not a crash)."""
    spec = mr.ProviderSpec(
        "groq", "openai", "https://api.groq.com/openai/v1", "gsk-x", "m", "m",
    )
    model = mr.JaloraChatModel(spec=spec, model_name="m")
    client = _FakeStreamClient(_FakeStream(200, lines=["data: [DONE]"]))
    monkeypatch.setattr(mr, "get_async_client", lambda timeout=120.0: client)

    text = asyncio.run(_collect_text(model._astream_openai({"model": "m"})))
    assert text == ""
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Ollama generation options (stage 4)
# ---------------------------------------------------------------------------


def test_ollama_payload_carries_tiered_sampler_options(monkeypatch):
    from app import config as app_config

    s = _settings(
        ollama_num_ctx_heavy=32768, ollama_num_ctx_mid=16384,
        ollama_num_ctx_light=16384, ollama_num_ctx_survival=8192,
        ollama_top_p=0.9, ollama_repeat_penalty=1.15,
        ollama_model="heavy-m", ollama_mid_model="mid-m",
        ollama_light_model="light-m", ollama_survival_model="life-m",
    )
    monkeypatch.setattr(app_config, "get_settings", lambda: s)
    monkeypatch.setattr(
        ollama_memory, "model_tiers",
        lambda: ["heavy-m", "mid-m", "light-m", "life-m"],
    )
    assert _ollama_model("heavy-m")._payload([], True)["options"]["num_ctx"] == 32768
    assert _ollama_model("mid-m")._payload([], True)["options"]["num_ctx"] == 16384
    assert _ollama_model("light-m")._payload([], True)["options"]["num_ctx"] == 16384
    assert _ollama_model("life-m")._payload([], True)["options"]["num_ctx"] == 8192
    payload = _ollama_model("heavy-m")._payload([], True)
    assert payload["options"]["top_p"] == 0.9
    assert payload["options"]["repeat_penalty"] == 1.15
    assert payload["options"]["temperature"] == 0.6
    # Unknown model names get the mid-tier window (never fail the payload).
    assert _ollama_model("someone-else")._payload([], True)["options"]["num_ctx"] == 16384


def test_non_ollama_payload_has_no_options():
    spec = mr.ProviderSpec("groq", "openai", "https://api.groq.com/openai/v1", "k", "m", "m")
    payload = mr.JaloraChatModel(spec=spec, model_name="m")._payload([], True)
    assert "options" not in payload


def test_serializers_close_unanswered_tool_calls_without_replay():
    messages = [
        HumanMessage(content="Earlier request"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "create_pdf", "args": {"title": "Report"}, "id": "call-1"},
                {"name": "send_email", "args": {"to": "user@example.com"}, "id": "call-2"},
            ],
        ),
        ToolMessage(content="PDF created", tool_call_id="call-1"),
        HumanMessage(content="Continue with my new request"),
        ToolMessage(content="Unpaired old result", tool_call_id="call-orphan"),
    ]

    openai_messages = mr._to_openai_messages(messages)

    assert [message["role"] for message in openai_messages] == [
        "user", "assistant", "tool", "tool", "user",
    ]
    assert openai_messages[2]["tool_call_id"] == "call-1"
    assert openai_messages[3]["tool_call_id"] == "call-2"
    assert "do not assume it ran or replay it" in openai_messages[3]["content"].lower()
    assert all(message.get("tool_call_id") != "call-orphan" for message in openai_messages)

    _, anthropic_messages = mr._to_anthropic_messages(messages)
    assistant_index = next(
        index for index, message in enumerate(anthropic_messages)
        if message["role"] == "assistant"
    )
    tool_result = anthropic_messages[assistant_index + 1]
    assert tool_result["role"] == "user"
    assert any(
        block.get("type") == "tool_result"
        and block.get("tool_use_id") == "call-2"
        for block in tool_result["content"]
    )


def test_google_flattens_foreign_provider_tool_history():
    messages = [
        HumanMessage(content="Create a plan for this task."),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "update_plan",
                    "args": {"steps": [{"id": "research", "tool": "search_web"}]},
                    "id": "call-from-other-provider",
                },
            ],
            additional_kwargs={
                "tool_call_extra_content": {
                    "call-from-other-provider": {
                        "google": {"thought_signature": "provider-specific"},
                    },
                },
            },
        ),
        ToolMessage(
            content="Plan accepted and stored.",
            tool_call_id="call-from-other-provider",
        ),
    ]

    spec = mr.ProviderSpec(
        "google", "openai", "https://example.test/v1", "key", "m", "m",
    )
    google_payload = mr.JaloraChatModel(
        spec=spec, model_name="m",
    )._payload(messages, stream=True)
    google_messages = google_payload["messages"]

    assert [message["role"] for message in google_messages] == [
        "user", "assistant", "user",
    ]
    assert google_messages[-1]["role"] == "user"
    assert "update_plan" in google_messages[1]["content"]
    assert "Plan accepted and stored." in google_messages[2]["content"]
    assert "untrusted" in google_messages[2]["content"].lower()
    assert "tool_calls" not in google_messages[1]

    openai_messages = mr._to_openai_messages(messages)
    assert [message["role"] for message in openai_messages] == [
        "user", "assistant", "tool",
    ]


def test_google_model_default_is_currently_supported():
    assert Settings(_env_file=None).google_model == "gemini-3.5-flash-lite"
