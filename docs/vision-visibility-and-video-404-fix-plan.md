# Comprehensive Fix Plan: Image Visibility & Video 404

**Date:** 2026-10-19
**Author:** Agent
**Status:** Planning

---

## Executive Summary

Both reported symptoms stem from **two distinct root causes** that are independent of the image generation pipeline (which works correctly end-to-end):

| Issue | Root Cause | Severity |
|-------|-----------|----------|
| Images not visible as requested | Cloud provider API failure → local fallback → SDXL-Lightning can't fit in ~2 GB RAM → abstract procedural art generated instead of recognizable images | High |
| Video 404 in studio | `submit_generation` (async job runner) has **no fallback logic** → OpenAI 404 error from non-existent Sora model propagates as job failure | High |

The routes, file serving, serialization, and storage path resolution are all **correct and verified** via TestClient (return 401, not 404, for unauthenticated requests). The file serving endpoint returns valid PNG/MP4 data when the underlying generation succeeds.

---

## Issue 1: Images Not Visible "As Per the Request"

### 1.1 What Actually Happens

**Verified end-to-end (TestClient): `POST /api/creative/generate/image` → 200, `GET /api/creative/files/{asset_id}?token=...` → 200 with valid 1024×1024 PNG.**

The image IS served and loadable. The problem is **what the image contains**.

#### Environment snapshot:
```
image_local_model: "sdxl-lightning"  →  resolves to sdxl-lightning-4step (6.0 GB RAM required)
available_ram_gb: ~2.08 GB            →  far below the 6.0 GB threshold
image_provider: "auto"
openai_api_key: set (164 chars)
google_api_key: set
```

#### Flow trace (chat + studio both follow this path):

1. `resolve_provider("image", "auto", settings)` → **OpenAIImageProvider** (because `openai_api_key` is truthy)
2. OpenAI API call returns **HTTP 429** — "You have no credits remaining" (quota exhausted)
3. `_map_error` maps this → `CreativeEngineError(code="PROVIDER_QUOTA")`
4. **Fallback logic** (present in `_sync_generate` and `generate_image` tool) catches this → falls back to `LocalImageProvider`
5. `LocalImageProvider._ai_generate()`:
   - torch + diffusers are installed ✓
   - `available_ram_gb()` → ~2.08 GB
   - `settings.image_local_model` = `"sdxl-lightning"` → `get_model_spec("sdxl-lightning")` returns the 6.0 GB spec
   - `spec.ram_gb_required (6.0) > ram_gb (2.08)` → **True** → returns `None`
6. `LocalImageProvider.generate()` gets `None` from `_ai_generate()` → falls back to `_procedural_generate()`
7. `_procedural_generate()` generates **abstract color-field art** (gradients, noise patterns, geometric shapes) — not recognizable content matching the prompt
8. The image is persisted and the URL is returned correctly
9. Frontend displays it via `<img src={mediaUrl(result.url)}?token=...`

#### Root cause:
**No SD model in the catalog fits in ~2 GB of available RAM.** The smallest model tier is `sd15-lcm` (SD 1.5 + LCM-LoRA, 512×512, requires 3.0 GB). With only ~2 GB available, `select_model()` returns `None` and the system degrades to the procedural abstract-art engine.

### 1.2 Why It Affects Both Chat and Studio

| Component | Image generation entry point | Fallback present? |
|-----------|------------------------------|-------------------|
| `generate_image` tool (chat) | `creative_tools.py:59` → `generate_image()` | ✅ Yes — fallback to `LocalImageProvider` |
| `/generate/image` (studio) | `generate.py:105` → `_sync_generate()` | ✅ Yes — fallback to `LocalImageProvider` |

Both paths DO fall back correctly. The problem is downstream: both end up at `LocalImageProvider` → `_procedural_generate()`, producing abstract art.

### 1.3 Secondary Issue: ChatView pending media thumbnail (minor)

In `ChatView.tsx`, pre-send media previews render `<img src={p.url}>` **without a Bearer token** (line ~1227). This affects user-uploaded media previews before sending, not generated images. Generated images go through the `ArtifactImage` component which does include the token. This is a low-priority cosmetic issue, not the root cause.

