"""Model registry for local AI image generation (HuggingFace diffusers).

Provides a declarative catalog of diffusion-model specifications with their
pipeline class, HF model ID, recommended inference parameters, and hardware
requirements. The :func:`select_model` function picks the best tier based on
available system RAM and device type, enabling graceful degradation on
CPU-only / RAM-constrained machines.

All public functions are safe to call without torch/diffusers installed — they
return ``None`` or raise :class:`ModelNotAvailableError` so callers can fall
back to the procedural engine.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field

logger = logging.getLogger("jalora.creative.model_registry")


class ModelNotAvailableError(Exception):
    """Raised when a model cannot be loaded (missing package, insufficient RAM, etc.)."""


@dataclass(frozen=True)
class ModelSpec:
    """Specification for a local diffusion model.

    Mirrors the parameters accepted by the corresponding diffusers pipeline's
    ``__call__`` method, as verified against the diffusers v0.41.0 source:

    - ``StableDiffusionXLPipeline.__call__``: prompt, prompt_2, height, width,
      num_inference_steps (default 50), guidance_scale (default 5.0),
      negative_prompt, negative_prompt_2, num_images_per_prompt, generator,
      output_type ("pil"), original_size, crops_coords_top_left, target_size.

    - ``StableDiffusionImg2ImgPipeline.__call__``: prompt, image, strength (0.8),
      num_inference_steps (50), guidance_scale (7.5), negative_prompt, generator.

    - ``StableDiffusionXLInpaintPipeline.__call__``: prompt, prompt_2, image,
      mask_image, masked_image_latents, height, width, padding_mask_crop,
      strength (0.9999), num_inference_steps (50), denoising_start/end,
      guidance_scale (7.5), negative_prompt, generator.
    """

    name: str                          # short key used in config (e.g. "sdxl-lightning")
    model_id: str                      # HF model repo for the base pipeline
    pipeline_class: str                # e.g. "StableDiffusionXLPipeline"
    ram_gb_required: float            # minimum free RAM to attempt this model
    width: int = 1024
    height: int = 1024
    num_inference_steps: int = 4
    guidance_scale: float = 0.0
    torch_dtype: str = "float16"       # "float16" or "float32"
    variant: str = "fp16"              # HF variant ("fp16", "fp32", or "")
    scheduler: str = "euler"           # "euler", "dpm", "ddim", "euler_a"
    timestep_spacing: str = "trailing" # "trailing" or "leading"
    unet_lora: str | None = None       # HF repo::filename for Lightning/LCM UNet/LoRA override
    is_lora: bool = False              # True = LoRA adapter (load_lora_weights); False = UNet checkpoint (load_state_dict)
    description: str = ""              # human-readable description for logs/UI
    speed_seconds: float = 60.0        # estimated CPU generation time (seconds)


# --------------------------------------------------------------------------- #
#  Model catalogs
# --------------------------------------------------------------------------- #

# SDXL-Lightning: distilled from SDXL, generates 1024×1024 in 1-8 steps.
# From the ByteDance/SDXL-Lightning README: guidance_scale=0, EulerDiscrete
# scheduler with timestep_spacing="trailing". 4-step recommended for quality.
_SDXL_LIGHTNING_BASE = "stabilityai/stable-diffusion-xl-base-1.0"
_SDXL_LIGHTNING_REPO = "ByteDance/SDXL-Lightning"

MODEL_SPECS: dict[str, ModelSpec] = {
    # ------------------------------------------------------------------ #
    #  Tier 1: SDXL-Lightning (best quality, 1024×1024, needs 6 GB RAM)  #
    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    #  Tier 0: SDXL-Turbo (fastest, 1-step, 1024x1024, needs 6 GB RAM)    #
    #  1-step distilled (CFG 0). Best quality/speed on GPU; ONNX INT8 helps on CPU.
    # ------------------------------------------------------------------ #
    "sdxl-turbo-1step": ModelSpec(
        name="sdxl-turbo-1step",
        model_id="stabilityai/sdxl-turbo",
        pipeline_class="StableDiffusionXLPipeline",
        ram_gb_required=6.0,
        width=1024, height=1024,
        num_inference_steps=1,
        guidance_scale=0.0,
        scheduler="euler", timestep_spacing="trailing",
        description="SDXL-Turbo 1-step: 1024x1024, fastest distilled, ~15-30s on GPU",
        speed_seconds=20.0,
    ),
    # ------------------------------------------------------------------ #
    #  Tier 1: SDXL-Lightning (best quality, 1024x1024, needs 6 GB RAM)    #
    # ------------------------------------------------------------------ #
    "sdxl-lightning-2step": ModelSpec(
        name="sdxl-lightning-2step",
        model_id=_SDXL_LIGHTNING_BASE,
        pipeline_class="StableDiffusionXLPipeline",
        ram_gb_required=6.0,
        width=1024, height=1024,
        num_inference_steps=2,
        guidance_scale=0.0,
        scheduler="euler", timestep_spacing="trailing",
        unet_lora=f"{_SDXL_LIGHTNING_REPO}::sdxl_lightning_2step_unet.safetensors",
        description="SDXL-Lightning 2-step: 1024×1024, ~30-60s on CPU",
        speed_seconds=45.0,
    ),
    "sdxl-lightning-4step": ModelSpec(
        name="sdxl-lightning-4step",
        model_id=_SDXL_LIGHTNING_BASE,
        pipeline_class="StableDiffusionXLPipeline",
        ram_gb_required=6.0,
        width=1024, height=1024,
        num_inference_steps=4,
        guidance_scale=0.0,
        scheduler="euler", timestep_spacing="trailing",
        unet_lora=f"{_SDXL_LIGHTNING_REPO}::sdxl_lightning_4step_unet.safetensors",
        description="SDXL-Lightning 4-step: 1024×1024, best quality/speed, ~60-90s on CPU",
        speed_seconds=75.0,
    ),
    "sdxl-lightning-8step": ModelSpec(
        name="sdxl-lightning-8step",
        model_id=_SDXL_LIGHTNING_BASE,
        pipeline_class="StableDiffusionXLPipeline",
        ram_gb_required=6.0,
        width=1024, height=1024,
        num_inference_steps=8,
        guidance_scale=0.0,
        scheduler="euler", timestep_spacing="trailing",
        unet_lora=f"{_SDXL_LIGHTNING_REPO}::sdxl_lightning_8step_unet.safetensors",
        description="SDXL-Lightning 8-step: 1024×1024, highest quality, ~90-150s on CPU",
        speed_seconds=120.0,
    ),
    # ------------------------------------------------------------------ #
    #  Tier 2: SD 1.5 + LCM-LoRA (lower RAM, 512×512, needs 4 GB)       #
    # ------------------------------------------------------------------ #
    "sd15-lcm": ModelSpec(
        name="sd15-lcm",
        model_id="runwayml/stable-diffusion-v1-5",
        pipeline_class="StableDiffusionPipeline",
        ram_gb_required=2.0,
        width=512, height=512,
        num_inference_steps=4,
        guidance_scale=0.0,
        scheduler="euler", timestep_spacing="trailing",
        unet_lora="latent-consistency/lcm-lora-sdv1-5::pytorch_lora_weights.safetensors",
        is_lora=True,  # LCM-LoRA is a LoRA adapter, loaded via load_lora_weights
        description="SD 1.5 + LCM-LoRA 4-step: 512×512, CPU-friendly with attention slicing (~1-2 GB RAM)",
        speed_seconds=10.0,
    ),
    # ------------------------------------------------------------------ #
    #  Tier 3: SD 1.5 default (full quality, 512×512, needs 4 GB)       #
    # ------------------------------------------------------------------ #
    "sd15-default": ModelSpec(
        name="sd15-default",
        model_id="runwayml/stable-diffusion-v1-5",
        pipeline_class="StableDiffusionPipeline",
        ram_gb_required=4.0,
        width=512, height=512,
        num_inference_steps=20,
        guidance_scale=7.5,
        scheduler="euler_a", timestep_spacing="default",
        description="SD 1.5 default: 512×512, 20 steps, CFG 7.5 — CPU-friendly with low_cpu_mem_usage",
        speed_seconds=30.0,
    ),
}

# Convenience aliases — map short config names to specific specs
MODEL_ALIASES: dict[str, str] = {
    "sdxl-turbo": "sdxl-turbo-1step",
    "sdxl-lightning": "sdxl-lightning-4step",   # default to 4-step
    "sdxl-lightning-2": "sdxl-lightning-2step",
    "sdxl-lightning-4": "sdxl-lightning-4step",
    "sdxl-lightning-8": "sdxl-lightning-8step",
    "sd15-lcm": "sd15-lcm",
    "sd15-default": "sd15-default",
}


def resolve_model_name(alias: str) -> str:
    """Resolve a short config name to a concrete model spec key."""
    return MODEL_ALIASES.get(alias, alias)


def get_model_spec(name: str) -> ModelSpec | None:
    """Look up a model spec by exact name or alias."""
    if name in MODEL_SPECS:
        return MODEL_SPECS[name]
    resolved = resolve_model_name(name)
    return MODEL_SPECS.get(resolved)


def select_model(ram_available_gb: float, device: str = "auto") -> ModelSpec | None:
    """Select the best model tier based on available RAM and device.

    Args:
        ram_available_gb: Free system RAM in GB.
        device: "auto", "cpu", "cuda", "mps".

    Returns:
        The best-fit ModelSpec, or None if no model can fit.
    """
    if device == "auto":
        device = _detect_device()

    # GPU users can always try the full SDXL-Lightning tier
    gpu_ok = device in ("cuda", "mps")

    candidates = list(MODEL_SPECS.values())
    # Prefer higher-quality models (higher RAM requirement = better quality),
    # then fewer inference steps (Lightning/LCM are fast + distilled).
    candidates.sort(
        key=lambda m: (m.ram_gb_required, -m.num_inference_steps),
        reverse=True,
    )

    for spec in candidates:
        fits_ram = gpu_ok or ram_available_gb >= spec.ram_gb_required
        if fits_ram:
            logger.info("Selected model '%s' (device=%s, ram=%.1fGB)",
                         spec.name, device, ram_available_gb)
            return spec

    logger.warning("No local model fits available RAM (%.1f GB). "
                    "Use procedural fallback.", ram_available_gb)
    return None


def _detect_device() -> str:
    """Detect the best available torch device."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def get_device_torch(device: str, settings_device: str = "auto") -> str:
    """Resolve the device string for torch, honouring settings override."""
    if settings_device and settings_device != "auto":
        return settings_device
    if device == "auto":
        return _detect_device()
    return device


