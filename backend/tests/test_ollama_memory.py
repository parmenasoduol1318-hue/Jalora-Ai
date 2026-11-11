"""Memory-aware Ollama loading: RAM probing, model pick, unload, monitor."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.services import ollama_memory
from app.services.ollama_memory import (
    OllamaModelInfo,
    ModelChoice,
    estimate_resident_gb,
    names_to_unload,
)


def _settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        default_provider="auto",
        openai_api_key=None,
        **overrides,
    )


@pytest.fixture(autouse=True)
def _reset_snapshot():
    ollama_memory._set_snapshot(tags={}, loaded={})
    yield
    ollama_memory._set_snapshot(tags={}, loaded={})


def _patch_ram(monkeypatch, gb: float):
    monkeypatch.setattr(ollama_memory, "available_memory_gb", lambda: gb)


def _patch_settings(monkeypatch, **overrides):
    settings = _settings(**overrides)
    monkeypatch.setattr(ollama_memory, "get_settings", lambda: settings)
    return settings


# ---------------------------------------------------------------------------
# RAM probing
# ---------------------------------------------------------------------------

def test_available_memory_gb_returns_positive():
    free = ollama_memory.available_memory_gb()
    assert free > 0.0
    assert free < 10_000.0  # sane bound (it is the real machine's RAM)


# ---------------------------------------------------------------------------
# Model size estimation
# ---------------------------------------------------------------------------

def test_estimate_resident_gb_uses_installed_size():
    info = OllamaModelInfo("qwen2.5:7b", size_bytes=3_121_348_608)
    got = estimate_resident_gb(info, fallback_gb=4.0)
    expected = info.size_gb * 1.15
    assert got == pytest.approx(expected, rel=1e-6)
    assert expected < 4.0  # installed info beats the generic threshold


def test_estimate_resident_gb_falls_back_to_threshold():
    assert estimate_resident_gb(None, fallback_gb=4.0) == 4.0


# ---------------------------------------------------------------------------
# Choosing a model from free RAM
# ---------------------------------------------------------------------------

def test_pick_single_model_when_heavy_equals_light(monkeypatch):
    _patch_settings(
        monkeypatch,
        ollama_model="tiny",
        ollama_mid_model="tiny",
        ollama_light_model="tiny",
        ollama_survival_model="",
    )
    choice = ollama_memory.pick_ollama_model()
    assert choice == ModelChoice("tiny", "single-model")


def test_pick_heavy_when_ram_sufficient(monkeypatch):
    _patch_settings(monkeypatch)
    _patch_ram(monkeypatch, 16.0)
    assert ollama_memory.pick_ollama_model().model == "qwen2.5:7b"


def test_pick_mid_when_ram_intermediate(monkeypatch):
    _patch_settings(monkeypatch)
    _patch_ram(monkeypatch, 2.5)
    choice = ollama_memory.pick_ollama_model()
    assert choice.model == "llama3.2:latest"
    assert choice.reason == "low-memory"


def test_pick_light_when_ram_tight(monkeypatch):
    _patch_settings(monkeypatch)
    _patch_ram(monkeypatch, 1.2)
    choice = ollama_memory.pick_ollama_model()
    assert choice.model == "qwen2.5:1.5b"
    assert choice.reason == "low-memory"


def test_pick_survival_when_under_light_floor(monkeypatch):
    _patch_settings(monkeypatch)
    _patch_ram(monkeypatch, 0.4)
    choice = ollama_memory.pick_ollama_model()
    assert choice.model == "qwen2.5:0.5b"
    assert choice.reason == "low-memory"


def test_pick_survival_forced_below_survival_floor(monkeypatch):
    _patch_settings(monkeypatch)
    _patch_ram(monkeypatch, 0.2)
    choice = ollama_memory.pick_ollama_model()
    assert choice.model == "qwen2.5:0.5b"
    assert choice.reason == "low-memory-forced"


def test_pick_uses_installed_size_when_available(monkeypatch):
    big = OllamaModelInfo("qwen2.5:7b", size_bytes=2 * 1024 ** 3)  # 2GB
    ollama_memory._set_snapshot(tags={"qwen2.5:7b": big}, loaded={})
    _patch_settings(monkeypatch, ollama_heavy_model_min_free_gb=1.0)
    _patch_ram(monkeypatch, 2.5)
    # 2GB * 1.15 = 2.3GB installed-size requirement > the 1.0GB floor,
    # and still fits in the 2.5GB free RAM.
    assert ollama_memory.pick_ollama_model().model == "qwen2.5:7b"


def test_pick_subtracts_loaded_resident_tier_models(monkeypatch):
    # The lightweight + survival models are already resident (1.4GB pinned).
    # Free RAM 4.5GB would fit the heavy model in isolation, but after
    # subtracting the pinned footprint only 3.1GB remain -> falls back to mid.
    ollama_memory._set_snapshot(
        tags={},
        loaded={"qwen2.5:1.5b": 1.0, "qwen2.5:0.5b": 0.4},
    )
    _patch_settings(monkeypatch)
    _patch_ram(monkeypatch, 4.5)
    choice = ollama_memory.pick_ollama_model()
    assert choice.model == "llama3.2:latest"
    assert choice.reason == "low-memory"


def test_pick_note_heavy_still_fits_with_headroom(monkeypatch):
    # Same pinned footprint but enough headroom -> heavy model is still chosen.
    ollama_memory._set_snapshot(
        tags={},
        loaded={"qwen2.5:1.5b": 1.0, "qwen2.5:0.5b": 0.4},
    )
    _patch_settings(monkeypatch)
    _patch_ram(monkeypatch, 6.0)
    assert ollama_memory.pick_ollama_model().model == "qwen2.5:7b"


def test_pick_respects_override(monkeypatch):
    _patch_settings(monkeypatch)
    _patch_ram(monkeypatch, 0.1)
    choice = ollama_memory.pick_ollama_model("llama3.2:1b")
    assert choice.model == "llama3.2:1b"
    assert choice.reason == "override"


# ---------------------------------------------------------------------------
# Unload decisions (pure)
# ---------------------------------------------------------------------------

def test_names_to_unload_healthy_memory_returns_nothing():
    loaded = {"qwen2.5:7b": 3.5, "qwen2.5:1.5b": 0.8}
    assert names_to_unload(8.0, loaded, "qwen2.5:7b", "qwen2.5:1.5b", 1.5) == []


def test_names_to_unload_low_memory_unloads_heavy_keeps_light():
    loaded = {"qwen2.5:7b": 3.5, "qwen2.5:1.5b": 0.8}
    names = names_to_unload(0.9, loaded, "qwen2.5:7b", "qwen2.5:1.5b", 1.5)
    assert names == ["qwen2.5:7b"]
    assert "qwen2.5:1.5b" not in names


def test_names_to_unload_unloads_other_resident_models():
    loaded = {"llama3.2:1b": 1.0, "qwen2.5:7b": 3.5, "qwen2.5:1.5b": 0.8}
    names = names_to_unload(0.9, loaded, "qwen2.5:7b", "qwen2.5:1.5b", 1.5)
    assert names == ["qwen2.5:7b", "llama3.2:1b"]


def test_names_to_unload_keeps_survival_and_light():
    loaded = {"qwen2.5:7b": 3.5, "qwen2.5:1.5b": 0.8, "qwen2.5:0.5b": 0.3}
    names = names_to_unload(
        0.9, loaded, "qwen2.5:7b", "qwen2.5:1.5b", 1.5, survival="qwen2.5:0.5b"
    )
    assert names == ["qwen2.5:7b"]


def test_names_to_unload_keeps_mid_tier():
    loaded = {"qwen2.5:7b": 3.5, "llama3.2:latest": 1.6}
    names = names_to_unload(
        0.9, loaded, "qwen2.5:7b", "qwen2.5:1.5b", 1.5, mid="llama3.2:latest"
    )
    assert names == ["qwen2.5:7b"]


# ---------------------------------------------------------------------------
# Tier ladder
# ---------------------------------------------------------------------------

def test_model_tiers_orders_heavy_mid_light_survival(monkeypatch):
    _patch_settings(monkeypatch)
    assert ollama_memory.model_tiers() == [
        "qwen2.5:7b",
        "llama3.2:latest",
        "qwen2.5:1.5b",
        "qwen2.5:0.5b",
    ]


def test_model_tiers_survival_disabled_when_empty(monkeypatch):
    _patch_settings(monkeypatch, ollama_survival_model="")
    assert ollama_memory.model_tiers() == [
        "qwen2.5:7b",
        "llama3.2:latest",
        "qwen2.5:1.5b",
    ]


def test_model_tiers_dedupes_when_mid_equals_heavy(monkeypatch):
    _patch_settings(monkeypatch, ollama_mid_model="qwen2.5:7b")
    assert ollama_memory.model_tiers() == [
        "qwen2.5:7b",
        "qwen2.5:1.5b",
        "qwen2.5:0.5b",
    ]


# ---------------------------------------------------------------------------
# Unload HTTP call
# ---------------------------------------------------------------------------

def test_unload_posts_keep_alive_zero(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = str(request.url.path)
        seen["json"] = json.loads(request.content or b"{}")
        return httpx.Response(200, json={})

    async def fake_client():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://ollama"
        )

    monkeypatch.setattr(ollama_memory, "_client", fake_client)
    ok = asyncio.run(ollama_memory.unload("qwen2.5:7b"))
    assert ok is True
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/generate"
    assert seen["json"] == {"model": "qwen2.5:7b", "prompt": "", "keep_alive": 0}


def test_unload_failure_returns_false(monkeypatch):
    async def fake_client():
        class _Boom:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, *a, **k):
                raise httpx.ConnectError("refused")

        return _Boom()

    monkeypatch.setattr(ollama_memory, "_client", fake_client)
    assert asyncio.run(ollama_memory.unload("qwen2.5:7b")) is False


def test_set_keep_alive_posts_seconds(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["json"] = json.loads(request.content or b"{}")
        return httpx.Response(200, json={})

    async def fake_client():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://ollama"
        )

    monkeypatch.setattr(ollama_memory, "_client", fake_client)
    ok = asyncio.run(ollama_memory.set_keep_alive("qwen2.5:7b", 120.0))
    assert ok is True
    assert seen["json"] == {"model": "qwen2.5:7b", "prompt": "", "keep_alive": 120.0}


def test_warm_noop_when_already_resident(monkeypatch):
    ollama_memory._set_snapshot(tags={}, loaded={"llama3.2:latest": 1.6})
    called: list[tuple[str, float]] = []

    async def fake_set(model: str, seconds: float) -> bool:
        called.append((model, seconds))
        return True

    monkeypatch.setattr(ollama_memory, "set_keep_alive", fake_set)
    assert asyncio.run(ollama_memory.warm("llama3.2:latest", 600.0)) is True
    assert called == []


def test_warm_loads_when_cold(monkeypatch):
    ollama_memory._set_snapshot(tags={}, loaded={})
    called: list[tuple[str, float]] = []

    async def fake_set(model: str, seconds: float) -> bool:
        called.append((model, seconds))
        return True

    monkeypatch.setattr(ollama_memory, "set_keep_alive", fake_set)
    assert asyncio.run(ollama_memory.warm("qwen2.5:7b", 120.0)) is True
    assert called == [("qwen2.5:7b", 120.0)]


def test_keep_alive_seconds_per_tier(monkeypatch):
    settings = _settings(
        ollama_keep_alive_heavy_seconds=11,
        ollama_keep_alive_mid_seconds=22,
        ollama_keep_alive_light_seconds=33,
        ollama_keep_alive_survival_seconds=44,
    )
    monkeypatch.setattr(ollama_memory, "get_settings", lambda: settings)
    assert ollama_memory.keep_alive_seconds_for("qwen2.5:7b") == 11
    assert ollama_memory.keep_alive_seconds_for("llama3.2:latest") == 22
    assert ollama_memory.keep_alive_seconds_for("qwen2.5:1.5b") == 33
    assert ollama_memory.keep_alive_seconds_for("qwen2.5:0.5b") == 44
    assert ollama_memory.keep_alive_seconds_for("custom:unknown") == 33  # light fallback


# ---------------------------------------------------------------------------
# Ollama read endpoints must use GET (POST 405s)
# ---------------------------------------------------------------------------

def test_fetch_tags_uses_get(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = str(request.url.path)
        return httpx.Response(200, json={"models": [
            {"name": "qwen2.5:7b", "size": 3 * 1024 ** 3, "details": {"parameter_size": "7B"}},
        ]})

    async def fake_client():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://ollama"
        )

    monkeypatch.setattr(ollama_memory, "_client", fake_client)
    tags = asyncio.run(ollama_memory.fetch_tags())
    assert seen["method"] == "GET"
    assert seen["path"] == "/api/tags"
    assert tags[0].name == "qwen2.5:7b"
    assert tags[0].parameter_size == "7B"


def test_fetch_loaded_gb_uses_get(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = str(request.url.path)
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b", "size": 2 * 1024 ** 3}]})

    async def fake_client():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://ollama"
        )

    monkeypatch.setattr(ollama_memory, "_client", fake_client)
    loaded = asyncio.run(ollama_memory.fetch_loaded_gb())
    assert seen["method"] == "GET"
    assert seen["path"] == "/api/ps"
    assert loaded == {"qwen2.5:7b": pytest.approx(2.0)}


# ---------------------------------------------------------------------------
# Monitor tick
# ---------------------------------------------------------------------------

def test_monitor_tick_unloads_heavy_under_floor(monkeypatch):
    _patch_settings(monkeypatch)
    _patch_ram(monkeypatch, 0.8)
    unloaded: list[str] = []

    async def fake_tags():
        return [OllamaModelInfo("qwen2.5:7b", size_bytes=3 * 1024 ** 3)]

    async def fake_loaded():
        return {"qwen2.5:7b": 3.5}

    async def fake_unload(name: str) -> bool:
        unloaded.append(name)
        return True

    monkeypatch.setattr(ollama_memory, "fetch_tags", fake_tags)
    monkeypatch.setattr(ollama_memory, "fetch_loaded_gb", fake_loaded)
    monkeypatch.setattr(ollama_memory, "unload", fake_unload)

    monitor = ollama_memory.OllamaMemoryMonitor(interval_seconds=999, enabled=True)
    asyncio.run(monitor._check_once())
    assert unloaded == ["qwen2.5:7b"]
    assert monitor.last_unloaded == ["qwen2.5:7b"]
    assert monitor.events


def test_monitor_tick_healthy_does_not_unload(monkeypatch):
    _patch_settings(monkeypatch)
    _patch_ram(monkeypatch, 8.0)
    unloaded: list[str] = []

    async def fake_tags():
        return [OllamaModelInfo("qwen2.5:7b", size_bytes=3 * 1024 ** 3)]

    async def fake_loaded():
        return {"qwen2.5:7b": 3.5}

    async def fake_unload(name: str) -> bool:
        unloaded.append(name)
        return True

    monkeypatch.setattr(ollama_memory, "fetch_tags", fake_tags)
    monkeypatch.setattr(ollama_memory, "fetch_loaded_gb", fake_loaded)
    monkeypatch.setattr(ollama_memory, "unload", fake_unload)

    monitor = ollama_memory.OllamaMemoryMonitor(interval_seconds=999, enabled=True)
    asyncio.run(monitor._check_once())
    assert unloaded == []
    assert monitor.last_unloaded == []


# ---------------------------------------------------------------------------
# Router integration
# ---------------------------------------------------------------------------

def test_route_ollama_uses_light_model_when_ram_tight(monkeypatch):
    from app.services.model_router import ModelRouter
    settings = _patch_settings(monkeypatch)
    _patch_ram(monkeypatch, 1.0)
    router = ModelRouter(settings)
    assert router.route(provider_override="ollama").model == "qwen2.5:1.5b"


def test_route_ollama_uses_heavy_model_when_ram_ok(monkeypatch):
    from app.services.model_router import ModelRouter
    settings = _patch_settings(monkeypatch)
    _patch_ram(monkeypatch, 16.0)
    router = ModelRouter(settings)
    assert router.route(provider_override="ollama").model == "qwen2.5:7b"


def test_route_ollama_model_override_wins(monkeypatch):
    from app.services.model_router import ModelRouter
    settings = _patch_settings(monkeypatch)
    _patch_ram(monkeypatch, 0.1)
    router = ModelRouter(settings)
    route = router.route(provider_override="ollama", model_override="custom:tiny")
    assert route.model == "custom:tiny"