"""Stage 7 evaluation: end-to-end Ollama pipeline scenarios.

Deterministic scenarios run offline (scripted fake HTTP clients). Opt-in live
scenarios hit a local Ollama only when JALORA_OLLAMA_EVAL_LIVE=1.
"""
from __future__ import annotations

import asyncio
import os

import httpx
import pytest
from langchain_core.messages import HumanMessage

from app.agents.intent import classify_intent
from app.config import Settings
from app.services import model_router as mr
from app.services import ollama_memory
from app.services.model_router import JaloraChatModel, ProviderSpec
from app.tools import registry as real_registry
from app.tools.routing import tool_names_for_intent

_LADDER = ["qwen2.5:7b", "llama3.2:latest", "qwen2.5:1.5b", "qwen2.5:0.5b"]


def _ladder(monkeypatch):
    monkeypatch.setattr(ollama_memory, "model_tiers", lambda: list(_LADDER))


def _settings(**overrides):
    base = {
        "ollama_num_ctx_heavy": 32768, "ollama_num_ctx_mid": 16384,
        "ollama_num_ctx_light": 16384, "ollama_num_ctx_survival": 8192,
        "ollama_top_p": 0.9, "ollama_repeat_penalty": 1.15,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _ollama(model: str = "qwen2.5:1.5b") -> JaloraChatModel:
    spec = ProviderSpec(
        "ollama", "openai", "http://ollama/v1", None,
        "qwen2.5:7b", "qwen2.5:7b", requires_key=False,
    )
    return JaloraChatModel(spec=spec, model_name=model)


async def _noop_unload(name):
    return False


# ---------------------------------------------------------------------------
# Deterministic scenario 1: intent -> scoped tool set (no network)
# ---------------------------------------------------------------------------


def test_eval_intent_to_tools_pipeline():
    cases = {
        "Research the latest renewable energy statistics": "research",
        "Debug this traceback and run the unit tests": "coding",
        "Remind me to call mom at 5pm tomorrow": "scheduling",
        "Write a short poem about rain": "task",
        "hey, how's it going?": "chat",
    }
    known = {spec.name for spec in real_registry.all()}
    for prompt, expected in cases.items():
        intent = classify_intent(prompt)
        assert intent.intent == expected, prompt
        names = tool_names_for_intent(prompt)
        if names is not None:  # task/unknown deliberately keeps the full registry
            assert names, prompt
            assert set(names) <= known, f"{prompt}: unknown tool names"


def test_scheduled_communication_stays_scheduling():
    """Scheduling markers must take precedence over the action verb "call"."""
    assert classify_intent("Please remind me to call my colleague tomorrow at 09:30").intent == "scheduling"
    assert classify_intent("Call my colleague now").intent == "task"


# ---------------------------------------------------------------------------
# Deterministic scenario 2: Ollama request payload contract
# ---------------------------------------------------------------------------


def test_eval_ollama_payload_contract(monkeypatch):
    from app import config as app_config

    _ladder(monkeypatch)
    monkeypatch.setattr(app_config, "get_settings", lambda: _settings())

    model = _ollama("llama3.2:latest")  # ladder index 1 => mid tier
    payload = model._payload([HumanMessage(content="hi")], stream=True)

    assert payload["model"] == "llama3.2:latest"
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}
    assert payload["messages"] == [{"role": "user", "content": "hi"}]

    options = payload["options"]
    assert options["num_ctx"] == 16384
    assert options["top_p"] == 0.9
    assert options["repeat_penalty"] == 1.15
    assert options["temperature"] == model.temperature


# ---------------------------------------------------------------------------
# Deterministic scenario 3: OOM on the heavy tier falls back transparently
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status, body="", json_data=None):
        self.status_code = status
        self._body = body
        self._json = json_data
        self.request = httpx.Request("POST", "http://ollama/v1/chat/completions")

    @property
    def text(self):
        return self._body

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err", request=self.request,
                response=httpx.Response(self.status_code, request=self.request, text=self._body),
            )


