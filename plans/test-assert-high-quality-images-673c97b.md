# Plan — Test & Assert High-Quality Image Generation

## Overview

The image generation pipeline currently produces images that pass basic structural
validation (valid PNG, >500 bytes, correct metadata), but there are **no assertions**
on whether the generated pixels themselves constitute a "high quality" image. The
user's original complaint was abstract gradients — we fixed the root cause (torch not
installed → procedural fallback), but we need tests that **ascertain** quality so
regressions are caught in CI.

This plan adds a lightweight image-quality assessment layer and corresponding test
assertions, covering: color variance (not a flat/solid color), pixel diversity, edge
content, dimension correctness, multi-variation distinctness, and prompt enhancement
richness. All checks use only `numpy` + `PIL` (already installed) — no extra deps.

## Implementation approach

### Step 1 — Add `image_quality.py` assessment helpers

Create `app/services/creative/image_quality.py` with pure functions that compute
quality metrics from raw image bytes:

```python
def assess_image_quality(data: bytes) -> dict:
    """Return {quality_score, color_variance, entropy, distinct_colors,
    edge_density, width, height, passes_qa}."""
```

Metrics:
- **color_variance**: `np.std(arr, axis=(0,1)).mean()` across RGB channels.
  A solid-color image has near-zero variance; real images exceed ~8.0.
- **entropy**: Shannon entropy of the luminance histogram (0–8). Real images
  exceed ~4.0; flat gradients score <2.0.
- **distinct_colors**: count of unique RGB tuples (sampled). Real images have
  >500 distinct colors; procedural abstract art has >1000.
- **edge_density**: fraction of pixels with `|gradient| > threshold` via
  `np.gradient` on luminance. Filters out blank/flat images.
- **quality_score**: weighted combination (0–100). Threshold for `passes_qa`:
  color_variance > 5, entropy > 3, edge_density > 0.02.
- Reuses `_procedural_generate` output dimensions for dimension validation.

### Step 2 — Enhance `validate_image_output()` in `providers.py`

Extend `validate_image_output()` to accept an optional `min_quality: bool = False`
flag. When `True`, run `assess_image_quality()` and raise `CreativeEngineError`
(code `LOW_QUALITY`) if the image fails the quality gate. Wire this into:
- `_pipeline_call()`: call `validate_image_output(img_data, provider="local-ai",
  min_quality=True)` for AI outputs.
- `OpenAIImageProvider.generate()`: call with `min_quality=True` on both
  `b64_json` and URL paths.

This catches degenerate AI outputs (empty latents, collapsed denoising) at
the source, not just corrupt/empty bytes.

### Step 3 — Add quality metadata to `GenOutput`

In `_pipeline_call()` and `_procedural_generate()`, attach quality metrics to the
output `meta` dict so the API response and tests can read them:

```python
meta = {"ai": True, "provider": "local-ai", "model": spec.name,
        "quality_score": score, "color_variance": cv, ...}
```

### Step 4 — Add quality assertion tests to `test_phase_d_image_generation.py`

Add a new `TestImageQuality` class (~8 tests) that asserts:

1. **test_procedural_image_has_color_variance**: procedural output has
   `color_variance > 5.0` (not a flat gradient).
2. **test_procedural_image_not_solid_color**: distinct colors > 100.
3. **test_procedural_image_dimensions_match_aspect**: output dimensions match
   `get_aspect_sizes()` for the requested ratio.
4. **test_procedural_image_entropy_above_flat**: entropy > 3.0 (real image
   content, not a smooth gradient).
5. **test_ai_mocked_output_passes_quality**: the mocked pipeline output passes
   `assess_image_quality` with `passes_qa=True` (mocked 64×64 image has
   variance from the `(128, 64, 200)` color).
6. **test_multiple_variations_are_distinct**: 3 variations from same prompt/seed
   produce different `data` (different seeds → different pixels).
7. **test_enhanced_prompt_is_richer**: `format_prompt()` output is at least
   2× longer than input and contains quality keywords ("sharp focus",
   "high resolution").
8. **test_validate_image_output_rejects_solid_color**: a solid-color PNG bytes
   raise `CreativeEngineError("LOW_QUALITY")`.
9. **test_validate_image_output_rejects_corrupt**: truncated/corrupt bytes raise.

Also add an API-level quality test in `TestImageGenerationAPI`:
- **test_api_image_metadata_includes_quality**: response `metadata` includes
  `quality_score` key (when available).

### Step 5 — Update `_procedural_generate` to inject quality metrics

In `_procedural_generate()`, compute `assess_image_quality()` on each output and
attach the metrics to `meta` (with `quality_score: 0.0` since procedural is
not AI-generated — but the variance/entropy checks still validate it's not
broken).

### Step 6 — Update docs

Add a "High-Quality Image Testing" section to `docs/plan-visual-output.md`
documenting the quality metrics, thresholds, and how to tune them.

## Testing strategy

Run `python -m pytest tests/test_phase_d_image_generation.py -v --tb=long` and
expect all existing 27 tests + new ~9 quality tests to pass with 0 failures.
The quality assertions use `numpy` + `PIL` only (no model downloads, no GPU).
Mocked AI-path tests use the existing `_make_mock_pipeline()` pattern.
The `test_validate_image_output_rejects_solid_color` test creates a real
single-color PNG via `PIL.Image.new("RGB", (64,64), (128,128,128))` and
asserts it raises `CreativeEngineError`.
