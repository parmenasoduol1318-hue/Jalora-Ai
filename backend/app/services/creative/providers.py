"""Provider-independent AI generation layer for the Creative Studio.

Consistent interfaces for image, video and audio generation so the frontend and
core project logic never depend on a specific vendor. Providers are selected by
config (``image_provider`` / ``video_provider`` / ``audio_provider``) with an
``auto`` mode that prefers a cloud provider with a configured key and otherwise
falls back to an offline, clearly-labelled *local* engine.

The local engines are genuine media producers (procedural imagery, Ken Burns
motion, synthesised ambience) but are **not** AI models — their outputs carry
``ai: false`` metadata so the UI can label them honestly. Cloud providers raise
:class:`CreativeEngineError` with a precise code (``PROVIDER_QUOTA``,
``PROVIDER_CREDITS``, ``PROVIDER_AUTH``...) when they cannot serve a request.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import numpy as np

from app.config import Settings, get_settings
from app.services.creative import CreativeEngineError

logger = logging.getLogger("jalora.creative.providers")

# Error codes that trigger fallback in video/audio paths.
# Images use AI-only provider failover so abstract procedural output is not
# returned as if it represented the user's requested scene.
FALLBACK_CODES = frozenset({
    "PROVIDER_QUOTA", "PROVIDER_CREDITS", "PROVIDER_AUTH", "PROVIDER_ERROR",
    "PROVIDER_TIMEOUT", "MODEL_UNAVAILABLE", "PROVIDER_NOT_CONFIGURED",
})

GOOGLE_BASE = "https://generativelanguage.googleapis.com/v1beta"
OPENAI_IMAGE_SIZES = {
    "1:1": "1024x1024", "16:9": "1536x1024", "9:16": "1024x1536",
    "4:3": "1536x1024", "3:4": "1024x1536", "3:2": "1536x1024", "2:3": "1024x1536",
}
OPENAI_DALLE_SIZES = {
    "1:1": "1024x1024", "16:9": "1792x1024", "9:16": "1024x1792",
    "4:3": "1792x1024", "3:4": "1024x1792",
}


@dataclass
class GenRequest:
    kind: str  # image | video | audio
    prompt: str
    negative_prompt: str | None = None
    aspect_ratio: str = "16:9"
    duration: float = 5.0
    seed: int | None = None
    num_variations: int = 1
    reference_image: Path | None = None  # image-to-image / motion source
    mask_image: Path | None = None       # inpainting
    voice: str | None = None             # audio/TTS
    model: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class GenOutput:
    data: bytes
    extension: str
    mime: str
    meta: dict = field(default_factory=dict)


def _map_error(exc: Exception, provider: str) -> CreativeEngineError:
    text = str(exc).lower()
    if "credit" in text or "insufficient_quota" in text or "quota" in text or "429" in text:
        code = "PROVIDER_QUOTA"
        msg = (f"{provider} cannot serve this request: quota or credits exhausted. "
               f"Add billing/credits or use the local engine.")
    elif "401" in text or "unauthorized" in text or "invalid api key" in text:
        code, msg = "PROVIDER_AUTH", f"{provider} rejected the configured API key."
    elif "model" in text and ("not found" in text or "does not exist" in text):
        code, msg = "MODEL_UNAVAILABLE", f"{provider} does not offer the requested model."
    elif "404" in text:
        # HTTP 404 most commonly means the requested model/endpoint doesn't
        # exist on the provider side (e.g. Sora preview not available).
        # This is a fallback-eligible error — try the local engine.
        code, msg = "MODEL_UNAVAILABLE", f"{provider} returned HTTP 404 — the requested model may not be available."
    elif "timeout" in text:
        code, msg = "PROVIDER_TIMEOUT", f"{provider} timed out."
    else:
        code, msg = "PROVIDER_ERROR", f"{provider} generation failed: {exc}"
    return CreativeEngineError(msg, code, provider=provider)


def validate_image_output(data: bytes, provider: str = "image",
                          min_quality: bool = False) -> dict | None:
    """QA validation: ensure a generated image is valid, non-empty, and above
    a minimum size threshold.

    Raises :class:`CreativeEngineError` with code ``EMPTY_OUTPUT`` if the image
    data is empty, corrupt, or too small to be a real result.  When
    *min_quality* is True (AI generation paths only), also runs the
    :func:`~app.services.creative.image_quality.assess_image_quality` gate and
    raises ``LOW_QUALITY`` if the image is a flat colour or structurally
    degenerate.

    Args:
        data: Raw image bytes (PNG, JPEG, etc.).
        provider: Provider name for error context.
        min_quality: If True, enforce quality thresholds (colour variance,
            entropy, edge density, distinct colours).  Defaults to False so the
            procedural fallback — which intentionally produces abstract art —
            is not rejected.

    Returns:
        Quality metrics dict (from
        :func:`~app.services.creative.image_quality.assess_image_quality`)
        when *min_quality* is True, otherwise None.
    """
    if not data or len(data) < 100:
        raise CreativeEngineError(
            f"{provider} returned an image that is too small or empty "
            f"({len(data) if data else 0} bytes). The model may have failed "
            f"silently — try a different prompt or provider.",
            "EMPTY_OUTPUT", provider=provider,
        )
    # Validate via PIL — catches corrupted/truncated image data
    from PIL import Image as _PILImage
    import io as _io
    try:
        img = _PILImage.open(_io.BytesIO(data))
        img.verify()  # verify() frees the image; need to re-open to access
    except Exception as exc:
        raise CreativeEngineError(
            f"{provider} returned invalid/corrupt image data: {exc}",
            "EMPTY_OUTPUT", provider=provider,
        ) from exc

    # Optional quality gate for AI-generated outputs
    if min_quality:
        from app.services.creative.image_quality import assess_image_quality
        metrics = assess_image_quality(data)
        if not metrics["passes_qa"]:
            raise CreativeEngineError(
                f"{provider} returned a low-quality image "
                f"(score={metrics['quality_score']}/100, "
                f"variance={metrics['color_variance']}, "
                f"entropy={metrics['entropy']}, "
                f"edges={metrics['edge_density']}). "
                f"The model may have produced a degenerate output.",
                "LOW_QUALITY", provider=provider,
            )
        return metrics
    return None


# ============================================================ image providers

class ImageProvider:
    name = "base"
    ai = True
    capabilities: dict = {}

    def configured(self) -> bool:
        return False

    def generate(self, req: GenRequest) -> list[GenOutput]:
        raise NotImplementedError

    def estimate_cost(self, req: GenRequest) -> float | None:
        return None


class OpenAIImageProvider(ImageProvider):
    name = "openai"
    capabilities = {"text_to_image": True, "image_to_image": True, "inpainting": True,
                    "variations": True, "transparent_bg": True, "negative_prompt": False,
                    "seed": False, "max_variations": 4}

    def __init__(self, settings: Settings):
        self.settings = settings

    def configured(self) -> bool:
        return bool(self.settings.openai_api_key)

    def _client(self):
        from openai import OpenAI
        return OpenAI(api_key=self.settings.openai_api_key,
                      base_url=self.settings.openai_base_url)

    def generate(self, req: GenRequest) -> list[GenOutput]:
        if not self.configured():
            raise CreativeEngineError("OpenAI is not configured.", "PROVIDER_NOT_CONFIGURED",
                                      provider=self.name)
        model = req.model or self.settings.image_model or "gpt-image-1"
        size = (OPENAI_DALLE_SIZES if model.startswith("dall-e") else OPENAI_IMAGE_SIZES).get(
            req.aspect_ratio, "1024x1024")
        # Enhance prompts using prompt_engine for better DALL·E / GPT-Image results
        try:
            from app.services.creative import prompt_engine
            display_prompt = prompt_engine.format_prompt(req.prompt, req.aspect_ratio)
        except Exception:
            display_prompt = req.prompt
        try:
            client = self._client()
            outputs: list[GenOutput] = []
            if req.reference_image and req.mask_image:
                kwargs = dict(model=model, prompt=display_prompt, size=size, n=1)
                with open(req.reference_image, "rb") as img, open(req.mask_image, "rb") as mask:
                    resp = client.images.edit(image=img, mask=mask, **kwargs)
            elif req.reference_image:
                with open(req.reference_image, "rb") as img:
                    resp = client.images.edit(model=model, image=img, prompt=display_prompt, size=size, n=req.num_variations)
            else:
                resp = client.images.generate(model=model, prompt=display_prompt, size=size,
                                              n=req.num_variations)
            for item in resp.data:
                if getattr(item, "b64_json", None):
                    img_data = base64.b64decode(item.b64_json)
                    # QA: validate + quality gate before accepting
                    qm = validate_image_output(img_data, provider=self.name, min_quality=True)
                    outputs.append(GenOutput(img_data, ".png", "image/png",
                                             {"ai": True, "provider": self.name, "model": model,
                                              **(qm or {})}))
                elif getattr(item, "url", None):
                    r = httpx.get(item.url, timeout=120)
                    r.raise_for_status()
                    validate_image_output(r.content, provider=self.name, min_quality=True)
                    outputs.append(GenOutput(r.content, ".png", "image/png",
                                             {"ai": True, "provider": self.name, "model": model}))
            if not outputs:
                raise CreativeEngineError("OpenAI returned no image data.", "EMPTY_OUTPUT",
                                          provider=self.name)
            return outputs
        except CreativeEngineError:
            raise
        except Exception as exc:
            raise _map_error(exc, self.name) from exc

    def estimate_cost(self, req: GenRequest) -> float | None:
        return 0.04 * max(1, req.num_variations)


class GoogleImageProvider(ImageProvider):
    name = "google"
    capabilities = {"text_to_image": True, "image_to_image": True, "inpainting": False,
                    "variations": True, "transparent_bg": False, "negative_prompt": False,
                    "seed": False, "max_variations": 4}

    def __init__(self, settings: Settings):
        self.settings = settings

    def configured(self) -> bool:
        return bool(self.settings.google_api_key)

    def generate(self, req: GenRequest) -> list[GenOutput]:
        if not self.configured():
            raise CreativeEngineError("Google AI is not configured.", "PROVIDER_NOT_CONFIGURED",
                                      provider=self.name)
        model = req.model or self.settings.image_model or "gemini-2.5-flash-image"
        parts: list[dict] = []
        prompt = req.prompt
        if req.negative_prompt:
            prompt += f"\nAvoid: {req.negative_prompt}"
        prompt += f"\nCompose for a {req.aspect_ratio} aspect ratio."
        parts.append({"text": prompt})
        if req.reference_image:
            mime = _guess_mime(req.reference_image)
            parts.append({"inlineData": {"mimeType": mime,
                                         "data": base64.b64encode(req.reference_image.read_bytes()).decode()}})
        body = {"contents": [{"parts": parts}],
                "generationConfig": {"responseModalities": ["IMAGE"],
                                     "imageConfig": {"aspectRatio": req.aspect_ratio}}}
        try:
            responses = []
            for _ in range(max(1, min(req.num_variations, 4))):
                r = httpx.post(f"{GOOGLE_BASE}/models/{model}:generateContent",
                               params={"key": self.settings.google_api_key}, json=body, timeout=180)
                if r.status_code != 200:
                    raise CreativeEngineError(_google_err(r), _google_code(r.status_code), provider=self.name)
                responses.append(r.json())
        except CreativeEngineError:
            raise
        except Exception as exc:
            raise _map_error(exc, self.name) from exc
        outputs: list[GenOutput] = []
        for data in responses:
            for cand in data.get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    inline = part.get("inlineData") or part.get("inline_data")
                    if inline and inline.get("data"):
                        mime = inline.get("mimeType", "image/png")
                        ext = ".png" if "png" in mime else ".jpg"
                        img_data = base64.b64decode(inline["data"])
                        qm = validate_image_output(img_data, provider=self.name, min_quality=True)
                        outputs.append(GenOutput(img_data, ext, mime,
                                                 {"ai": True, "provider": self.name, "model": model,
                                                  **(qm or {})}))
        if not outputs:
            raise CreativeEngineError("Google returned no image data.", "EMPTY_OUTPUT", provider=self.name)
        return outputs

    def estimate_cost(self, req: GenRequest) -> float | None:
        return 0.039


class LocalImageProvider(ImageProvider):
    """Local image generation with real AI diffusion (Stable Diffusion via diffusers)
    and a procedural abstract-art fallback.

    When torch + diffusers are installed and system RAM permits, this provider
    downloads a lightweight checkpoint (e.g. SDXL-Lightning) from HuggingFace and
    runs real text-to-image / img2img / inpainting inference — even on CPU.

    When torch/diffusers are *not* installed or the system can't fit the model,
    it transparently degrades to the procedural abstract-art engine so the user
    always gets an image (though not a recognisable one).
    """

    name = "local"
    ai = True  # True when the AI path is active; procedural fallback still sets ai=False in meta
    capabilities = {"text_to_image": True, "image_to_image": True, "inpainting": True,
                    "variations": True, "transparent_bg": True, "negative_prompt": True,
                    "seed": True, "max_variations": 4, "procedural": True}

    def configured(self) -> bool:
        return True

    def generate(self, req: GenRequest) -> list[GenOutput]:
        # Try the real AI diffusion path first
        outputs = self._ai_generate(req)
        if outputs:
            return outputs
        # Fallback to the procedural engine
        return self._procedural_generate(req)

    # --------------------------------------------------------------- AI path

    def _ai_generate(self, req: GenRequest) -> list[GenOutput] | None:
        """Attempt AI image generation via HuggingFace diffusers.

        Returns a list of GenOutput if successful, or None if the AI path
        is unavailable (torch not installed, insufficient RAM, model
        download failed) — in which case the caller falls back to procedural.
        """
        from app.services.creative import model_registry, prompt_engine

        # Graceful degradation: if torch/diffusers aren't installed, return None
        # so the caller uses the procedural fallback.
        if not (model_registry.can_load_torch() and model_registry.can_load_diffusers()):
            logger.debug("torch/diffusers not installed — using procedural fallback")
            return None

        # Select the best model tier for available RAM / device
        settings = get_settings()

        ram_gb = model_registry.available_ram_gb()
        device_str = model_registry.get_device_torch("auto", settings.image_local_device)

        # If a specific model is configured, try it first — but if it doesn't
        # fit in available RAM, fall through to auto-selection of a smaller tier
        # rather than silently giving up.
        spec = None
        if settings.image_local_model:
            spec = model_registry.get_model_spec(settings.image_local_model)
            if spec is not None and spec.ram_gb_required > ram_gb:
                logger.warning(
                    "Configured model '%s' requires %.1f GB RAM but only %.1f GB "
                    "available — trying auto-selection for a smaller model",
                    spec.name, spec.ram_gb_required, ram_gb,
                )
                spec = None  # fall through to select_model

        if spec is None:
            spec = model_registry.select_model(ram_gb, device_str)

        if spec is None:
            logger.warning("No local model fits available RAM (%.1f GB) — "
                           "using procedural fallback", ram_gb)
            return None

        # If a specific model was requested but it doesn't fit available RAM,
        # degrade to the procedural fallback instead of attempting to download
        # and OOM-crash.
        if spec.ram_gb_required > ram_gb:
            logger.warning(
                "Model '%s' requires %.1f GB RAM but only %.1f GB available — "
                "using procedural fallback", spec.name, spec.ram_gb_required, ram_gb,
            )
            return None

        # Determine the generation mode
        if req.reference_image and req.mask_image:
            mode = "inpaint"
        elif req.reference_image:
            mode = "img2img"
        else:
            mode = "txt2img"

        try:
            pipe = self._load_pipeline(spec, device_str, settings.huggingface_token, mode)
        except Exception as e:
            logger.warning("Failed to load model '%s': %s — falling back to procedural",
                           spec.name, e)
            return None

        # Build enhanced prompt using structured type-aware templates
        image_type = req.extra.get("image_type") if req.extra else None
        enhanced = prompt_engine.format_prompt(
            req.prompt, req.aspect_ratio, image_type=image_type,
        )
        neg_prompt = prompt_engine.build_negative_prompt(req.negative_prompt)
        w, h = self._resolve_size(req, spec)

        outputs: list[GenOutput] = []
        num_variations = max(1, min(req.num_variations, 4))
        for i in range(num_variations):
            seed = req.seed if req.seed is not None else (i * 12345 + 1)
            try:
                out = self._pipeline_call(
                    pipe, spec, mode, req, enhanced, neg_prompt,
                    w, h, seed, device_str,
                )
                if out:
                    outputs.extend(out)
            except Exception as e:
                logger.warning("Generation failed for seed %d: %s", seed, e)
                if i == 0 and not outputs:
                    # First attempt failed and no partial output — fall back
                    return None
        return outputs if outputs else None

    def _load_pipeline(self, spec, device: str, hf_token: str | None, mode: str = "txt2img"):
        """Load (and cache) a diffusers pipeline for the given mode,
        applying Lightning/LCM UNet/LoRA overrides.

        Cached per (model_name, device, mode) so img2img/inpaint pipelines
        with the same checkpoint are only loaded once.
        """
        import torch
        from huggingface_hub import hf_hub_download, login
        from safetensors.torch import load_file
        from diffusers import (
            StableDiffusionXLPipeline,
            StableDiffusionXLImg2ImgPipeline,
            StableDiffusionXLInpaintPipeline,
            StableDiffusionPipeline,
            StableDiffusionImg2ImgPipeline,
            StableDiffusionInpaintPipeline,
            EulerDiscreteScheduler,
            EulerAncestralDiscreteScheduler,
            DPMSolverMultistepScheduler,
        )

        is_sdxl = "XL" in spec.pipeline_class

        # Select the correct pipeline class for (model family × mode)
        if mode == "txt2img":
            pipe_cls = StableDiffusionXLPipeline if is_sdxl else StableDiffusionPipeline
        elif mode == "img2img":
            pipe_cls = (StableDiffusionXLImg2ImgPipeline
                        if is_sdxl else StableDiffusionImg2ImgPipeline)
        elif mode == "inpaint":
            pipe_cls = (StableDiffusionXLInpaintPipeline
                        if is_sdxl else StableDiffusionInpaintPipeline)
        else:
            pipe_cls = StableDiffusionXLPipeline if is_sdxl else StableDiffusionPipeline

        cache_key = f"{spec.name}:{device}:{mode}"
        cached = _PIPELINE_CACHE.get(cache_key)
        if cached is not None:
            return cached

        # Authenticate if token provided (needed for some gated inpainting models)
        if hf_token:
            try:
                login(token=hf_token)
            except Exception:
                pass

        # Choose torch dtype — on CPU, float16 is poorly supported (slow,
        # limited ops). Force float32 on CPU unless the model spec explicitly
        # requests float16 (e.g. SDXL-Lightning fp16 variant). Even when
        # float16 is requested, we keep it only for GPU; CPU gets float32 for
        # correctness, with low_cpu_mem_usage to avoid peak during loading.
        if device == "cpu":
            dtype = torch.float32
        elif spec.torch_dtype == "float16":
            dtype = torch.float16
        else:
            dtype = torch.float32

        # Build from_pretrained kwargs.  On CPU, low_cpu_mem_usage=True +
        # skipping the safety checker can save ~1 GB of RAM.
        fp_kwargs: dict = {
            "torch_dtype": dtype,
            "variant": spec.variant if spec.variant else None,
            "use_safetensors": True,
        }
        if device == "cpu":
            fp_kwargs["low_cpu_mem_usage"] = True
            fp_kwargs["safety_checker"] = None  # type: ignore[arg-type]

        # Load the base pipeline
        pipe = pipe_cls.from_pretrained(
            spec.model_id,
            **fp_kwargs,
        )

        # Apply Lightning UNet or LCM-LoRA override if specified.
        # is_lora=False -> full UNet replacement (Lightning checkpoint, loaded
        #   via load_state_dict on pipe.unet).
        # is_lora=True  -> LoRA adapter (LCM-LoRA, loaded via load_lora_weights
        #   + fuse_lora for speed).
        if spec.unet_lora:
            if "::" in spec.unet_lora:
                repo, filename = spec.unet_lora.split("::", 1)
            else:
                repo, filename = spec.unet_lora, None

            if spec.is_lora and filename:
                # LoRA adapter (LCM-LoRA etc.)
                lora_path = hf_hub_download(repo, filename)
                pipe.load_lora_weights(lora_path)
                pipe.fuse_lora()
            elif filename and filename.endswith(".safetensors"):
                # Full UNet replacement (Lightning)
                unet_path = hf_hub_download(repo, filename)
                pipe.unet.load_state_dict(
                    load_file(unet_path, device="cpu")
                )
            else:
                logger.warning("Could not load override '%s' for model '%s'",
                               spec.unet_lora, spec.name)

        # Set the scheduler (Euler for Lightning/LCM with trailing timesteps)
        if spec.scheduler == "euler":
            pipe.scheduler = EulerDiscreteScheduler.from_config(
                pipe.scheduler.config, timestep_spacing=spec.timestep_spacing,
            )
        elif spec.scheduler == "euler_a":
            pipe.scheduler = EulerAncestralDiscreteScheduler.from_config(
                pipe.scheduler.config,
            )
        elif spec.scheduler == "dpm":
            pipe.scheduler = DPMSolverMultistepScheduler.from_config(
                pipe.scheduler.config,
            )

        # Move to device and apply memory optimizations
        pipe = pipe.to(device)
        if device == "cpu":
            # CPU optimizations: attention slicing + thread tuning
            pipe.enable_attention_slicing()
            import os
            cpu_count = os.cpu_count() or 4
            torch.set_num_threads(min(4, cpu_count - 2))
        elif device in ("cuda", "mps"):
            pipe.enable_model_cpu_offload()

        _PIPELINE_CACHE[cache_key] = pipe
        logger.info("Loaded model '%s' on device '%s' (mode=%s)",
                     spec.name, device, mode)
        return pipe

    def _resolve_size(self, req: GenRequest, spec) -> tuple[int, int]:
        """Resolve width/height from aspect_ratio and model spec."""
        from app.services.creative.model_registry import get_aspect_sizes
        ar_sizes = get_aspect_sizes(spec.width, spec.height)
        if req.aspect_ratio in ar_sizes:
            return ar_sizes[req.aspect_ratio]
        return spec.width, spec.height

    def _pipeline_call(self, pipe, spec, mode, req, prompt, neg_prompt,
                       w, h, seed, device) -> list[GenOutput]:
        """Invoke the diffusers pipeline for a single seed and return GenOutputs."""
        import io
        import torch

        start_time = time.time()
        is_sdxl = "XL" in spec.pipeline_class

        common_kwargs: dict = {
            "prompt": prompt,
            "negative_prompt": neg_prompt,
            "num_inference_steps": spec.num_inference_steps,
            "guidance_scale": spec.guidance_scale,
            "height": h,
            "width": w,
            "output_type": "pil",
            "generator": torch.Generator(device="cpu").manual_seed(seed),
        }

        if is_sdxl:
            common_kwargs["prompt_2"] = prompt
            common_kwargs["negative_prompt_2"] = neg_prompt
            common_kwargs["original_size"] = (h, w)
            common_kwargs["target_size"] = (h, w)
            common_kwargs["negative_original_size"] = (h, w)
            common_kwargs["negative_target_size"] = (h, w)

        if mode == "img2img":
            from PIL import Image
            from diffusers.utils import load_image
            ref_img = load_image(
                Image.open(req.reference_image).convert("RGB").resize((w, h))
            )
            common_kwargs["image"] = ref_img
            common_kwargs["strength"] = (
                req.extra.get("strength", get_settings().image_local_strength)
                if req.extra else get_settings().image_local_strength
            )
        elif mode == "inpaint":
            from PIL import Image
            from diffusers.utils import load_image
            ref_img = load_image(
                Image.open(req.reference_image).convert("RGB").resize((w, h))
            )
            mask_img = load_image(
                Image.open(req.mask_image).convert("L").resize((w, h))
            )
            common_kwargs["image"] = ref_img
            common_kwargs["mask_image"] = mask_img

        result = pipe(**common_kwargs)
        imgs = result.images if hasattr(result, "images") else result[0]

        outputs: list[GenOutput] = []
        meta_mode = {"img2img": "img2img", "inpaint": "inpaint"}.get(mode, "txt2img")
        duration = round(time.time() - start_time, 2)
        for img in imgs:
            buf = io.BytesIO()
            img.save(buf, "PNG")
            img_data = buf.getvalue()
            # QA: validate the image is non-empty, not corrupt, and passes quality gate
            qm = validate_image_output(img_data, provider="local-ai", min_quality=True)
            outputs.append(GenOutput(
                img_data, ".png", "image/png",
                {"ai": True, "provider": "local-ai", "model": spec.name,
                 "seed": seed, "mode": meta_mode,
                 "num_inference_steps": spec.num_inference_steps,
                 "guidance_scale": spec.guidance_scale,
                 "duration_seconds": duration,
                 **(qm or {})},
            ))
        return outputs

    def _settings(self):
        from app.config import get_settings
        return get_settings()

    def _procedural_generate(self, req: GenRequest) -> list[GenOutput]:
        """Deterministic procedural image synthesis (offline, not AI)."""
        from PIL import Image, ImageDraw, ImageFilter
        from app.services.creative.image_quality import assess_image_quality

        ratio = {r: s for r, s in {
            "16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1024, 1024),
            "4:3": (1024, 768), "3:4": (768, 1024), "3:2": (1200, 800), "2:3": (800, 1200),
        }.items()}.get(req.aspect_ratio, (1280, 720))
        outputs: list[GenOutput] = []
        for i in range(max(1, min(req.num_variations, 8))):
            seed_material = f"{req.prompt}|{req.seed}|{i}".encode()
            seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
            rng = np.random.default_rng(seed)
            base = _prompt_palette(req.prompt, rng)
            w, h = ratio
            kind = _scene_kind(req.prompt)
            sky_top = np.array(base[0], np.float32)
            sky_horizon = np.array(base[1], np.float32)
            ground = np.array(base[2], np.float32)
            horizon_y = int(h * (0.62 if kind == "city" else 0.60))
            grad = np.zeros((h, w, 3), np.float32)
            ys = np.linspace(0, 1, h)[:, None]
            for c in range(3):
                grad[..., c] = sky_top[c] * (1 - ys) + sky_horizon[c] * ys
            grad[horizon_y:, :] = ground[None, None, :]
            grad = grad * 0.5 + rng.random((h, w, 3)).astype(np.float32) * 128
            img = Image.fromarray(np.clip(grad, 0, 255).astype(np.uint8), "RGB")
            draw = ImageDraw.Draw(img, "RGBA")
            _draw_focal(draw, w, h, horizon_y, sky_top, kind, rng)
            _draw_foreground(draw, w, horizon_y, ground, kind, rng)
            # Draw prompt-aware objects (tree, house, sun, person, etc.)
            _draw_prompt_objects(draw, req.prompt, w, h, horizon_y, ground, rng)
            img = img.filter(ImageFilter.GaussianBlur(0.25))
            img = _vignette(img)
            if req.reference_image:
                try:
                    from app.services.creative import image_engine
                    ref = image_engine.open_image(req.reference_image).convert("RGB").resize((w, h))
                    img = Image.blend(ref, img, 0.45)
                except Exception:
                    pass
            import io
            buf = io.BytesIO()
            img.save(buf, "PNG")
            img_data = buf.getvalue()
            # Basic structural validation (no quality gate — procedural is a
            # fallback that degrades gracefully rather than rejecting outputs)
            validate_image_output(img_data, provider="local-procedural")
            qm = assess_image_quality(img_data)
            outputs.append(GenOutput(img_data, ".png", "image/png",
                                     {"ai": False, "provider": "local-procedural",
                                      "model": "jalora-procedural", "seed": seed,
                                      "mode": "procedural",
                                      **qm}))
        return outputs

    def estimate_cost(self, req: GenRequest) -> float | None:
        return 0.0

    def remove_background(self, image_data: bytes) -> bytes | None:
        """Remove the background from an image, returning RGBA PNG bytes.

        Uses *rembg* (U²-Net) if installed; returns None if the package is
        not available so the caller can degrade gracefully.
        """
        from app.services.creative import model_registry
        if not model_registry.can_load_rembg():
            logger.debug("rembg not installed — background removal unavailable")
            return None
        try:
            import io
            from PIL import Image
            from rembg import remove
            img = Image.open(io.BytesIO(image_data)).convert("RGBA")
            result = remove(img)
            buf = io.BytesIO()
            result.save(buf, "PNG")
            return buf.getvalue()
        except Exception as e:
            logger.warning("Background removal failed: %s", e)
            return None
_PIPELINE_CACHE: dict[str, "StableDiffusionXLPipeline"] = {}


def _prompt_palette(prompt: str, rng) -> list[tuple[float, float, float]]:
    keywords = {
        "sunset": [(255, 140, 60), (200, 60, 90), (60, 30, 80)],
        "ocean": [(30, 90, 150), (20, 150, 170), (10, 40, 90)],
        "forest": [(40, 90, 50), (80, 130, 60), (25, 45, 30)],
        "neon": [(20, 20, 40), (230, 30, 140), (30, 200, 230)],
        "desert": [(230, 180, 110), (200, 120, 70), (120, 70, 40)],
        "snow": [(230, 240, 250), (180, 200, 220), (120, 140, 170)],
        "fire": [(60, 20, 10), (230, 120, 20), (255, 220, 80)],
        "city": [(20, 20, 40), (25, 30, 60), (15, 20, 30)],
        "night": [(10, 20, 50), (25, 30, 70), (5, 10, 20)],
    }
    low = prompt.lower()
    for key, pal in keywords.items():
        if key in low:
            return [tuple(float(v) for v in c) for c in pal]
    arr = rng.integers(30, 220, (3, 3)).astype(float)
    return [tuple(row) for row in arr]


def _scene_kind(prompt: str) -> str:
    """Infer a coarse scene archetype (day/night/city) for the procedural engine."""
    low = (prompt or "").lower()
    if any(k in low for k in ("city", "urban")):
        return "city"
    if any(k in low for k in ("night", "stars", "moon", "dark")):
        return "night"
    return "day"


def _draw_focal(draw, w: int, h: int, horizon_y: int, sky_top, kind: str, rng) -> None:
    """Draw a deterministic sun / moon / stars focal element in the sky band."""
    if kind == "night":
        for _ in range(24):
            sx = int(rng.integers(0, w))
            sy = int(rng.integers(0, horizon_y))
            d = int(rng.integers(1, 4))
            draw.ellipse([sx - d, sy - d, sx + d, sy + d], fill=(255, 255, 240, 210))
        mcx, mcy = int(w * 0.82), int(h * 0.28)
        mr = int(min(w, h) * 0.05)
        draw.ellipse([mcx - mr, mcy - mr, mcx + mr, mcy + mr], fill=(245, 245, 245, 255))
        oc = tuple(int(c) for c in sky_top) + (255,)
        draw.ellipse([mcx - mr + int(mr * 0.35), mcy - mr + int(mr * 0.35),
                      mcx + mr + int(mr * 0.35), mcy + mr + int(mr * 0.35)], fill=oc)
    else:
        cx = int(w * (0.8 if rng.random() < 0.5 else 0.2))
        cy = int(h * 0.28)
        r = int(min(w, h) * 0.055)
        for rad in range(r, 0, -2):
            alpha = int(90 * (rad / r))
            draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad],
                         fill=(255, 232, 150, alpha))


def _draw_foreground(draw, w: int, horizon_y: int, ground, kind: str, rng) -> None:
    """Draw simple foreground silhouettes (mountains / city blocks) along the horizon."""
    dark = tuple(max(0, int(c) - 50) for c in ground) + (255,)
    if kind == "city":
        n = 7
        for i in range(n):
            x0 = int(i * w / n) + int(rng.integers(-3, 4))
            width = int(w * rng.uniform(0.04, 0.07)) + 6
            height = int(rng.uniform(0.3, 0.7) * (horizon_y - 2)) + 2
            x0 = max(0, min(x0, w - width))
            draw.rectangle([x0, horizon_y - height, x0 + width, horizon_y], fill=dark)
    else:
        n = 5
        for i in range(n):
            cx = int((i + 0.5) * w / n)
            bw = int(w * rng.uniform(0.08, 0.16)) + 8
            bh = int(rng.uniform(0.12, 0.24) * horizon_y) + 4
            x0 = cx - bw // 2
            draw.polygon([(x0, horizon_y - bh), (x0 + bw, horizon_y - bh), (cx, horizon_y)], fill=dark)


def _prompt_objects(prompt: str) -> list[str]:
    """Extract object keywords from a prompt for procedural iconography.

    Returns a list of normalised object identifiers that the procedural
    renderer recognises (e.g. ``["tree", "sun"]``).  The list is capped at
    a handful of entries so the canvas isn't overcrowded.
    """
    low = (prompt or "").lower()
    # Keyword -> canonical object.  Order matters: more specific terms first.
    obj_map: list[tuple[str, str]] = [
        ("mountain", "mountain"),
        ("tree", "tree"),
        ("forest", "tree"),
        ("flower", "flower"),
        ("sun", "sun"),
        ("moon", "moon"),
        ("star", "star"),
        ("cloud", "cloud"),
        ("rain", "rain"),
        ("house", "house"),
        ("building", "house"),
        ("castle", "castle"),
        ("bridge", "bridge"),
        ("boat", "boat"),
        ("ship", "boat"),
        ("car", "car"),
        ("person", "person"),
        ("people", "person"),
        ("man", "person"),
        ("woman", "person"),
        ("human", "person"),
        ("woman", "person"),
        ("dog", "animal"),
        ("cat", "animal"),
        ("bird", "bird"),
        ("fish", "fish"),
        ("apple", "fruit"),
        ("cat", "animal"),
        ("heart", "heart"),
        ("star", "star"),
    ]
    found: list[str] = []
    for kw, canonical in obj_map:
        if re.search(rf"\b{re.escape(kw)}\b", low) and canonical not in found:
            found.append(canonical)
    # Deduplicate while preserving order, cap at 6
    return found[:6]


def _draw_prompt_objects(draw, prompt: str, w: int, h: int,
                         horizon_y: int, ground, rng) -> None:
    """Draw simple, recognisable icons for objects mentioned in the prompt.

    Each object is a minimal but identifiable shape (tree = green circle on
    brown trunk, house = square + triangle roof, person = stick figure, etc.)
    placed deterministically based on the prompt's seed-derived RNG.
    """
    objects = _prompt_objects(prompt)
    if not objects:
        return

    dark_ground = tuple(max(0, int(c) - 30) for c in ground) + (255,)
    dark_ground_alt = tuple(max(0, int(c) - 30) for c in ground) + (180,)

    n = len(objects)
    for idx, obj in enumerate(objects):
        # Spread objects across the canvas; sky objects above horizon,
        # ground objects below.
        x = int(w * (0.15 + 0.7 * idx / max(1, n - 1))) + int(rng.integers(-20, 21))
        x = max(0, min(x, w - 1))

        if obj == "sun":
            r = int(min(w, h) * 0.04 + rng.integers(-5, 6))
            cx, cy = x, int(h * 0.22)
            for rad in range(r, 0, -2):
                alpha = int(180 * (rad / r))
                draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad],
                             fill=(255, 232, 100, alpha))
        elif obj == "moon":
            r = int(min(w, h) * 0.04 + rng.integers(-5, 6))
            cx, cy = x, int(h * 0.22)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(245, 245, 245, 255))
            # Crescent cut-out
            offset = int(r * 0.3)
            draw.ellipse([cx - r + offset, cy - r, cx + r + offset, cy + r],
                         fill=tuple(int(c) for c in (10, 20, 50)) + (255,))
        elif obj == "star":
            r = int(min(w, h) * 0.02)
            cx, cy = x, int(h * 0.20 + rng.integers(-30, 31))
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 200, 220))
        elif obj == "cloud":
            cx = x
            cy = int(h * 0.18 + rng.integers(-20, 21))
            for ox in (-25, 0, 25):
                r = int(min(w, h) * 0.03)
                draw.ellipse([cx + ox - r, cy - r, cx + ox + r, cy + r],
                             fill=(255, 255, 255, 180))
        elif obj == "rain":
            cx = x
            top_y = int(h * 0.12)
            for _ in range(12):
                rx = cx + int(rng.integers(-30, 31))
                ry = top_y + int(rng.integers(0, h - top_y - horizon_y))
                draw.line([rx, ry, rx, ry + 12], fill=(100, 140, 255, 160), width=2)
        elif obj == "tree":
            cx = x
            trunk_w = int(min(w, h) * 0.025)
            trunk_h = int(min(w, h) * 0.08)
            tx0 = cx - trunk_w // 2
            ty0 = horizon_y - trunk_h
            draw.rectangle([tx0, ty0, tx0 + trunk_w, horizon_y],
                           fill=tuple(int(c * 0.6) for c in ground[:3]) + (255,))
            leaf_r = int(min(w, h) * 0.04)
            for oy in (0, -leaf_r * 0.6, leaf_r * 0.6):
                draw.ellipse([cx - leaf_r, ty0 + oy - leaf_r,
                              cx + leaf_r, ty0 + oy + leaf_r],
                             fill=(0, max(80, int(ground[1]) - 20), 0, 220))
        elif obj == "mountain":
            cx = x
            bw = int(min(w, h) * 0.1)
            bh = int(horizon_y * 0.6)
            px = cx - bw // 2
            py = horizon_y - bh
            draw.polygon(
                [(px, py + bh), (px + bw, py + bh), (cx, py)],
                fill=dark_ground,
            )
            # Snow cap
            if rng.random() < 0.5:
                cap_w = int(bw * 0.4)
                cap_h = int(bh * 0.2)
                draw.polygon(
                    [(cx - cap_w // 2, py + 5),
                     (cx + cap_w // 2, py + 5),
                     (cx, py + 5 + cap_h // 2)],
                    fill=(245, 245, 245, 255),
                )
        elif obj == "flower":
            cx, cy = x, horizon_y - 10
            petal_r = int(min(w, h) * 0.025)
            # Petals (4 dots around center)
            for angle in (0, 90, 180, 270):
                import math as _m
                px = cx + int(petal_r * 0.7 * _m.cos(_m.radians(angle)))
                py = cy + int(petal_r * 0.7 * _m.sin(_m.radians(angle)))
                draw.ellipse([px - petal_r, py - petal_r, px + petal_r, py + petal_r],
                             fill=(255, int(100 + rng.integers(0, 80)), 200, 220))
            # Center
            draw.ellipse([cx - petal_r * 0.5, cy - petal_r * 0.5,
                          cx + petal_r * 0.5, cy + petal_r * 0.5],
                         fill=(255, 215, 30, 255))
        elif obj == "house":
            cx = x
            bw = int(min(w, h) * 0.08)
            bh = int(min(w, h) * 0.06)
            hx = cx - bw // 2
            hy = horizon_y - bh
            draw.rectangle([hx, hy, hx + bw, hy + bh], fill=dark_ground)
            # Roof (triangle)
            draw.polygon([(hx, hy), (hx + bw, hy), (cx, hy - bh // 2)],
                         fill=tuple(int(c * 0.7) for c in ground[:3]) + (255,))
            # Door
            dw, dh = max(3, bw // 6), max(4, bh // 3)
            draw.rectangle([hx + bw // 2 - dw // 2, hy + bh - dh,
                            hx + bw // 2 + dw // 2, hy + bh],
                           fill=tuple(int(c * 0.3) for c in ground[:3]) + (255,))
        elif obj == "castle":
            cx = x
            bw = int(min(w, h) * 0.12)
            bh = int(min(w, h) * 0.07)
            hx = cx - bw // 2
            hy = horizon_y - bh
            draw.rectangle([hx, hy, hx + bw, hy + bh], fill=dark_ground)
            # Towers
            for tx in (hx + 5, hx + bw // 2, hx + bw - 5):
                tw = max(4, bw // 8)
                draw.rectangle([tx - tw // 2, hy - bh // 2, tx + tw // 2, hy + bh],
                               fill=dark_ground)
                draw.ellipse([tx - tw // 2, hy - bh // 2 - tw // 3,
                              tx + tw // 2, hy - bh // 2 + tw // 3],
                             fill=tuple(int(c * 0.7) for c in ground[:3]) + (255,))
            # Roof
            draw.polygon([(hx, hy), (hx + bw, hy), (cx, hy - bh // 2)],
                         fill=tuple(int(c * 0.7) for c in ground[:3]) + (255,))
        elif obj == "bridge":
            cx = x
            bw = int(min(w, h) * 0.15)
            road_y = horizon_y + int(min(w, h) * 0.03)
            draw.rectangle([cx - bw // 2, road_y, cx + bw // 2, road_y + 6],
                           fill=tuple(int(c * 0.5) for c in ground[:3]) + (255,))
            # Arch
            draw.arc([cx - bw // 2, road_y - bw // 4, cx + bw // 2, road_y + 6],
                     start=0, end=180, fill=tuple(int(c * 0.5) for c in ground[:3]) + (255,),
                     width=4)
        elif obj == "boat":
            cx = x
            bw = int(min(w, h) * 0.06)
            bh = int(min(w, h) * 0.03)
            by = horizon_y + int((h - horizon_y) * 0.3)
            hull_color = tuple(int(c * 0.4) for c in ground[:3]) + (255,)
            draw.rectangle([cx - bw // 2, by, cx + bw // 2, by + bh], fill=hull_color)
            draw.polygon([(cx - bw // 2, by), (cx + bw // 2, by), (cx, by - bh)],
                         fill=hull_color)
            # Mast
            draw.rectangle([cx, by - int(min(w, h) * 0.08), cx + 2, by],
                           fill=tuple(int(c * 0.3) for c in ground[:3]) + (255,))
            # Sail
            draw.polygon([(cx + 2, by - int(min(w, h) * 0.08),),
                          (cx + int(min(w, h) * 0.08), by - int(min(w, h) * 0.04)),
                          (cx + 2, by)], fill=(255, 255, 255, 200))
        elif obj == "car":
            cx = x
            bw = int(min(w, h) * 0.07)
            bh = int(min(w, h) * 0.035)
            cy = horizon_y + int((h - horizon_y) * 0.15)
            car_color = tuple(int(c * 0.5) for c in ground[:3]) + (255,)
            draw.rectangle([cx - bw // 2, cy, cx + bw // 2, cy + bh], fill=car_color)
            draw.polygon([(cx - bw // 2, cy), (cx + bw // 2, cy), (cx, cy - bh // 2)],
                         fill=car_color)
            # Wheels
            for wx in (cx - bw // 3, cx + bw // 3):
                draw.ellipse([wx - 4, cy + bh - 2, wx + 4, cy + bh + 6],
                             fill=(30, 30, 30, 255))
        elif obj == "person":
            cx = x
            head_r = int(min(w, h) * 0.02)
            head_y = horizon_y + int((h - horizon_y) * 0.3)
            skin = (230, 210, 180, 255)
            # Head
            draw.ellipse([cx - head_r, head_y - head_r, cx + head_r, head_y + head_r],
                         fill=skin)
            # Body
            body_h = int(min(w, h) * 0.04)
            draw.line([cx, head_y + head_r, cx, head_y + head_r + body_h],
                      fill=(50, 50, 80, 255), width=3)
            # Arms (stick out)
            arm_len = int(min(w, h) * 0.025)
            draw.line([cx, head_y + head_r + 3, cx - arm_len, head_y + head_r + body_h // 2],
                      fill=(50, 50, 80, 255), width=2)
            draw.line([cx, head_y + head_r + 3, cx + arm_len, head_y + head_r + body_h // 2],
                      fill=(50, 50, 80, 255), width=2)
            # Legs
            leg_len = int(min(w, h) * 0.03)
            draw.line([cx, head_y + head_r + body_h,
                       cx - leg_len // 2, head_y + head_r + body_h + leg_len],
                      fill=(30, 30, 80, 255), width=2)
            draw.line([cx, head_y + head_r + body_h,
                       cx + leg_len // 2, head_y + head_r + body_h + leg_len],
                      fill=(30, 30, 80, 255), width=2)
        elif obj == "animal":
            cx = x
            body_r = int(min(w, h) * 0.025)
            cy = horizon_y + int((h - horizon_y) * 0.2)
            animal_color = tuple(int(c * 0.4) for c in ground[:3]) + (255,)
            # Body (oval)
            draw.ellipse([cx - body_r, cy - body_r // 2, cx + body_r, cy + body_r // 2],
                         fill=animal_color)
            # Head
            draw.ellipse([cx + body_r - body_r // 2, cy - body_r, cx + body_r + body_r // 2,
                          cy], fill=animal_color)
            # Ears
            draw.polygon([(cx + body_r, cy - body_r),
                          (cx + body_r + 5, cy - body_r // 2),
                          (cx + body_r + 10, cy - body_r)], fill=animal_color)
        elif obj == "bird":
            cx = x
            cy = horizon_y - int(min(w, h) * 0.02)
            # Simple V-shape wings
            wing = int(min(w, h) * 0.015)
            draw.line([cx - wing, cy, cx, cy - wing], fill=(30, 30, 30, 255), width=2)
            draw.line([cx, cy - wing, cx + wing, cy], fill=(30, 30, 30, 255), width=2)
        elif obj == "fish":
            cx = x
            cy = horizon_y + int((h - horizon_y) * 0.4)
            body_r = int(min(w, h) * 0.02)
            fish_color = (60, 120, 200, 220)
            draw.ellipse([cx - body_r, cy - body_r // 2, cx + body_r, cy + body_r // 2],
                         fill=fish_color)
            # Tail
            draw.polygon([(cx - body_r, cy), (cx - body_r - 8, cy - 6),
                          (cx - body_r - 8, cy + 6)], fill=fish_color)
            # Eye
            draw.ellipse([cx + body_r - 4, cy - 2, cx + body_r + 1, cy + 3],
                         fill=(0, 0, 0, 255))
        elif obj == "fruit":
            cx, cy = x, horizon_y + 10
            r = int(min(w, h) * 0.02)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                         fill=(255, int(100 + rng.integers(0, 100)), 30, 220))
            # Leaf
            draw.polygon([(cx, cy - r), (cx + 8, cy - r - 4), (cx + 12, cy - r + 2)],
                         fill=(30, 120, 40, 255))
        elif obj == "heart":
            cx = x
            cy = horizon_y + int((h - horizon_y) * 0.3)
            size = int(min(w, h) * 0.03)
            # Two circles
            draw.ellipse([cx - size, cy - size, cx, cy], fill=(220, 30, 60, 220))
            draw.ellipse([cx, cy - size, cx + size, cy], fill=(220, 30, 60, 220))
            # Triangle bottom
            draw.polygon([(cx - size, cy), (cx + size, cy), (cx, cy + size)],
                         fill=(220, 30, 60, 220))


def _vignette(img) -> "Image.Image":
    """Gently darken corners for a more intentional, photo-like finish."""
    from PIL import Image
    w, h = img.size
    xs = np.linspace(-1, 1, w)[None, :]
    ys = np.linspace(-1, 1, h)[:, None]
    r = np.sqrt(xs * xs + ys * ys)
    mask = np.clip(1.0 - r * 0.30, 0.0, 1.0)
    arr = np.asarray(img).astype(np.float32)
    arr *= mask[:, :, None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


# ============================================================ video providers

class VideoProvider:
    name = "base"
    ai = True
    capabilities: dict = {}

    def configured(self) -> bool:
        return False

    def generate(self, req: GenRequest, progress=None) -> list[GenOutput]:
        raise NotImplementedError

    def estimate_cost(self, req: GenRequest) -> float | None:
        return None


class OpenAIVideoProvider(VideoProvider):
    name = "openai"
    capabilities = {"text_to_video": True, "image_to_video": True, "max_duration": 12,
                    "aspect_ratios": ["16:9", "9:16"], "audio": True}

    def __init__(self, settings: Settings):
        self.settings = settings

    def configured(self) -> bool:
        return bool(self.settings.openai_api_key)

    def generate(self, req: GenRequest, progress=None) -> list[GenOutput]:
        if not self.configured():
            raise CreativeEngineError("OpenAI is not configured.", "PROVIDER_NOT_CONFIGURED",
                                      provider=self.name)
        model = req.model or self.settings.video_model or "sora-2"
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.settings.openai_api_key,
                            base_url=self.settings.openai_base_url)
            create_kwargs = dict(model=model, prompt=req.prompt,
                                 seconds=str(int(max(1, min(req.duration, 12)))),
                                 size=_sora_size(req.aspect_ratio))
            if req.reference_image:
                with open(req.reference_image, "rb") as img:
                    video = client.videos.create_and_poll(input_reference=img, **create_kwargs)
            else:
                video = client.videos.create_and_poll(**create_kwargs)
            content = client.videos.download_content(video.id, variant="video")
            data = content.content if hasattr(content, "content") else content.read()
            return [GenOutput(data, ".mp4", "video/mp4",
                              {"ai": True, "provider": self.name, "model": model,
                               "video_id": video.id})]
        except CreativeEngineError:
            raise
        except Exception as exc:
            raise _map_error(exc, self.name) from exc

    def estimate_cost(self, req: GenRequest) -> float | None:
        return 0.10 * max(1, req.duration)


def _sora_size(aspect: str) -> str:
    return {"16:9": "1280x720", "9:16": "720x1280", "1:1": "720x720"}.get(aspect, "1280x720")


class GoogleVideoProvider(VideoProvider):
    name = "google"
    capabilities = {"text_to_video": True, "image_to_video": True, "max_duration": 8,
                    "aspect_ratios": ["16:9", "9:16"], "audio": True}

    def __init__(self, settings: Settings):
        self.settings = settings

    def configured(self) -> bool:
        return bool(self.settings.google_api_key)

    def generate(self, req: GenRequest, progress=None) -> list[GenOutput]:
        if not self.configured():
            raise CreativeEngineError("Google AI is not configured.", "PROVIDER_NOT_CONFIGURED",
                                      provider=self.name)
        model = req.model or self.settings.video_model or "veo-3.1-fast-generate-preview"
        instance: dict = {"prompt": req.prompt}
        if req.reference_image:
            instance["image"] = {"imageBytes": base64.b64encode(req.reference_image.read_bytes()).decode(),
                                 "mimeType": _guess_mime(req.reference_image)}
        body = {"instances": [instance],
                "parameters": {"aspectRatio": req.aspect_ratio,
                               "durationSeconds": int(max(1, min(req.duration, 8)))}}
        try:
            r = httpx.post(f"{GOOGLE_BASE}/models/{model}:predictLongRunning",
                           params={"key": self.settings.google_api_key}, json=body, timeout=60)
            if r.status_code != 200:
                raise CreativeEngineError(_google_err(r), _google_code(r.status_code), provider=self.name)
            op = r.json().get("name")
            if not op:
                raise CreativeEngineError("Veo did not return an operation.", "EMPTY_OUTPUT", provider=self.name)
            deadline = time.time() + 600
            result = None
            while time.time() < deadline:
                time.sleep(8)
                if progress:
                    progress(min(0.95, 0.1 + (600 - (deadline - time.time())) / 700))
                pr = httpx.get(f"{GOOGLE_BASE}/{op}", params={"key": self.settings.google_api_key}, timeout=60)
                pj = pr.json()
                if pj.get("done"):
                    result = pj
                    break
            if result is None:
                raise CreativeEngineError("Veo generation timed out.", "PROVIDER_TIMEOUT", provider=self.name)
            if "error" in result:
                raise CreativeEngineError(str(result["error"]), "PROVIDER_ERROR", provider=self.name)
            samples = (result.get("response", {}).get("generateVideoResponse", {})
                       .get("generatedSamples", []))
            outputs = []
            for sample in samples:
                uri = (sample.get("video") or {}).get("uri")
                if not uri:
                    continue
                dl = httpx.get(uri, params={"key": self.settings.google_api_key},
                               follow_redirects=True, timeout=300)
                dl.raise_for_status()
                outputs.append(GenOutput(dl.content, ".mp4", "video/mp4",
                                         {"ai": True, "provider": self.name, "model": model}))
            if not outputs:
                raise CreativeEngineError("Veo returned no downloadable video.", "EMPTY_OUTPUT", provider=self.name)
            return outputs
        except CreativeEngineError:
            raise
        except Exception as exc:
            raise _map_error(exc, self.name) from exc

    def estimate_cost(self, req: GenRequest) -> float | None:
        return 0.35 * max(1, req.duration)


class LocalVideoProvider(VideoProvider):
    """Ken Burns motion from a still, or procedural animated frames (offline)."""

    name = "local"
    ai = False
    capabilities = {"text_to_video": True, "image_to_video": True, "max_duration": 60,
                    "aspect_ratios": ["16:9", "9:16", "1:1", "4:3"], "audio": False,
                    "procedural": True}

    def configured(self) -> bool:
        return True

    def generate(self, req: GenRequest, progress=None) -> list[GenOutput]:
        import tempfile

        from app.services.creative import media_engine

        size = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1024, 1024),
                "4:3": (1024, 768), "3:4": (768, 1024)}.get(req.aspect_ratio, (1280, 720))
        tmp = Path(tempfile.mkdtemp())
        dest = tmp / "local_motion.mp4"
        if req.reference_image:
            media_engine.image_to_video(req.reference_image, dest, duration=req.duration,
                                        size=size, ken_burns=True, zoom_to=1.15)
        else:
            from app.services.creative.providers import LocalImageProvider
            img_out = LocalImageProvider().generate(
                GenRequest(kind="image", prompt=req.prompt, aspect_ratio=req.aspect_ratio,
                           seed=req.seed))[0]
            src = tmp / "src.png"
            src.write_bytes(img_out.data)
            media_engine.image_to_video(src, dest, duration=req.duration, size=size,
                                        ken_burns=True, pan="auto")
        return [GenOutput(dest.read_bytes(), ".mp4", "video/mp4",
                          {"ai": False, "provider": "local-motion", "model": "jalora-kenburns"})]

    def estimate_cost(self, req: GenRequest) -> float | None:
        return 0.0


# ============================================================ audio providers

class AudioProvider:
    name = "base"
    ai = True
    capabilities: dict = {}

    def configured(self) -> bool:
        return False

    def generate(self, req: GenRequest) -> list[GenOutput]:
        raise NotImplementedError

    def estimate_cost(self, req: GenRequest) -> float | None:
        return None


class OpenAIAudioProvider(AudioProvider):
    name = "openai"
    capabilities = {"tts": True, "music": False, "sfx": False, "voices": True}

    def __init__(self, settings: Settings):
        self.settings = settings

    def configured(self) -> bool:
        return bool(self.settings.openai_api_key)

    def generate(self, req: GenRequest) -> list[GenOutput]:
        if not self.configured():
            raise CreativeEngineError("OpenAI is not configured.", "PROVIDER_NOT_CONFIGURED",
                                      provider=self.name)
        model = req.model or "gpt-4o-mini-tts"
        voice = req.voice or "alloy"
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.settings.openai_api_key,
                            base_url=self.settings.openai_base_url)
            with client.audio.speech.with_streaming_response.create(
                    model=model, voice=voice, input=req.prompt) as resp:
                data = resp.read()
            return [GenOutput(data, ".mp3", "audio/mpeg",
                              {"ai": True, "provider": self.name, "model": model,
                               "kind": "speech", "voice": voice})]
        except CreativeEngineError:
            raise
        except Exception as exc:
            raise _map_error(exc, self.name) from exc

    def estimate_cost(self, req: GenRequest) -> float | None:
        return 0.015


class LocalAudioProvider(AudioProvider):
    """Synthesised ambience / tones from prompt (offline, not AI)."""

    name = "local"
    ai = False
    capabilities = {"tts": False, "music": True, "sfx": True, "procedural": True}

    def configured(self) -> bool:
        return True

    def generate(self, req: GenRequest) -> list[GenOutput]:
        import io

        import soundfile as sf

        sr = 44100
        dur = max(1.0, min(req.duration or 10.0, 300.0))
        n = int(dur * sr)
        seed = int.from_bytes(hashlib.sha256(f"{req.prompt}|{req.seed}".encode()).digest()[:8], "big")
        rng = np.random.default_rng(seed)
        low = req.prompt.lower()
        if any(w in low for w in ("rain", "water", "ocean", "waves")):
            base = rng.random(n).astype(np.float32) * 2 - 1
            from scipy import signal
            b, a = signal.butter(2, 0.05, "low")
            audio = signal.lfilter(b, a, base).astype(np.float32) * 0.5
        elif any(w in low for w in ("wind", "space", "drone", "ambient")):
            t = np.linspace(0, dur, n)
            audio = sum(0.15 * np.sin(2 * np.pi * f * t) for f in (55, 82.5, 110))
            audio = (audio * (0.6 + 0.4 * np.sin(2 * np.pi * 0.1 * t))).astype(np.float32)
        else:
            scale = [220.0, 246.9, 293.7, 329.6, 392.0]
            t = np.linspace(0, dur, n)
            audio = np.zeros(n, np.float32)
            beat = 0.5
            k = 0
            while k * beat < dur:
                f = scale[int(rng.integers(0, len(scale)))]
                s = int(k * beat * sr)
                e = min(n, s + int(beat * sr * 0.9))
                env = np.linspace(0, 1, e - s) * np.linspace(1, 0, e - s)
                audio[s:e] += 0.3 * env * np.sin(2 * np.pi * f * t[:e - s])
                k += 1
        audio = audio / max(1e-6, float(np.max(np.abs(audio))))
        buf = io.BytesIO()
        sf.write(buf, audio.astype(np.float32), sr, format="WAV")
        return [GenOutput(buf.getvalue(), ".wav", "audio/wav",
                          {"ai": False, "provider": "local-synth", "model": "jalora-synth"})]

    def estimate_cost(self, req: GenRequest) -> float | None:
        return 0.0


# ============================================================ registry

def _image_providers(settings: Settings) -> list[ImageProvider]:
    return [OpenAIImageProvider(settings), GoogleImageProvider(settings), LocalImageProvider()]


def _video_providers(settings: Settings) -> list[VideoProvider]:
    return [OpenAIVideoProvider(settings), GoogleVideoProvider(settings), LocalVideoProvider()]


def _audio_providers(settings: Settings) -> list[AudioProvider]:
    return [OpenAIAudioProvider(settings), LocalAudioProvider()]


_KIND_ORDER = {"auto": None}


def resolve_provider(kind: str, requested: str | None, settings: Settings | None = None):
    """Pick a provider for ``kind`` (image|video|audio) honouring config."""
    settings = settings or get_settings()
    requested = (requested or "auto").lower()
    if kind == "image":
        providers = _image_providers(settings)
        default_choice = settings.image_provider
    elif kind == "video":
        providers = _video_providers(settings)
        default_choice = settings.video_provider
    else:
        providers = _audio_providers(settings)
        default_choice = settings.audio_provider
    if requested != "auto":
        for p in providers:
            if p.name == requested:
                return p
        # Alias: "local-ai" and "local-procedural" both resolve to LocalImageProvider
        if requested in ("local-ai", "local-procedural"):
            for p in providers:
                if p.name == "local":
                    return p
    if default_choice and default_choice != "auto":
        for p in providers:
            if p.name == default_choice:
                return p
    # auto: first configured (cloud preferred), else first available
    for p in providers:
        if p.configured():
            return p
    return providers[-1]


def generate_ai_image(
    request: GenRequest,
    requested: str | None,
    settings: Settings,
) -> tuple[list[GenOutput], ImageProvider, dict | None]:
    """Generate an image using AI providers; never return procedural artwork.

    In automatic mode, try the configured provider first, followed by any other
    configured cloud provider, and finally the local diffusion provider. Local
    procedural output is rejected because it cannot reliably depict the
    requested scene.
    """
    selected = resolve_provider("image", requested, settings)
    candidates = [selected]
    if (requested or "auto").lower() == "auto":
        candidates.extend(
            provider for provider in _image_providers(settings)
            if provider.name != selected.name and provider.configured()
        )

    failures: list[tuple[str, str]] = []
    for provider in candidates:
        try:
            if isinstance(provider, LocalImageProvider):
                outputs = provider._ai_generate(request)
                if outputs is None:
                    failures.append((provider.name, "MODEL_UNAVAILABLE"))
                    logger.warning(
                        "Local AI image generation is unavailable; procedural "
                        "image output is not eligible for this request",
                    )
                    continue
            else:
                outputs = provider.generate(request)
        except CreativeEngineError as exc:
            code = exc.code or "GENERATION_ERROR"
            failures.append((provider.name, code))
            logger.warning(
                "AI image generation failed for provider %s (%s)",
                provider.name, code,
            )
            continue
        except Exception:
            failures.append((provider.name, "PROVIDER_ERROR"))
            logger.exception(
                "AI image generation crashed for provider %s",
                provider.name,
            )
            continue

        if outputs and all(output.meta.get("ai") is True for output in outputs):
            fallback = None
            if provider.name != selected.name:
                reason = next(
                    (
                        code for name, code in failures
                        if name == selected.name
                    ),
                    "PROVIDER_UNAVAILABLE",
                )
                fallback = {
                    "from": selected.name,
                    "reason": reason,
                    "to": provider.name,
                }
            return outputs, provider, fallback

        failures.append((provider.name, "NON_AI_OUTPUT"))
        logger.warning(
            "Rejecting non-AI image output from provider %s",
            provider.name,
        )

    attempted = ", ".join(
        f"{name} ({code})" for name, code in failures
    ) or "no configured image provider"
    raise CreativeEngineError(
        "No configured image provider returned verified AI-generated output. "
        f"Attempts: {attempted}. Configure a working image provider or restore "
        "its quota; procedural placeholder images are not used.",
        "AI_IMAGE_GENERATION_UNAVAILABLE",
        provider=selected.name,
    )


def capability_registry(settings: Settings | None = None) -> dict:
    """Secret-free description of what each configured provider supports."""
    settings = settings or get_settings()
    out: dict[str, list[dict]] = {}
    for kind, providers in (("image", _image_providers(settings)),
                            ("video", _video_providers(settings)),
                            ("audio", _audio_providers(settings))):
        out[kind] = [
            {"name": p.name, "ai": p.ai, "configured": p.configured(),
             "capabilities": getattr(p, "capabilities", {})}
            for p in providers
        ]
    return out


def _guess_mime(path: Path) -> str:
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif"}.get(path.suffix.lower(), "image/png")


def _google_code(status: int) -> str:
    return {429: "PROVIDER_QUOTA", 401: "PROVIDER_AUTH", 403: "PROVIDER_AUTH",
            404: "MODEL_UNAVAILABLE"}.get(status, "PROVIDER_ERROR")


def _google_err(resp: httpx.Response) -> str:
    try:
        msg = resp.json().get("error", {}).get("message", resp.text)
    except Exception:
        msg = resp.text
    return f"Google AI error ({resp.status_code}): {msg[:400]}"
