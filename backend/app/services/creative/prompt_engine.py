"""Prompt engineering service for AI image generation.

Improves generation quality by:
- Expanding short/clip-style prompts into detailed descriptions with type-specific
  templates (portrait, landscape, product, poster, illustration, etc.).
- Providing a sensible default negative prompt when the user doesn't supply one.
- Building aspect-ratio-aware prompt suffixes.

Based on SDXL community best practices and research that top generators
(DALL·E 3, Midjourney) all use prompt expansion/rewrite before synthesis.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("jalora.creative.prompt_engine")

# --------------------------------------------------------------------------- #
#  Quality / style keywords appended to prompts to improve SDXL output.
#  These are the most commonly recommended tags in the SDXL community
#  (from the model card comments, Civitai, and HuggingFace discussions).
# --------------------------------------------------------------------------- #

_QUALITY_KEYWORDS = (
    "high quality, extremely detailed, high resolution, "
    "sharp focus, professional photography, "
    "8k, ultra hd, cinematic"
)

# Default negative prompt — the standard "bad-hand" / "bad-concept" list from the
# SDXL community. Covers the most common artifacts SDXL produces without guidance.
DEFAULT_NEGATIVE_PROMPT = (
    "deformed, disfigured, bad anatomy, extra limbs, missing limbs, "
    "fused fingers, too many fingers, bad hands, missing fingers, "
    "text, signature, watermark, username, "
    "blurry, low quality, worst quality, normal quality, "
    "jpeg artifacts, out of frame, cropped, lowres, "
    "mutated hands, cloned face, malformed, "
    "ugly, duplicate, morbid, mutilated, "
    "extra arms, extra legs, disfigured, "
    "inaccurate eyes, cross-eyed, "
    "nsfw, nude, naked"
).strip()

# Aspect-ratio composition guidance (like Google's current approach, but
# kept as a separate suffix so it doesn't corrupt the main prompt).
_ASPECT_GUIDANCE = {
    "16:9": "wide cinematic landscape shot",
    "9:16": "tall vertical portrait orientation, full body",
    "1:1": "centered composition, square frame",
    "4:3": "standard landscape, balanced framing",
    "3:4": "portrait orientation, centered subject",
    "3:2": "classic photo ratio, horizontal",
    "2:3": "classic portrait ratio, vertical",
}

# --------------------------------------------------------------------------- #
#  Structured prompt templates by image type.
#  These wrap the user's raw prompt to produce a detailed, well-conditioned
#  description — the same "prompt expansion" that DALL·E 3 / Midjourney use
#  internally before synthesis.  Each template fills specific slots so that
#  short or vague prompts become recognisable, photorealistic images rather
#  than abstract shapes.
# --------------------------------------------------------------------------- #
_STYLE_TEMPLATES: dict[str, str] = {
    "photorealistic": (
        "{prompt}, photorealistic, ultra-detailed, 85mm lens, "
        "shallow depth of field, natural skin texture, "
        "professional photography, cinematic lighting, "
        "high resolution, sharp focus"
    ),
    "portrait": (
        "{prompt}, photorealistic portrait, 85mm lens, f/1.8 aperture, "
        "shallow depth of field, soft bokeh background, "
        "natural skin texture, cinematic lighting, "
        "sharp focus on eyes, high resolution"
    ),
    "landscape": (
        "{prompt}, expansive landscape, wide-angle 24mm lens, "
        "golden hour lighting, atmospheric perspective, "
        "hyper-detailed terrain, 8k resolution, "
        "professional landscape photography"
    ),
    "product": (
        "{prompt}, product photography, studio lighting, "
        "clean white background, 35mm lens, "
        "sharp focus, commercial photography, "
        "high resolution, studio setup"
    ),
    "poster": (
        "{prompt}, movie poster, dramatic composition, "
        "bold typography space, cinematic lighting, "
        "high contrast, 4k poster design, "
        "professional movie poster"
    ),
    "illustration": (
        "{prompt}, digital illustration, concept art, "
        "studio-quality, highly detailed, "
        "artstation trending, smooth brushwork"
    ),
    "anime": (
        "{prompt}, anime style, studio-quality animation, "
        "detailed character design, vibrant colors, "
        "clean line art"
    ),
    "oil painting": (
        "{prompt}, oil painting, baroque style, "
        "rich textures, masterful brushstrokes, "
        "classical art, museum-quality"
    ),
}

# Detect image type from prompt keywords to pick a template
_IMAGE_TYPE_KEYWORDS: dict[str, list[str]] = {
    "portrait": ["portrait", "headshot", "face", "person", "persona", "character"],
    "landscape": ["landscape", "scenery", "mountain", "ocean", "sky", "horizon", "nature"],
    "product": ["product", "product photography", "showcase", "market", "advertisement"],
    "poster": ["poster", "movie poster", "flyer", "banner", "promotional"],
    "illustration": ["illustration", "concept art", "drawing", "sketch"],
    "anime": ["anime", "manga", "cel"],
    "oil painting": ["oil painting", "baroque", "classical painting"],
}


def detect_image_type(prompt: str) -> str:
    """Detect the image type from prompt keywords.

    Returns one of the keys in ``_STYLE_TEMPLATES`` (defaulting to
    ``"photorealistic"`` when no keyword matches).
    """
    if not prompt:
        return "photorealistic"
    lower = prompt.lower()
    for img_type, keywords in _IMAGE_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return img_type
    return "photorealistic"


def format_prompt(prompt: str, aspect_ratio: str = "16:9",
                  image_type: str | None = None,
                  override_quality: str | None = None,
                  override_style: str | None = None) -> str:
    """Expand a raw user prompt into a detailed, well-conditioned description.

    Mirrors DALL·E 3's approach of rewriting short prompts into richer
    descriptions before passing them to the diffusion model.  Uses structured
    templates for different image types (portrait, landscape, product, poster,
    illustration, anime, oil painting) so short prompts become recognisable
    rather than abstract.

    Args:
        prompt: The raw user prompt.
        aspect_ratio: Target aspect ratio string.
        image_type: Optional explicit image type override. If None, auto-detected
            from prompt keywords.
        override_quality: Optional explicit quality suffix.
        override_style: Optional explicit style keywords to insert.

    Returns:
        An enhanced prompt string suitable for SDXL-Lightning or SD 1.5.
    """
    if not prompt or not prompt.strip():
        return _QUALITY_KEYWORDS

    raw = prompt.strip()
    detected_type = image_type or detect_image_type(raw)
    template = _STYLE_TEMPLATES.get(detected_type)

    lower = raw.lower()
    has_quality = any(q in lower for q in ("8k", "4k", "hd", "high quality",
                                           "detailed", "cinematic", "sharp",
                                           "professional", "masterpiece"))
    has_style = any(s in lower for s in ("photorealistic", "oil painting",
                                          "digital art", "illustration", "anime",
                                          "cinematic", "photo", "realistic"))

    quality = override_quality if override_quality else (
        "" if has_quality else (", " + _QUALITY_KEYWORDS)
    )
    style = ""
    if override_style:
        style = f", {override_style}"
    elif not has_style and len(raw) < 200:
        # Only add generic style if the prompt is short and lacks style cues
        style = ", photorealistic"

    if template:
        enhanced = template.format(prompt=raw) + quality
    else:
        enhanced = raw + style + quality

    guidance = ""
    if aspect_ratio and aspect_ratio in _ASPECT_GUIDANCE:
        guidance = f", {_ASPECT_GUIDANCE[aspect_ratio]}"

    enhanced = enhanced + guidance
    logger.debug("Prompt enhanced: '%s' -> '%s'", raw[:60], enhanced[:60])
    return enhanced


def enhance_prompt(prompt: str, aspect_ratio: str = "16:9",
                   override_quality: str | None = None,
                   override_style: str | None = None) -> str:
    """Expand a raw user prompt into a detailed generation prompt.

    Delegates to :func:`format_prompt` with auto-detected image type.

    Mirrors DALL·E 3's approach of rewriting short prompts into richer
    descriptions before passing them to the diffusion model.

    Args:
        prompt: The raw user prompt (e.g. "a cat sitting on a couch").
        aspect_ratio: The target aspect ratio string (e.g. "16:9").
        override_quality: Optional explicit quality suffix; defaults to the
            standard SDXL quality keywords.
        override_style: Optional explicit style keywords to insert before the
            quality suffix.

    Returns:
        An enhanced prompt string suitable for SDXL-Lightning or SD 1.5.
    """
    return format_prompt(prompt, aspect_ratio,
                         override_quality=override_quality,
                         override_style=override_style)


def build_negative_prompt(negative_prompt: str | None = None) -> str:
    """Return the negative prompt to pass to the diffusion model.

    If the user provided one, use it (appended to the default). Otherwise, use
    the DEFAULT_NEGATIVE_PROMPT. This mirrors how DALL·E 3 and Midjourney
    apply implicit "avoid these things" conditioning.
    """
    if negative_prompt and negative_prompt.strip():
        return f"{negative_prompt.strip()}, {DEFAULT_NEGATIVE_PROMPT}"
    return DEFAULT_NEGATIVE_PROMPT


def build_generation_kwargs(
    spec,  # ModelSpec (forward-referenced to avoid import cycle)
    prompt: str,
    negative_prompt: str | None,
    aspect_ratio: str,
    seed: int | None,
    strength: float | None = None,
) -> dict:
    """Build the kwargs dict to pass to the diffusers pipeline ``__call__``.

    This is the bridge between Jalora's GenRequest model and the diffusers
    pipeline API. Each pipeline has a slightly different signature:

    - StableDiffusionXLPipeline: prompt, prompt_2, height, width,
      num_inference_steps, guidance_scale, negative_prompt, generator, ...
    - StableDiffusionImg2ImgPipeline: prompt, image, strength,
      num_inference_steps, guidance_scale, negative_prompt, generator, ...
    - StableDiffusionXLInpaintPipeline: prompt, image, mask_image,
      num_inference_steps, guidance_scale, negative_prompt, generator, ...

    Args:
        spec: The ModelSpec from model_registry.
        prompt: The (enhanced) positive prompt.
        negative_prompt: User-provided or default negative prompt.
        aspect_ratio: Aspect ratio string.
        seed: Seed integer (None = random).
        strength: Img2img strength (0.0-1.0), or None for txt2img/inpainting.

    Returns:
        A dict of kwargs suitable for ``pipe(**kwargs)``.
    """
    kwargs = {
        "prompt": prompt,
        "negative_prompt": build_negative_prompt(negative_prompt),
        "num_inference_steps": spec.num_inference_steps,
        "guidance_scale": spec.guidance_scale,
        "height": spec.height,
        "width": spec.width,
        "output_type": "pil",
    }

    # Seed -> torch.Generator
    if seed is not None:
        import torch
        kwargs["generator"] = torch.Generator(device="cpu").manual_seed(seed)

    # SDXL-specific params
    if spec.pipeline_class in ("StableDiffusionXLPipeline", "StableDiffusionXLInpaintPipeline"):
        # SDXL uses two text encoders; same prompt for both is the standard approach
        kwargs["prompt_2"] = prompt
        neg = build_negative_prompt(negative_prompt)
        kwargs["negative_prompt_2"] = neg
        # original_size = (height, width) for aspect-ratio awareness
        kwargs["original_size"] = (spec.height, spec.width)
        kwargs["target_size"] = (spec.height, spec.width)
        kwargs["negative_original_size"] = (spec.height, spec.width)
        kwargs["negative_target_size"] = (spec.height, spec.width)

    # Img2img strength
    if strength is not None and spec.pipeline_class == "StableDiffusionImg2ImgPipeline":
        kwargs["strength"] = strength

    return kwargs