def can_load_torch() -> bool:
    """Check if torch is importable (without raising)."""
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


def can_load_diffusers() -> bool:
    """Check if diffusers is importable (without raising)."""
    try:
        import diffusers  # noqa: F401
        return True
    except ImportError:
        return False


def available_ram_gb() -> float:
    """Return available system RAM in GB."""
    try:
        import psutil
        return psutil.virtual_memory().available / (1024 ** 3)
    except ImportError:
        # Rough fallback using /proc or vmstat
        try:
            import os
            # Try /proc/meminfo (Linux) — won't work on Windows, but safe fallback
            if os.path.exists("/proc/meminfo"):
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemAvailable:"):
                            return int(line.split()[1]) / (1024 ** 2)
        except Exception:
            pass
        return 4.0  # conservative default


def get_aspect_sizes(width: int, height: int) -> dict[str, tuple[int, int]]:
    """Return common SDXL-compatible aspect ratio -> (width, height) mappings
    that are multiples of 8 (required by the U-Net)."""
    return {
        "1:1": (1024, 1024),
        "16:9": (1152, 896),     # closest to 16:9 that's a multiple of 8
        "9:16": (896, 1152),
        "4:3": (1152, 896),      # same as 16:9; SDXL uses 1024x768 rounded
        "3:4": (896, 1152),
        "3:2": (1216, 832),
        "2:3": (832, 1216),
    }


def can_load_rembg() -> bool:
    """Check if the *rembg* package is importable for background removal."""
    try:
        import rembg  # noqa: F401
        return True
    except ImportError:
        return False
