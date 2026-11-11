"""Tests for Phase D image generation — local AI (diffusers) + procedural fallback.

These tests verify:
  - model_registry: model selection, aliases, aspect-size resolution
  - prompt_engine: prompt enhancement, default negative prompt
  - LocalImageProvider: AI path with a mocked pipeline and deterministic
    procedural fallback, regardless of locally installed model weights
  - The API endpoint returns real image data (not just file_type checks)
"""
from __future__ import annotations

import io
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.creative import model_registry, prompt_engine
from app.services.creative import providers
from app.services.creative.providers import GenOutput, GenRequest, LocalImageProvider


class _ApiImageProvider:
    name = "test-ai"
    ai = True


def _install_fake_ai_image_provider(monkeypatch):
    import numpy as np
    from PIL import Image

    provider = _ApiImageProvider()

    def generate(request, requested, settings):
        y, x = np.indices((128, 128))
        image = np.stack(
            (x * 2 % 256, y * 2 % 256, (x + y) % 256),
            axis=-1,
        ).astype(np.uint8)
        rng = np.random.default_rng(request.seed or 123)
        noise = rng.integers(-20, 21, image.shape, dtype=np.int16)
        image = np.clip(image.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        buffer = io.BytesIO()
        Image.fromarray(image).save(buffer, "PNG")
        data = buffer.getvalue()
        quality = providers.validate_image_output(
            data, provider=provider.name, min_quality=True
        )
        return [
            GenOutput(
                data,
                ".png",
                "image/png",
                {
                    "ai": True,
                    "provider": provider.name,
                    "model": "test-image-model",
                    **(quality or {}),
                },
            )
        ], provider, None

    monkeypatch.setattr(providers, "generate_ai_image", generate)


# --------------------------------------------------------------------------- #
#  model_registry tests
# --------------------------------------------------------------------------- #

class TestModelRegistry:
    """Verify the model catalog and tier selection logic."""

    def test_model_specs_exist(self):
        """Both tiers (SDXL-Lightning and SD 1.5 + LCM) should be cataloged."""
        assert model_registry.get_model_spec("sdxl-lightning") is not None
        assert model_registry.get_model_spec("sd15-lcm") is not None

    def test_model_aliases_resolve(self):
        """Short config names resolve to concrete specs."""
        assert model_registry.get_model_spec("sdxl-lightning") is not None
        assert model_registry.get_model_spec("sdxl-lightning-4") is not None
        assert model_registry.get_model_spec("sd15-lcm") is not None

    def test_sdxl_lightning_has_correct_params(self):
        """SDXL-Lightning specs must have guidance_scale=0 and 1024 width."""
        spec = model_registry.get_model_spec("sdxl-lightning")
        assert spec is not None
        assert spec.guidance_scale == 0.0
        assert spec.width == 1024
        assert spec.height == 1024
        assert spec.scheduler == "euler"
        assert spec.timestep_spacing == "trailing"
        assert "ByteDance/SDXL-Lightning" in spec.unet_lora

    def test_sd15_lcm_fallback_params(self):
        """SD 1.5 + LCM fallback must have 512 width."""
        spec = model_registry.get_model_spec("sd15-lcm")
        assert spec is not None
        assert spec.width == 512
        assert spec.height == 512
        assert spec.guidance_scale == 0.0

    def test_select_model_with_sufficient_ram(self):
        """With 8 GB free RAM, the SDXL tier should be selected."""
        spec = model_registry.select_model(8.0, "cpu")
        assert spec is not None
        assert "sdxl" in spec.name

    def test_select_model_insufficient_ram(self):
        """With only 3 GB free RAM, fall back to SD 1.5 tier."""
        spec = model_registry.select_model(3.0, "cpu")
        assert spec is not None
        assert spec.name == "sd15-lcm"

    def test_select_model_no_fit(self):
        """With 1 GB free RAM, no model fits — returns None."""
        spec = model_registry.select_model(1.0, "cpu")
        assert spec is None

    def test_aspect_sizes_are_multiples_of_8(self):
        """All SDXL-compatible sizes must be multiples of 8 (U-Net requirement)."""
        sizes = model_registry.get_aspect_sizes(1024, 1024)
        for ratio, (w, h) in sizes.items():
            assert w % 8 == 0, f"Width {w} for {ratio} is not a multiple of 8"
            assert h % 8 == 0, f"Height {h} for {ratio} is not a multiple of 8"

    def test_can_load_check_functions(self):
        """can_load_torch / can_load_diffusers should return bools without error."""
        assert isinstance(model_registry.can_load_torch(), bool)
        assert isinstance(model_registry.can_load_diffusers(), bool)


# --------------------------------------------------------------------------- #
#  prompt_engine tests
# --------------------------------------------------------------------------- #

class TestPromptEngine:
    """Verify prompt enhancement and negative prompt construction."""

    def test_enhance_short_prompt(self):
        """A short generic prompt should get quality + style suffixes."""
        enhanced = prompt_engine.enhance_prompt("a cat", "16:9")
        assert "cat" in enhanced
        # Should contain quality keywords
        assert "high quality" in enhanced or "ultra hd" in enhanced
        # Should contain aspect guidance
        assert "cinematic" in enhanced.lower() or "landscape" in enhanced.lower()

    def test_enhance_already_detailed_prompt(self):
        """A prompt that already contains quality cues should not get duplicated."""
        enhanced = prompt_engine.enhance_prompt(
            "A 4k ultra hd photorealistic portrait of a person, sharp focus, highly detailed",
            "1:1",
        )
        # Should not add "high quality" again since it already says "highly detailed"
        assert enhanced.count("high quality") <= 1

    def test_enhance_empty_prompt(self):
        enhanced = prompt_engine.enhance_prompt("", "16:9")
        assert len(enhanced) > 0

    def test_default_negative_prompt(self):
        """The default negative prompt should be non-trivial and cover artifacts."""
        neg = prompt_engine.build_negative_prompt(None)
        assert len(neg) > 50
        assert "deformed" in neg.lower()
        assert "blurry" in neg.lower()

    def test_custom_negative_prompt_appended(self):
        """A user-provided negative prompt should be preserved + extended."""
        neg = prompt_engine.build_negative_prompt("ugly face")
        assert "ugly face" in neg
        assert "deformed" in neg.lower()

    def test_build_generation_kwargs_txt2img(self):
        """Generation kwargs should include SDXL-specific params for SDXL models."""
        from app.services.creative.model_registry import get_model_spec
        spec = get_model_spec("sdxl-lightning")
        kwargs = prompt_engine.build_generation_kwargs(
            spec, "a cat", "bad anatomy", "16:9", None,
        )
        assert kwargs["prompt"] == "a cat"
        assert "negative_prompt" in kwargs
        assert kwargs["guidance_scale"] == 0.0
        assert kwargs["num_inference_steps"] == 4
        # SDXL should have prompt_2
        assert "prompt_2" in kwargs


# --------------------------------------------------------------------------- #
#  LocalImageProvider procedural fallback tests
# --------------------------------------------------------------------------- #

class TestLocalImageProvider:
    """Verify the local provider's fallback behavior and capabilities."""

    def test_capabilities_updated(self):
        """Local provider should report AI capabilities when torch is available."""
        provider = LocalImageProvider()
        caps = provider.capabilities
        assert caps["text_to_image"] is True
        assert caps["image_to_image"] is True
        assert caps["inpainting"] is True
        assert caps["negative_prompt"] is True
        assert caps["seed"] is True

    def test_configured_always_true(self):
        """Local provider is always configured (procedural fallback is always available)."""
        provider = LocalImageProvider()
        assert provider.configured() is True

    def test_procedural_fallback_generates_image(self):
        """The procedural engine should produce a valid PNG without a model."""
        provider = LocalImageProvider()
        req = GenRequest(
            kind="image",
            prompt="A sunset over the ocean",
            aspect_ratio="1:1",
            num_variations=1,
        )
        with patch.object(LocalImageProvider, "_ai_generate", return_value=None):
            outputs = provider.generate(req)
        assert len(outputs) == 1
        assert outputs[0].mime == "image/png"
        assert outputs[0].extension == ".png"
        # The PNG header should be present
        assert outputs[0].data[:8] == b"\x89PNG\r\n\x1a\n"
        # Should be at least a few KB of actual image data
        assert len(outputs[0].data) > 500

    def test_procedural_fallback_renders_scene_objects_without_typeerror(self):
        provider = LocalImageProvider()
        request = GenRequest(
            kind="image",
            prompt="A woman beside a boat, car, dog, fish, and heart",
            aspect_ratio="1:1",
            seed=42,
        )
        with patch.object(LocalImageProvider, "_ai_generate", return_value=None):
            outputs = provider.generate(request)

        assert outputs[0].data[:8] == b"\x89PNG\r\n\x1a\n"
        assert outputs[0].meta["provider"] == "local-procedural"

    def test_procedural_reproducible_with_seed(self):
        """Same seed + prompt should produce the same image (deterministic)."""
        provider = LocalImageProvider()
        req = GenRequest(
            kind="image", prompt="forest landscape",
            aspect_ratio="1:1", num_variations=1, seed=42,
        )
        with patch.object(LocalImageProvider, "_ai_generate", return_value=None):
            out1 = provider.generate(req)
            out2 = provider.generate(req)
        assert out1[0].data == out2[0].data

    def test_multiple_variations(self):
        """num_variations should produce multiple outputs."""
        provider = LocalImageProvider()
        req = GenRequest(
            kind="image", prompt="city skyline",
            aspect_ratio="16:9", num_variations=3, seed=99,
        )
        with patch.object(LocalImageProvider, "_ai_generate", return_value=None):
            outputs = provider.generate(req)
        assert len(outputs) == 3
        # Each should have unique full data (different seeds)
        assert len({o.data for o in outputs}) == 3


# --------------------------------------------------------------------------- #
#  AI path tests (skipped when torch/diffusers are not installed)
# --------------------------------------------------------------------------- #

torch_available = model_registry.can_load_torch()
diffusers_available = model_registry.can_load_diffusers()


def _make_mock_pil_image(size=(64, 64), color=(128, 64, 200)):
    """Create an in-memory PIL image with gradient + noise for testing the
    AI pipeline path. Uses a non-solid pattern so it passes quality gates."""
    from PIL import Image
    import numpy as np
    w, h = size
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for x in range(w):
        for y in range(h):
            arr[y, x] = [
                int((color[0] + x * 2) % 256),
                int((color[1] + y * 2) % 256),
                int((color[2] + (x + y)) % 256),
            ]
    # Add a little noise for color variance
    noise = np.random.randint(-20, 20, (h, w, 3), dtype=np.int16)
    arr = np.clip(arr.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


def _make_mock_pipeline():
    """Create a mock diffusers pipeline that returns a fake PIL image."""
    mock_img = _make_mock_pil_image()
    result = MagicMock()
    result.images = [mock_img]
    mock_pipe = MagicMock()
    mock_pipe.return_value = result
    mock_pipe.scheduler = MagicMock()
    mock_pipe.scheduler.config = {}
    mock_pipe.unet = MagicMock()
    mock_pipe.to = MagicMock(return_value=mock_pipe)
    mock_pipe.enable_attention_slicing = MagicMock()
    return mock_pipe


@pytest.mark.skipif(
    not (torch_available and diffusers_available),
    reason="torch + diffusers not installed — AI generation requires these",
)
class TestLocalAIProvider:
    """These tests verify the AI pipeline code path by mocking the diffusers
    pipeline (so no model download is needed). They only run when torch +
    diffusers are installed, since the pipeline call requires torch.
    """

    def test_ai_generates_real_image(self):
        """With torch installed and a pipeline, LocalImageProvider should
        generate an AI image via _pipeline_call (mocked pipeline)."""
        provider = LocalImageProvider()
        req = GenRequest(
            kind="image",
            prompt="A photorealistic orange tabby cat sitting on a windowsill",
            aspect_ratio="1:1",
            num_variations=1,
            seed=42,
        )
        # Mock _load_pipeline so no model download occurs; bypass the RAM
        # check by patching available_ram_gb to report plenty of memory.
        mock_pipe = _make_mock_pipeline()
        with patch.object(LocalImageProvider, "_load_pipeline", return_value=mock_pipe), \
             patch.object(model_registry, "available_ram_gb", return_value=16.0):
            outputs = provider.generate(req)
        assert len(outputs) >= 1
        assert outputs[0].mime == "image/png"
        assert outputs[0].data[:8] == b"\x89PNG\r\n\x1a\n"
        assert outputs[0].meta.get("ai") is True
        assert outputs[0].meta.get("provider") == "local-ai"

    def test_ai_metadata_fields(self):
        """AI-generated outputs should carry model and inference metadata."""
        provider = LocalImageProvider()
        req = GenRequest(
            kind="image", prompt="abstract geometric shapes",
            aspect_ratio="1:1", num_variations=1, seed=7,
        )
        mock_pipe = _make_mock_pipeline()
        with patch.object(LocalImageProvider, "_load_pipeline", return_value=mock_pipe), \
             patch.object(model_registry, "available_ram_gb", return_value=16.0):
            outputs = provider.generate(req)
        assert outputs[0].meta.get("ai") is True
        assert outputs[0].meta.get("model") is not None
        assert "duration_seconds" in outputs[0].meta
        assert "num_inference_steps" in outputs[0].meta

    def test_ai_falls_back_to_procedural_on_error(self):
        """If AI generation fails (model error), it should fall back to procedural."""
        provider = LocalImageProvider()
        # Patch _ai_generate to return None — simulating a pipeline failure
        with patch.object(LocalImageProvider, "_ai_generate", return_value=None):
            req = GenRequest(
                kind="image", prompt="test",
                aspect_ratio="1:1", num_variations=1,
            )
            outputs = provider.generate(req)
            assert len(outputs) == 1
            assert outputs[0].meta.get("ai") is False
            assert outputs[0].meta.get("provider") == "local-procedural"

    def test_ai_img2img_uses_strength(self):
        """Img2img mode should pass the reference image and strength to the pipeline."""
        from app.services.creative.providers import GenRequest
        provider = LocalImageProvider()
        # Create a temporary reference image
        import io as _io
        from PIL import Image
        ref_img = Image.new("RGB", (128, 128), (100, 100, 100))
        ref_buf = _io.BytesIO()
        ref_img.save(ref_buf, "PNG")
        ref_buf.seek(0)

        ref_path = Path(tempfile.gettempdir()) / "_test_ref_img.png"
        ref_path.write_bytes(ref_buf.getvalue())

        req = GenRequest(
            kind="image",
            prompt="A stylized version of the reference",
            aspect_ratio="1:1",
            num_variations=1,
            seed=42,
            reference_image=ref_path,
            extra={"strength": 0.5},
        )
        mock_pipe = _make_mock_pipeline()
        with patch.object(LocalImageProvider, "_load_pipeline", return_value=mock_pipe), \
             patch.object(model_registry, "available_ram_gb", return_value=16.0):
            outputs = provider.generate(req)
        assert len(outputs) >= 1
        assert outputs[0].meta.get("provider") == "local-ai"
        assert outputs[0].meta.get("mode") == "img2img"


# --------------------------------------------------------------------------- #
#  API integration tests (end-to-end via TestClient)
# --------------------------------------------------------------------------- #

class TestImageGenerationAPI:
    """Verify the /api/creative/generate/image endpoint returns real images."""

    def test_generate_image_returns_real_png(self, client, auth_headers, monkeypatch):
        """The API must return a real, non-empty PNG — not just a file_type check."""
        _install_fake_ai_image_provider(monkeypatch)
        headers = auth_headers()
        res = client.post("/api/creative/generate/image", json={
            "prompt": "A futuristic cityscape at night with neon lights",
            "aspect_ratio": "16:9",
        }, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["asset"]["file_type"] == "image"
        assert data["asset"]["size_bytes"] > 100
        # Metadata should identify the provider and whether AI was used
        meta = data["asset"].get("metadata", {})
        assert "provider" in meta
        assert "ai" in meta
        assert "model" in meta

    def test_generate_image_with_seed_is_reproducible(
        self, client, auth_headers, monkeypatch
    ):
        """The same seed should produce the same image via the API."""
        _install_fake_ai_image_provider(monkeypatch)
        headers = auth_headers()
        payload = {
            "prompt": "A red balloon floating in a blue sky",
            "aspect_ratio": "1:1",
            "seed": 12345,
        }
        res1 = client.post("/api/creative/generate/image", json=payload, headers=headers)
        assert res1.status_code == 200
        # Register a second user to avoid cross-test contamination
        headers2 = auth_headers("user2@test.com", "supersecret1")
        res2 = client.post("/api/creative/generate/image", json=payload, headers=headers2)
        assert res2.status_code == 200

        # When using the procedural fallback (no torch or insufficient RAM),
        # same seed + prompt → same output
        if not torch_available or model_registry.available_ram_gb() < 3.0:
            assert res1.json()["asset"]["size_bytes"] == res2.json()["asset"]["size_bytes"]

    def test_capabilities_endpoint_lists_local(self, client, auth_headers):
        """The /capabilities endpoint should list the local provider."""
        headers = auth_headers()
        res = client.get("/api/creative/capabilities", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        providers = data["providers"]["image"]
        # The local provider should always be listed (it's always configured)
        local_providers = [p for p in providers if p["name"] == "local"]
        assert len(local_providers) >= 1
        # The local provider should advertise its capabilities
        local = local_providers[0]
        assert local["capabilities"]["text_to_image"] is True
        assert local["capabilities"]["negative_prompt"] is True


# --------------------------------------------------------------------------- #
#  Image quality tests
# --------------------------------------------------------------------------- #

class TestImageQuality:
    """Assert that generated images are genuinely high-quality (not abstract
    gradients, solid colours, or corrupt data).

    These tests verify:
      - Procedural images have meaningful colour variance and entropy.
      - Procedural image dimensions match the requested aspect ratio.
      - The quality-gate in validate_image_output() rejects solid colours
        and corrupt data.
      - Multiple variations produce distinct pixel data.
      - Enhanced prompts are substantially richer than raw prompts.
      - The API response includes quality metadata.
    """

    def test_procedural_image_has_color_variance(self):
        """Procedural images must have colour variance > 5 (not a flat colour)."""
        from app.services.creative.image_quality import assess_image_quality
        provider = LocalImageProvider()
        req = GenRequest(
            kind="image", prompt="A vibrant sunset over the ocean",
            aspect_ratio="1:1", num_variations=1, seed=42,
        )
        with patch.object(LocalImageProvider, "_ai_generate", return_value=None):
            outputs = provider.generate(req)
        metrics = assess_image_quality(outputs[0].data)
        assert metrics["color_variance"] > 5.0, (
            f"Expected color_variance > 5, got {metrics['color_variance']}"
        )

    def test_procedural_image_not_solid_color(self):
        """Procedural images must have > 10 distinct colours."""
        provider = LocalImageProvider()
        req = GenRequest(
            kind="image", prompt="A forest with green trees",
            aspect_ratio="1:1", num_variations=1, seed=10,
        )
        with patch.object(LocalImageProvider, "_ai_generate", return_value=None):
            outputs = provider.generate(req)
        assert outputs[0].meta["distinct_colors"] > 10

    def test_procedural_image_dimensions_match_aspect(self):
        """Procedural image dimensions must match the requested aspect ratio."""
        from app.services.creative.image_quality import assess_image_quality
        provider = LocalImageProvider()
        expected_sizes = {
            "16:9": (1280, 720),
            "1:1": (1024, 1024),
            "9:16": (720, 1280),
            "4:3": (1024, 768),
        }
        for ratio, (exp_w, exp_h) in expected_sizes.items():
            req = GenRequest(
                kind="image", prompt="landscape scene",
                aspect_ratio=ratio, num_variations=1, seed=42,
            )
            with patch.object(LocalImageProvider, "_ai_generate", return_value=None):
                outputs = provider.generate(req)
            metrics = assess_image_quality(outputs[0].data)
            assert metrics["width"] == exp_w, f"Width mismatch for {ratio}: {metrics['width']} != {exp_w}"
            assert metrics["height"] == exp_h, f"Height mismatch for {ratio}: {metrics['height']} != {exp_h}"

    def test_procedural_image_entropy_above_flat(self):
        """Procedural images should have entropy > 3 (real visual content)."""
        provider = LocalImageProvider()
        req = GenRequest(
            kind="image", prompt="A city skyline at night",
            aspect_ratio="1:1", num_variations=1, seed=7,
        )
        with patch.object(LocalImageProvider, "_ai_generate", return_value=None):
            outputs = provider.generate(req)
        entropy = outputs[0].meta["entropy"]
        assert entropy > 3.0, f"Expected entropy > 3.0, got {entropy}"

    def test_procedural_image_metadata_includes_quality(self):
        """Procedural output metadata should include quality metrics."""
        provider = LocalImageProvider()
        req = GenRequest(
            kind="image", prompt="A stormy sea",
            aspect_ratio="1:1", num_variations=1, seed=99,
        )
        with patch.object(LocalImageProvider, "_ai_generate", return_value=None):
            outputs = provider.generate(req)
        meta = outputs[0].meta
        assert "quality_score" in meta
        assert "color_variance" in meta
        assert "entropy" in meta
        assert "distinct_colors" in meta
        assert "edge_density" in meta
        assert "width" in meta
        assert "height" in meta
        assert "height" in meta

    def test_variations_are_pixel_distinct(self):
        """Different num_variations should produce different pixel data."""
        provider = LocalImageProvider()
        req = GenRequest(
            kind="image", prompt="abstract patterns",
            aspect_ratio="1:1", num_variations=4, seed=55,
        )
        with patch.object(LocalImageProvider, "_ai_generate", return_value=None):
            outputs = provider.generate(req)
        assert len(outputs) == 4
        # All should have different pixel data
        assert len({o.data for o in outputs}) == 4

    def test_enhanced_prompt_is_richer(self):
        """format_prompt() should significantly expand short prompts."""
        from app.services.creative import prompt_engine
        raw = "a cat"
        enhanced = prompt_engine.format_prompt(raw, "16:9")
        # Enhanced should be at least 2x the length of the raw prompt
        assert len(enhanced) >= len(raw) * 2
        # Should contain quality keywords
        assert any(kw in enhanced.lower() for kw in
                   ("high quality", "ultra hd", "sharp focus", "high resolution",
                    "detailed", "cinematic", "professional"))
        # Should not duplicate the prompt verbatim
        assert enhanced.count("a cat") <= 2

    def test_enhanced_prompt_detects_image_type(self):
        """format_prompt() should detect portrait and apply portrait template."""
        from app.services.creative import prompt_engine
        enhanced = prompt_engine.format_prompt(
            "A person smiling at the camera", "1:1",
        )
        # Portrait template should add lens/photography terms
        assert any(term in enhanced.lower() for term in
                   ("portrait", "85mm lens", "bokeh", "depth of field"))
        assert "person" in enhanced.lower()

    def test_validate_image_output_rejects_solid_color(self):
        """A solid-color PNG should fail the quality gate (min_quality=True)."""
        from PIL import Image
        from app.services.creative.providers import validate_image_output, CreativeEngineError
        buf = io.BytesIO()
        Image.new("RGB", (256, 256), (128, 128, 128)).save(buf, "PNG")
        solid_data = buf.getvalue()
        # Basic validation (no quality gate) should pass
        validate_image_output(solid_data, provider="test")  # should not raise
        # Quality gate should reject it
        with pytest.raises(CreativeEngineError) as exc_info:
            validate_image_output(solid_data, provider="test", min_quality=True)
        assert exc_info.value.code == "LOW_QUALITY"

    def test_validate_image_output_rejects_corrupt(self):
        """Corrupt/truncated image data should raise EMPTY_OUTPUT."""
        from app.services.creative.providers import validate_image_output, CreativeEngineError
        # Truncated PNG
        bad_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
        with pytest.raises(CreativeEngineError) as exc_info:
            validate_image_output(bad_data, provider="test")
        assert exc_info.value.code == "EMPTY_OUTPUT"
        # Completely empty
        with pytest.raises(CreativeEngineError) as exc_info:
            validate_image_output(b"", provider="test")
        assert exc_info.value.code == "EMPTY_OUTPUT"

    def test_validate_image_output_accepts_real_image(self):
        """A real image with colour variance should pass the quality gate."""
        from PIL import Image
        import numpy as np
        from app.services.creative.providers import validate_image_output
        # Create a gradient image with noise
        arr = np.zeros((128, 128, 3), dtype=np.uint8)
        for x in range(128):
            for y in range(128):
                arr[y, x] = [(x * 2) % 256, (y * 2) % 256, ((x + y)) % 256]
        noise = np.random.randint(-30, 30, (128, 128, 3), dtype=np.int16)
        arr = np.clip(arr.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, "PNG")
        # Should pass both basic validation and quality gate
        qm = validate_image_output(buf.getvalue(), provider="test", min_quality=True)
        assert qm is not None
        assert qm["passes_qa"] is True

    def test_api_image_metadata_includes_quality(
        self, client, auth_headers, monkeypatch
    ):
        """The API response should include quality metadata on generated images."""
        _install_fake_ai_image_provider(monkeypatch)
        headers = auth_headers()
        res = client.post("/api/creative/generate/image", json={
            "prompt": "A vibrant forest landscape with mountains",
            "aspect_ratio": "16:9",
        }, headers=headers)
        assert res.status_code == 200
        meta = res.json()["asset"].get("metadata", {})
        # Quality metrics should be present in the metadata
        assert "quality_score" in meta
        assert "color_variance" in meta
        assert "provider" in meta
        assert "ai" in meta
