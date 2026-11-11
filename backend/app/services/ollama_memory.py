"""Memory-aware Ollama model loading.

Ollama keeps loaded models resident and allocates their GGML buffers from RAM.
When free memory is short, llama-server dies with errors like
``ggml_backend_cpu_buffer_type_alloc_buffer: failed to allocate buffer ...``
which surfaces as ``ollama API error 500`` from the app. To make local model
loading safe on memory-constrained machines this module:

* peeks at the real free RAM (``psutil``) and at Ollama's own /api/tags and
  /api/ps views;
* answers "which model should I load next?" (``pick_ollama_model``) using a
  four-tier ladder: capable model -> mid-tier model -> lightweight model ->
  ultra-light survival model, picking the heaviest that fits in the free RAM
  minus the resident footprint of the other already-loaded tier models;
* runs a small background monitor that periodically refreshes the snapshot and
  unloads heavy models as soon as free RAM falls below a safe floor, so later
  requests automatically fall back to the lightweight model instead of crashing.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.config import get_settings

logger = logging.getLogger("jalora.ollama_memory")

_GB = 1024 ** 3

MONITOR_INTERVAL_SECONDS = 15.0
_LOAD_OVERHEAD_FACTOR = 1.15  # GGML file size -> roughly resident footprint

# Ollama exposes these over GET (POST returns 405).
_GET_ENDPOINTS = ("/api/tags", "/api/ps", "/api/version")


@dataclass(frozen=True)
class OllamaModelInfo:
    name: str
    size_bytes: int
    parameter_size: str = ""  # e.g. "7B", "1.5B" (from details)

    @property
    def size_gb(self) -> float:
        return self.size_bytes / _GB


@dataclass
class OllamaSnapshot:
    tags: dict[str, OllamaModelInfo] = field(default_factory=dict)
    loaded: dict[str, float] = field(default_factory=dict)  # name -> resident GB
    updated_at: float = 0.0
    healthy: bool = False  # False when Ollama was unreachable


@dataclass
class ModelChoice:
    model: str
    reason: str  # "memory-ok" | "low-memory" | "low-memory-forced" | "single-model" | "override"


# --------------------------------------------------------------------------
# RAM helpers
# --------------------------------------------------------------------------

def available_memory_gb() -> float:
    """Free/available RAM in GiB. Returns +inf if it cannot be measured so a
    broken probe never blocks the capable model (callers still cap via the
    configured thresholds)."""
    try:
        import psutil
        return psutil.virtual_memory().available / _GB
    except Exception:  # noqa: BLE001 - measurement must never crash routing
        logger.warning("could not measure free RAM with psutil; using +inf")
        return float("inf")


# --------------------------------------------------------------------------
# Model tier helpers
# --------------------------------------------------------------------------

def model_tiers(survival: bool = True) -> list[str]:
    """Heaviest -> lightest local model names (survival tier optional)."""
    settings = get_settings()
    heavy = settings.ollama_model
    mid = settings.ollama_mid_model
    light = settings.ollama_light_model or heavy
    tiers = [heavy]
    if mid and mid not in tiers:
        tiers.append(mid)
    if light not in tiers:
        tiers.append(light)
    if survival:
        survival_model = settings.ollama_survival_model
        if survival_model and survival_model not in tiers:
            tiers.append(survival_model)
    return tiers


def tier_requirement_gb(model: str) -> float:
    """Minimum free RAM to attempt a model (installed file size preferred)."""
    settings = get_settings()
    need = max(settings.ollama_light_model_min_free_gb, 0.4)
    if model == settings.ollama_model:
        need = max(settings.ollama_heavy_model_min_free_gb, 1.0)
    elif model == settings.ollama_mid_model:
        need = max(settings.ollama_mid_model_min_free_gb, 0.6)
    elif model == settings.ollama_survival_model:
        need = max(settings.ollama_survival_model_min_free_gb, 0.2)
    info = _get_snapshot().tags.get(model)
    if info is not None and info.size_gb > 0:
        need = max(need, estimate_resident_gb(info, need))
    return need


# --------------------------------------------------------------------------
# Ollama HTTP helpers
# --------------------------------------------------------------------------

async def _client() -> httpx.AsyncClient:
    from app.config import get_settings as _get
    base = _get().ollama_base_url.rstrip("/")
    return httpx.AsyncClient(base_url=base, timeout=10.0)


async def fetch_tags() -> list[OllamaModelInfo]:
    """Installed models from GET /api/tags with their file sizes."""
    async with await _client() as c:
        resp = await c.get("/api/tags")
        resp.raise_for_status()
        payload = resp.json()
    models: list[OllamaModelInfo] = []
    for item in payload.get("models", []):
        details = item.get("details", {}) or {}
        models.append(
            OllamaModelInfo(
                name=str(item.get("name", "")),
                size_bytes=int(item.get("size", 0) or 0),
                parameter_size=str(details.get("parameter_size", "") or ""),
            )
        )
    return [m for m in models if m.name]


async def fetch_loaded_gb() -> dict[str, float]:
    """Currently resident model names -> approximate resident size in GiB."""
    async with await _client() as c:
        try:
            resp = await c.get("/api/ps")
            resp.raise_for_status()
            payload = resp.json()
        except Exception:  # noqa: BLE001 - /api/ps may be missing on old versions
            logger.debug("ollama /api/ps unavailable; treating nothing as loaded")
            return {}
    loaded: dict[str, float] = {}
    for item in payload.get("models", []):
        name = str(item.get("name", ""))
        if not name:
            continue
        resident = int(item.get("size", 0) or item.get("size_vram", 0) or 0) / _GB
        loaded[name] = resident
    return loaded


async def unload(model: str) -> bool:
    """Ask Ollama to unload a resident model (keep_alive 0) — freed RAM becomes
    available to the OS without touching the stored weights."""
    if not model:
        return False
    try:
        async with await _client() as c:
            resp = await c.post(
                "/api/generate",
                json={"model": model, "prompt": "", "keep_alive": 0},
            )
            resp.raise_for_status()
        logger.info("ollama unloaded model '%s'", model)
        return True
    except Exception as exc:  # noqa: BLE001 - never crash the monitor
        logger.warning("failed to unload ollama model '%s': %s", model, exc)
        return False


async def set_keep_alive(model: str, seconds: float) -> bool:
    """Refresh a resident model's keep-alive window via /api/generate with an
    empty prompt: generates no tokens but keeps the weights pinned in RAM so
    the next request skips the (slow) cold load."""
    if not model:
        return False
    try:
        async with await _client() as c:
            resp = await c.post(
                "/api/generate",
                json={"model": model, "prompt": "", "keep_alive": seconds},
            )
            resp.raise_for_status()
        logger.debug("ollama keep_alive refreshed for '%s' (+%.0fs)", model, seconds)
        return True
    except Exception as exc:  # noqa: BLE001 - best-effort residency refresh
        logger.debug("failed to refresh ollama keep_alive for '%s': %s", model, exc)
        return False


async def warm(model: str, keep_alive_seconds: float) -> bool:
    """Pre-load a model so the first real request is not slowed by a cold load.
    No-op when the model is already resident. Best-effort: never raises."""
    if not model:
        return False
    if model in _get_snapshot().loaded:
        return True
    return await set_keep_alive(model, keep_alive_seconds)


def keep_alive_seconds_for(model: str) -> int:
    """How many seconds to keep a tier resident after a request (per-tier)."""
    settings = get_settings()
    if model == settings.ollama_model:
        return settings.ollama_keep_alive_heavy_seconds
    if model == settings.ollama_mid_model:
        return settings.ollama_keep_alive_mid_seconds
    if model == settings.ollama_survival_model:
        return settings.ollama_keep_alive_survival_seconds
    return settings.ollama_keep_alive_light_seconds


# --------------------------------------------------------------------------
# Decision helpers (pure; unit-tested)
# --------------------------------------------------------------------------

def estimate_resident_gb(info: OllamaModelInfo | None, fallback_gb: float) -> float:
    if info is not None and info.size_gb > 0:
        return info.size_gb * _LOAD_OVERHEAD_FACTOR
    return fallback_gb


def pick_ollama_model(model_override: Optional[str] = None) -> ModelChoice:
    """Pick the heaviest local model that fits the current free RAM.

    Ladder: capable model -> mid-tier model -> lightweight model -> ultra-light
    survival model. The per-candidate budget subtracts the resident footprint of
    the other already-loaded tier models (from /api/ps) so concurrently resident
    models cannot pile up and OOM-crash llama-server.
    """
    if model_override:
        return ModelChoice(model_override, "override")
    settings = get_settings()
    tiers = model_tiers()
    if not tiers:
        return ModelChoice(settings.ollama_model, "single-model")
    if len(tiers) == 1:
        return ModelChoice(tiers[0], "single-model")

    free_gb = available_memory_gb()
    snapshot = _get_snapshot()
    tier_names = set(tiers)
    for model in tiers:
        # RAM pinned by other tier models that stay resident is not available
        # for a fresh load of this candidate.
        pinned_by_others = sum(
            gb
            for name, gb in snapshot.loaded.items()
            if name in tier_names and name != model
        )
        budget = max(free_gb - pinned_by_others, 0.0)
        if budget >= tier_requirement_gb(model):
            reason = "memory-ok" if model == settings.ollama_model else "low-memory"
            if snapshot.tags and model not in snapshot.tags:
                logger.warning("ollama model '%s' not installed; run `ollama pull %s`", model, model)
            return ModelChoice(model, reason)

    survival = tiers[-1]
    need = tier_requirement_gb(survival)
    logger.warning(
        "free RAM %.1fGB is below even the survival model floor (%.1fGB); "
        "using '%s' anyway (load may fail until RAM is freed)",
        free_gb, need, survival,
    )
    return ModelChoice(survival, "low-memory-forced")


def names_to_unload(
    free_gb: float,
    loaded_gb: dict[str, float],
    heavy: str,
    light: str,
    floor_gb: float,
    survival: Optional[str] = None,
    mid: Optional[str] = None,
) -> list[str]:
    """Names of resident models to unload when free RAM is under the safe floor.

    Heavy model is unloaded first (biggest gain); other non-critical models are
    released next. The mid/lightweight and survival models are never unloaded —
    they keep the app responsive under pressure. Empty when memory is healthy."""
    if free_gb >= floor_gb:
        return []
    keep = {heavy, light, survival or "", mid or ""}
    order = sorted(loaded_gb, key=lambda n: -loaded_gb[n])
    prioritized = [n for n in order if n == heavy]
    others = [n for n in order if n not in keep]
    return prioritized + others


# --------------------------------------------------------------------------
# Background monitor
# --------------------------------------------------------------------------

class OllamaMemoryMonitor:
    """Periodically refreshes the Ollama snapshot and unloads heavy models when
    free RAM drops below the safe floor."""

    def __init__(
        self,
        interval_seconds: float = MONITOR_INTERVAL_SECONDS,
        enabled: Optional[bool] = None,
    ) -> None:
        settings = get_settings()
        self.interval_seconds = interval_seconds
        self.enabled = (
            settings.ollama_memory_monitor if enabled is None else enabled
        )
        self.healthy: bool = False
        self.last_check: float = 0.0
        self.last_unloaded: list[str] = []
        self.events: list[str] = []
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if not self.enabled:
            logger.info("ollama memory monitor disabled")
            return
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="ollama-memory-monitor")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=5.0)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            self._task.cancel()
        self._task = None

    # -- state ------------------------------------------------------------

    def snapshot(self) -> dict:
        _snap = _get_snapshot()
        return {
            "enabled": self.enabled,
            "healthy": self.healthy and _snap.healthy,
            "last_check": self.last_check,
            "free_gb": available_memory_gb(),
            "heavy_model": get_settings().ollama_model,
            "mid_model": get_settings().ollama_mid_model,
            "light_model": get_settings().ollama_light_model,
            "survival_model": get_settings().ollama_survival_model,
            "loaded": _snap.loaded,
            "installed": sorted(_snap.tags),
            "last_unloaded": self.last_unloaded,
            "events": self.events[-20:],
        }

    # -- logic ------------------------------------------------------------

    async def _run(self) -> None:
        logger.info("ollama memory monitor started (every %.0fs)", self.interval_seconds)
        while not self._stop.is_set():
            try:
                await self._check_once()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001 - a bad probe must not kill the loop
                logger.exception("ollama memory monitor tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _check_once(self) -> None:
        import time
        settings = get_settings()
        free_gb = available_memory_gb()
        try:
            tags = await fetch_tags()
            loaded = await fetch_loaded_gb()
            self.healthy = True
            _set_snapshot(
                tags={t.name: t for t in tags}, loaded=loaded, healthy=True
            )
        except Exception as exc:  # noqa: BLE001 - Ollama offline is fine
            logger.info("ollama unreachable during monitor tick: %s", exc)
            _set_snapshot(tags={}, loaded={}, healthy=False)
            self.healthy = False
        self.last_check = time.time()
        self.last_unloaded = []
        names = names_to_unload(
            free_gb,
            _get_snapshot().loaded,
            settings.ollama_model,
            settings.ollama_light_model,
            settings.ollama_safe_ram_floor_gb,
            settings.ollama_survival_model,
            mid=settings.ollama_mid_model,
        )
        for name in names:
            if await unload(name):
                self.last_unloaded.append(name)
        if names:
            self.events.append(
                f"{time.strftime('%H:%M:%S')} free RAM {free_gb:.1f}GB "
                f"< floor {settings.ollama_safe_ram_floor_gb:.1f}GB; unloaded {', '.join(names)}"
            )
            logger.warning(
                "low memory: %.1fGB free, unloaded %s to prevent OOM crash",
                free_gb, ", ".join(names) or "nothing",
            )


# --------------------------------------------------------------------------
# Module-level snapshot + singleton
# --------------------------------------------------------------------------

_snapshot = OllamaSnapshot()

_shared_monitor: Optional[OllamaMemoryMonitor] = None


def _get_snapshot() -> OllamaSnapshot:
    return _snapshot


def _set_snapshot(
    tags: Optional[dict[str, OllamaModelInfo]] = None,
    loaded: Optional[dict[str, float]] = None,
    healthy: bool = True,
) -> None:
    global _snapshot
    if tags is not None:
        _snapshot.tags = tags
    if loaded is not None:
        _snapshot.loaded = loaded
    _snapshot.healthy = healthy
    import time as _t
    _snapshot.updated_at = _t.time()


def get_monitor() -> OllamaMemoryMonitor:
    global _shared_monitor
    if _shared_monitor is None:
        _shared_monitor = OllamaMemoryMonitor()
    monitor = _shared_monitor
    assert monitor is not None  # set above; keeps the static checker happy
    return monitor