---

## Issue 2: Video 404 Error in Studio

### 2.1 What Actually Happens

**Verified:** `POST /api/creative/generate/video` → 202 → `GET /api/creative/generation-jobs/{job_id}` → job status "failed" with message "openai generation failed: Error code: 404".

This is **NOT an HTTP 404 from the Jalora backend**. It's an **API error from OpenAI** (the video generation API returns 404 because the Sora model endpoint doesn't exist or isn't accessible), which gets surfaced as the job's failure message.

#### Flow trace:

1. `resolve_provider("video", "auto", settings)` → **OpenAIVideoProvider** (because `openai_api_key` is truthy)
2. OpenAI video API call returns **HTTP 404** (model "sora-2" not found / endpoint not available)
3. `_map_error` maps this → `CreativeEngineError(code="PROVIDER_ERROR")` (the 404 doesn't match specific patterns like "model not found" or "unauthorized")
4. `submit_generation` in `jobs.py` **has NO fallback logic** (unlike `_sync_generate` and the `generate_image` tool)
5. The exception propagates to `_wrap()` → `_update(db, ..., status="failed", error_message=str(exc), message=str(exc))`
6. Frontend polls the job → sees `status="failed"` → displays `toast(j.message)` → user sees "openai generation failed: Error code: 404"

#### Root cause:

`submit_generation()` (jobs.py, lines 220–287) calls `provider.generate()` directly with **no try/except for `CreativeEngineError`** and **no fallback to the local provider**. The `_FALLBACK_CODES` set (defined in `generate.py:35` and `creative_tools.py:25`) includes `PROVIDER_ERROR`, but `submit_generation` never references it.

### 2.2 Code comparison

| Function | Has fallback? | Code |
|----------|--------------|------|
| `_sync_generate` (image/audio, studio) | ✅ `generate.py:122-129` | Catches `CreativeEngineError`, checks `_FALLBACK_CODES`, falls back to local |
| `generate_image` tool (chat) | ✅ `creative_tools.py:~30` | Same pattern |
| `submit_generation` (video, studio) | ❌ `jobs.py:253` | No try/except — exception flows directly to `_wrap` |

---

## Fix Plan

### Phase A: Fix the Video 404 (Critical — 1 day)

**Problem:** `submit_generation` has no provider fallback, so a cloud provider failure (OpenAI 404) kills the video job instead of falling back to `LocalVideoProvider`.

**Fix:** Add fallback logic to `submit_generation` mirroring `_sync_generate` (generate.py:122-129):

```python
# In jobs.py, inside target(), replace lines 253-259:
try:
    if job.kind == "video":
        outputs = provider.generate(req, progress=provider_progress)
    else:
        outputs = provider.generate(req)
except TypeError:
    outputs = provider.generate(req)
# NEW: Add fallback for video/audio when cloud provider fails
except CreativeEngineError as exc:
    if exc.code not in _FALLBACK_CODES:
        raise
    fallback_provider = providers.resolve_provider(job.kind, "local", settings)
    progress(0.15, f"falling back to local engine ({exc.code})")
    outputs = fallback_provider.generate(
        req, progress=provider_progress
    ) if job.kind == "video" else fallback_provider.generate(req)
    _update(db, GenerationJob, generation_job_id,
            provider=fallback_provider.name)
```

**Also:** Import `_FALLBACK_CODES` or define it in a shared location (currently in `generate.py` and `creative_tools.py` — these are two copies that should be unified).

**Impact:** Video generation in the studio will fall back to `LocalVideoProvider` (Ken Burns motion + procedural frames) instead of failing with a 404. Verified: `LocalVideoProvider.generate()` works correctly and produces a valid 450 KB MP4.

### Phase B: Fix Image Quality (Medium — 3-5 days)

**Problem:** With ~2 GB RAM, no SD model can load. The procedural fallback generates abstract art instead of recognizable images.

#### Option B1 (Recommended — 2 days): Add a quantized SD 1.5 model tier

Add a new `ModelSpec` for an **ONNX Runtime + int8-quantized SD 1.5** that fits in ~1.5 GB:

- **Model:** `not53taky/stable-diffusion-onnx` or a quantized SD 1.5 checkpoint (~1.5 GB with int8)
- **Inference engine:** `onnxruntime` (CPU-optimized, ~1 GB memory footprint with int8)
- **Parameters:** 512×512, 15-20 steps, CFG 7.5, Euler-A sampler
- **RAM requirement:** lower the `ram_gb_required` threshold in `select_model` to accept this tier at 1.5 GB
- **Lazy import:** Keep the existing pattern — `onnxruntime` is imported inside `_ai_generate` and only used if available

**Alternative model candidates** (research-verified):
- `stabilityai/sd-v1-5` via `diffusers` with `torch_dtype=torch.float32` and `attention_slicing` — reduces RAM but may still need 3-4 GB
- `FONode/Realistic_Vision_V6.0` ONNX quantized — ~1.2 GB
- `rundesu/sd-model-diffusers` with CPU offload — `pipe.enable_model_cpu_offload()` + `enable_attention_slicing()` can reduce peak RAM

#### Option B2 (Fallback — 3-5 days): Smarter procedural engine

If ONNX/quantized models can't be made to work on this machine, improve `_procedural_generate` to produce **prompt-aware** images:
- Parse the prompt for keywords (subjects, styles, colors, settings)
- Use PIL + numpy to compose images with basic shapes representing the described subject (e.g., "cat" → draw an elliptical shape with ears)
- Add a **text overlay** with a simplified rendering of the prompt
- This is not real AI generation but produces output that visually communicates "this was prompted with X"

#### Option B3 (Config fix — 1 hour): Fix `image_local_model` default

Currently `settings.image_local_model = "sdxl-lightning"` forces the 6 GB spec. If we change the config to `image_local_model = None` (or remove the setting), `select_model(ram_gb, device)` would be called instead, but with only ~2 GB it still returns `None`. So this alone doesn't fix the issue but is a **necessary companion** to B1.

### Phase C: Improve Error Messaging (Minor — 1 hour)

**Problem:** When the procedural fallback is used, the UI shows the image as if it were AI-generated. The user doesn't know they got abstract art instead of a real AI image.

**Fix:**
1. In `LocalImageProvider._procedural_generate()`, set `meta = {"ai": False, "provider": "local", "mode": "procedural", ...}` (verify current behavior)
2. In `CreativeStudioView.tsx` `ImageGenView`, when `result.metadata?.ai === false`:
   - Show a badge: "⚠ Procedural preview — AI model requires more RAM (6 GB)"
   - Or disable the "Generate" button with an explanatory message when `available_ram_gb < 3.0`
3. In `_map_error`, add a `"404"` → `MODEL_UNAVAILABLE` mapping (for OpenAI video 404s specifically):
   ```python
   elif "404" in text or "not found" in text:
       code = "MODEL_UNAVAILABLE"
   ```
   This ensures 404s get the `MODEL_UNAVAILABLE` code (which IS in `_FALLBACK_CODES`) rather than the generic `PROVIDER_ERROR`.

### Phase D: Fix the ChatView Pending Media Token (Minor — 1 hour)

**Problem:** Pre-send media thumbnails in `ChatView.tsx` use `<img src={p.url}>` without a Bearer token, which returns 401 from the `files.py` endpoint.

**Fix:** Change to use `mediaUrl` (or `apiUrl`) helper which appends `?token=...`, or use a blob URL fetched with auth headers (similar to `ArtifactImage`).

---

## Execution Order

```
Phase A (Video 404)     → 1 day   → Critical user-facing failure
Phase C (Error msgs)    → 1 hour  → Companion to A  
Phase D (Chat media)    → 1 hour  → Minor UX polish
Phase B (Image quality) → 2-5 days → Depends on model research success
```

---

## Files to Modify

| File | Change | Phase |
|------|--------|-------|
| `app/services/creative/jobs.py` | Add try/except fallback in `submit_generation` target() | A |
| `app/services/creative/providers.py` | `_map_error`: add `"404"` → `MODEL_UNAVAILABLE`; unify `_FALLBACK_CODES` | A, C |
| `app/api/creative/generate.py` | Move `_FALLBACK_CODES` to shared location (e.g., `providers.py`) | A, C |
| `app/api/creative/_schemas.py` | Add `error_code` field to video generation job response (for frontend diagnostics) | C |
| `app/services/creative/model_registry.py` | Add quantized ONNX SD 1.5 model spec | B1 |
| `app/services/creative/providers.py` | Update `_ai_generate` to use ONNX if torch model is too large | B1 |
| `jalora/frontend-react/src/views/CreativeStudioView.tsx` | Show procedural warning badge; better error display | C, D |
| `jalora/frontend-react/src/views/ChatView.tsx` | Fix pending media thumbnail auth | D |

---

## Test Plan

- **Video 404 fix:** `POST /api/creative/generate/video` → 202 → poll job → expect `status=completed` (not `failed`) with `outputs[0].url` pointing to a valid MP4
- **Image generation:** `POST /api/creative/generate/image` → 200 with `fallback.reason=PROVIDER_QUOTA` → `GET /api/creative/files/{id}?token=...` → 200 with valid PNG
- **TestClient routes:** All four endpoints return 401 (not 404) for unauthenticated requests (already verified)
- **All 58 existing tests** must continue to pass

---

## Verification Done (DO NOT RETRY)

1. ✅ Routes are registered — TestClient confirms 401 (not 404) for unauthenticated requests to all four endpoints
2. ✅ Image generation flow works end-to-end — `POST /generate/image` → 200, `GET /files/{id}?token=...` → 200 with valid 1024×1024 PNG
3. ✅ Video generation works locally — `LocalVideoProvider.generate()` produces valid 450 KB MP4 with correct magic bytes
4. ✅ PyAV encoders available — libx264, libvpx-vp9, mpeg4 all confirmed
5. ✅ File serving path resolution is consistent — `persist_bytes` and `files.py` both resolve to the same absolute path
6. ✅ Path traversal check in `files.py` is correct (only checks for `..` escape)
7. ✅ `serialize_asset` and `serialize_generation_job` produce correct `/api/creative/files/{asset_id}` URLs
8. ✅ Test suite: all 58 tests pass (39 image + 19 creative/generation/media)
9. ✅ `_FALLBACK_CODES` includes `PROVIDER_ERROR` and `PROVIDER_QUOTA` (both relevant codes)
10. ✅ `available_ram_gb()` reports ~2.08 GB; `select_model(2.08, "cpu")` returns `None`; `select_model(3.0, "cpu")` returns `sd15-lcm`; `image_local_model = "sdxl-lightning"` forces the 6.0 GB spec

---

## Research Notes (How Others Generate "Perfect" Images)

Based on prior research (Wikipedia, HuggingFace API, GitHub source):

1. **Stable Diffusion XL-Lightning** (ByteDance): 4-step distilled, CFG=0, 1024×1024 — requires ~6 GB RAM on CPU. Not feasible on 2 GB.
2. **SDXL-Turbo** (Stability): 1-step distilled — also ~6 GB, same constraint.
3. **LCM-LoRA** (Stability): Works with SD 1.5 base — requires ~3-4 GB RAM on CPU with float16.
4. **ONNX Runtime quantization**: Community converters (`optimum`, `onnxruntime`) can quantize SD 1.5 to int8, reducing RAM to ~1.2-1.5 GB. This is the path of least resistance for CPU-only 2 GB machines.
5. **Prompt engineering patterns** (from community best practices):
   - Use descriptive, multi-clause prompts: "subject, style, lighting, camera, quality"
   - Negative prompts for SD: "deformed, blurry, low quality, watermark, text, signature"
   - The existing `prompt_engine.py` already implements these patterns
6. **Quality gates** (from industry practice):
   - CLIP-based semantic similarity scoring
   - LaMa inpainting for face/hand fixes
   - The existing `image_quality.py` implements numpy/PIL-based quality metrics

The current architecture is well-designed. The primary blocker is the **2 GB RAM constraint** preventing any real diffusion model from loading.