class _FakeSyncClient:
    def __init__(self, *responses):
        self._queue = list(responses)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(json)
        return self._queue.pop(0)


def test_eval_oom_falls_back_sync(monkeypatch):
    from app import config as app_config

    _ladder(monkeypatch)
    monkeypatch.setattr(
        ollama_memory, "pick_ollama_model",
        lambda: ollama_memory.ModelChoice("qwen2.5:0.5b", "low-memory"),
    )
    monkeypatch.setattr(ollama_memory, "unload", _noop_unload)
    monkeypatch.setattr(app_config, "get_settings", lambda: _settings())

    client = _FakeSyncClient(
        _Resp(500, body='{"error":"out-of-memory, try again in a few minutes"}'),
        _Resp(200, json_data={
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "model": "llama3.2:latest",
        }),
    )
    monkeypatch.setattr(mr, "get_sync_client", lambda timeout=120.0: client)

    result = _ollama("qwen2.5:7b")._generate([HumanMessage(content="hi")])
    assert result.generations[0].message.content == "ok"
    assert [c["model"] for c in client.calls] == ["qwen2.5:7b", "llama3.2:latest"]


# ---------------------------------------------------------------------------
# Deterministic scenario 4: empty first tier stream falls through to the next
# ---------------------------------------------------------------------------


class _Stream:
    def __init__(self, status_code, lines=(), body=""):
        self.status_code = status_code
        self._lines = list(lines)
        self._body = body

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


class _FakeAsyncClient:
    def __init__(self, *streams):
        self._queue = list(streams)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append(json)
        return self._queue.pop(0)


async def _collect(gen):
    parts = []
    async for chunk in gen:
        parts.append(chunk.message.content or "")
    return "".join(parts)


def test_eval_empty_first_tier_falls_back(monkeypatch):
    _ladder(monkeypatch)
    client = _FakeAsyncClient(
        _Stream(200, lines=[]),
        _Stream(200, lines=[
            'data: {"choices":[{"delta":{"content":"two"},"index":0}]}',
            "data: [DONE]",
        ]),
    )
    monkeypatch.setattr(mr, "get_async_client", lambda timeout=120.0: client)

    text = asyncio.run(_collect(_ollama("qwen2.5:1.5b")._astream_openai({"model": "qwen2.5:1.5b"})))
    assert text == "two"
    assert [c["model"] for c in client.calls] == ["qwen2.5:1.5b", "qwen2.5:0.5b"]


# ---------------------------------------------------------------------------
# Opt-in live scenario: real local Ollama (JALORA_OLLAMA_EVAL_LIVE=1)
# ---------------------------------------------------------------------------

_LIVE = os.environ.get("JALORA_OLLAMA_EVAL_LIVE") == "1"


def _ollama_base() -> str:
    from app.config import get_settings

    return get_settings().ollama_base_url.rstrip("/")


@pytest.mark.skipif(not _LIVE, reason="set JALORA_OLLAMA_EVAL_LIVE=1 to run live")
def test_live_eval_tier_models_respond():
    import time

    base = _ollama_base()
    tags = httpx.get(f"{base}/api/tags", timeout=5.0)
    assert tags.status_code == 200
    installed = {m.get("name") for m in tags.json().get("models", [])}

    for name in _LADDER:
        if name not in installed:
            continue
        spec = ProviderSpec("ollama", "openai", f"{base}/v1", None, name, name, requires_key=False)
        model = JaloraChatModel(spec=spec, model_name=name)
        started = time.perf_counter()
        result = model._generate([HumanMessage(content="Reply with the single word: pong")])
        elapsed = time.perf_counter() - started
        content = (result.generations[0].message.content or "").strip()
        assert content, f"{name} returned an empty reply"
        assert elapsed < 180, f"{name} took too long: {elapsed:.1f}s"
