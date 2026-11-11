# Plan — Image Generation Improvement

> ## Status (as of 2026-10-12) — Planning Pass (no code)
> This document captures the audit, comparison, and implementation plan for fixing
> Jalora's image generation. **No code is written in this pass.** The plan is ready
> for a subsequent build pass.

---

## 1. Problem Statement

**Symptom:** Jalora cannot generate images that match the user's request. When a user
asks for "a futuristic cityscape at night", the system returns an abstract procedural
blob (gradient + random ellipses) instead of a recognisable scene.

**Root cause:** The offline fallback path — `LocalImageProvider` in
`app/services/creative/providers.py` — is **100% procedural** (Pillow + numpy). It
maps the prompt to one of 7 hardcoded keyword palettes (`sunset`, `ocean`, `forest`,
`neon`, `desert`, `snow`, `fire`), generates a random per-channel gradient, then
stamps 8–20 random coloured ellipses with a Gaussian blur. It has no concept of
objects, composition, or scene semantics. Any prompt that doesn't contain one of those
7 keywords falls through to a completely random colour palette.

**Trigger conditions** (silent — the user doesn't know):
1. No `JALORA_OPENAI_API_KEY` and no `JALORA_GOOGLE_API_KEY` are set → `resolve_provider`
   in `auto` mode falls through to `LocalImageProvider` (which is always `configured() → True`).
2. A cloud provider is configured but raises a `CreativeEngineError` with a fallback code
   (`PROVIDER_NOT_CONFIGURED`, `PROVIDER_QUOTA`, `PROVIDER_AUTH`, `PROVIDER_ERROR`,
   `PROVIDER_TIMEOUT`, `MODEL_UNAVAILABLE`) → `creative_tools.py:generate_image` and
   `generate.py:_sync_generate` both silently retry on the local procedural provider.
3. The `_provider_message` in `creative_tools.py:208` *does* honestly tell the user
   the local engine "generates abstract art rather than recognisable scenes" — but this
   is shown as a subtle note in the tool result, not surfaced as a blocking warning
   before the user submits a prompt.

This is confirmed by the test suite: `tests/test_creative.py:39–46` generates an image
with the prompt "Futuristic cityscape at night" and asserts only `file_type == "image"`
— it **never verifies the image semantically matches the prompt**. The procedural
fallback passes tests while still failing the user.

---

## 2. How Top AI Image Generators Work (Research Summary)

### 2.1 Stable Diffusion (the open-source leader)

**Source:** Wikipedia "Stable Diffusion" + Hugging Face diffusers source code (v0.41.0) +
model cards for `stabilityai/stable-diffusion-xl-base-1.0` (4.3M downloads),
`ByteDance/SDXL-Lightning` (129K downloads, arxiv:2402.13929),
`latent-consistency/lcm-lora-sdxl` (arxiv:2311.05556).

**Architecture (Stable Diffusion 1.x/2.x):**
- **Latent Diffusion Model (LDM)** — 3 components:
  1. **VAE** (Variational Autoencoder): encoder compresses the image from pixel space
     (512×512) to a smaller latent space (64×64 × 4 channels); decoder converts the
     denoised latent back to pixel space.
  2. **U-Net** (860M params): a ResNet backbone that iteratively denoises the latent
     representation, conditioned on text via cross-attention.
  3. **Text encoder**: CLIP ViT-L/14 (123M params) — converts the text prompt into a
     768-dim embedding that guides the U-Net via cross-attention.
- **Training data**: LAION-5B (600M aesthetic-filtered image-text pairs from Common Crawl).
- **Training compute**: 256× Nvidia A100 GPUs on AWS, 150,000 GPU-hours.
- **Key inference parameters** (from the SDXL pipeline `__call__` signature):
  - `prompt` / `prompt_2` (two text encoders in SDXL)
  - `height`, `width` (1024×1024 native for SDXL; 512×512 for SD 1.5)
  - `num_inference_steps` (default 50; 20–30 for SDXL, lower for distilled models)
  - `guidance_scale` / CFG (classifier-free guidance; default 5–7 for SDXL, 7–10 typical)
  - `negative_prompt` / `negative_prompt_2` — specifies what to avoid
  - `generator` (`torch.Generator`) — seed for reproducibility
  - `strength` (img2img only, 0.0–1.0) — noise added to the reference image
  - `mask_image` (inpainting only) — black/white mask defining where to regenerate

**SDXL (1.0, July 2023)** — key improvements:
- Native 1024×1024 resolution (vs 512×512 in SD 1.x).
- Two text encoders: CLIP ViT-L/14-224 + OpenCLIP ViT-H/xl (larger context, better prompt understanding).
- Trained on multiple aspect ratios and orientations (not just square).
- **Refiner model**: a second U-Net trained to add fine details via img2img denoising
  (denoising_start/denoising_end parameters). Applied after the base model produces
  a coarse latent.
- Recommended scheduler: Euler a or DPM++ 2M Karras.
- Weights: SDXL base fp16 ≈ 2.3 GB; with two text encoders + VAE ≈ 5 GB VRAM (GPU).

**SDXL-Lightning** (arxiv:2402.13929, ByteDance):
- Distilled from SDXL via *progressive adversarial diffusion distillation*.
- Generates 1024×1024 images in **1–8 steps** (vs 20–50 for standard SDXL).
- Key insight: because the model is distilled, `guidance_scale=0` (no CFG needed).
  The U-Net checkpoint + `EulerDiscreteScheduler` with `timestep_spacing="trailing"`.
- Checkpoints: `sdxl_lightning_Nstep_unet.safetensors` (full UNet replacement) or
  `sdxl_lightning_Nstep_lora.safetensors` (LoRA adapter, can be merged).
- 4-step variant recommended for best quality/speed balance.

**LCM-LoRA** (arxiv:2311.05556, Latent Consistency Models):
- LoRA adapter that converts any SD model into a 1–4 step generator.
- Works on SD 1.5 base (`lcm-lora-sdv1-5`) and SDXL base (`lcm-lora-sdxl`).
- Also uses `guidance_scale=0` and `num_inference_steps` matching the LoRA (e.g., 4).

**SD 3 / SD 3.5**:
- Switches from U-Net to a **Rectified Flow Transformer** (MMDiT) — not compatible
  with the diffusers `StableDiffusionPipeline` API without a different pipeline class.
- Out of scope for initial implementation (requires `text_to_image` SD3 pipeline).

**img2img** (Wikipedia + diffusers source):
- Takes a reference image, adds controlled noise (`strength` param, 0.0–1.0), then
  denoises with the text prompt. `strength=0` = identical to reference, `strength=1`
  = full regeneration from noise.
- The `StableDiffusionImg2ImgPipeline.__call__` signature confirms: `image`, `strength`,
  `prompt`, `negative_prompt`, `guidance_scale`, `num_inference_steps`, `generator`.

**Inpainting** (Wikipedia + diffusers source):
- Takes an image + a mask (white = region to regenerate, black = region to keep).
- Fills the masked area with content consistent with the prompt.
- SDXL inpainting pipeline (`StableDiffusionXLInpaintPipeline.__call__`) confirms:
  `image`, `mask_image`, `prompt`, `strength` (default 0.9999),
  `padding_mask_crop`, `denoising_start/end`, `guidance_scale`.
- SDXL inpainting model: `stabilityai/stable-diffusion-xl-base-1.0` + inpainting LoRA,
  or the dedicated `diffusers/stable-diffusion-xl-inpainting` checkpoint.

**Negative prompts** (Wikipedia):
- A separate text string specifying things to avoid (e.g., "deformed hands, blurry,
  text, signature"). Applied via classifier-free guidance by pairing the positive
  prompt with the negative prompt.

**CPU considerations:**
- SDXL base ≈ 2.3 GB (fp16) + two text encoders → needs ~6 GB RAM minimum.
  With 8 CPU cores and 15.8 GB RAM (3.5 GB available), SDXL-on-CPU at 30 steps
  takes ~20–40 minutes per image (far too slow for interactive use).
- SDXL-Lightning at 2–4 steps cuts this to ~1–2 minutes on CPU.
- SD 1.5 (4-stage, 512×512) runs in ~5–15 seconds on CPU but produces lower quality.
- **ONNX Runtime** can accelerate CPU inference (quantized models, 2–4× speedup).
- **`enable_model_cpu_offload()`** in diffusers moves components between CPU/GPU
  one at a time to minimize memory; `enable_attention_slicing()` reduces peak memory
  by processing attention heads in chunks.
- **`torch.set_num_threads(n)`** controls CPU thread count.

### 2.2 DALL·E (OpenAI's proprietary model)

**Source:** Wikipedia "DALL-E" + existing `OpenAIImageProvider` implementation.

- **DALL·E 1**: discrete VAE (image → token sequence) + 12B-param autoregressive
  transformer (GPT-3-style) + CLIP pair for filtering.
- **DALL·E 2/3/GPT Image**: 3.5B params, diffusion model conditioned on CLIP image
  embeddings. A **prior model** generates CLIP embeddings from text, then a
  **diffusion decoder** produces the image. Same LDM architecture family as SD.
- **DALL·E 3**: No public training details. Key feature: uses ChatGPT to
  **rewrite/expand** the user's prompt before generation ("prompt rewriting").
  Integrated into ChatGPT Plus. As of March 2025, replaced by GPT Image (native
  image generation in ChatGPT).
- **API**: `client.images.generate(model="gpt-image-1", prompt=..., size=...)` or
  `client.images.edit(image=..., mask=..., prompt=...)` for inpainting.
- **Limitations**: OpenAI silently drops `negative_prompt` and `seed` (confirmed in
  `OpenAIImageProvider.capabilities`: `negative_prompt: False, seed: False`).
- **Cost**: $0.04/image (as estimated in the current `estimate_cost`).

### 2.3 Midjourney

**Source:** Wikipedia "Midjourney (software)".

- **Proprietary, closed model** — no public weights or training details.
- Accessed exclusively via Discord bot (`/imagine` command) or web UI.
- Versions: V1→V6.1, with V4+ models trained on Google TPUs.
- V6 (released Dec 2023): trained from scratch over 9 months; improved text rendering,
  more literal prompt interpretation.
- Features: image weight (0–2.0, controls reference image influence), Style Reference
  (extract color palette/texture/atmosphere from a reference image), Character Reference
  (maintain character consistency), Vary (Region) for selective regeneration.
- Aspect ratios: 1:1, 2:3, 3:2, 4:5, 5:4, 7:4, 4:7, 9:16, 16:9, and custom.
- No API (Discord bot only) — would require bot automation, not directly callable.

### 2.4 Google Imagen

**Source:** Wikipedia "Imagen (text-to-image model)".

- Uses **T5 transformer LLM** for text encoding (not CLIP — T5 has better language
  understanding).
- **Cascaded diffusion**: generates at 64×64 → upsamples to 256×256 → upsamples to 1024×1024.
  Three stages, each a diffusion model. This produces higher-fidelity results than
  single-stage approaches.
- Imagen 4 (May 2025): generates up to 2K resolution.
- Available via: Gemini API (`gemini-2.5-flash-image`), ImageFX, Vertex AI.
- 5 aspect ratios: 9:16, 3:4, 1:1, 4:3, 16:9.
- **Current implementation** (`GoogleImageProvider`): uses `gemini-2.5-flash-image` via
  the Gemini API with `responseModalities=["IMAGE"]`. Supports image-to-image but
  NOT inpainting (no mask support). Negative prompt appended as text (`"Avoid: ..."`)
  rather than true classifier-free guidance. Seed not supported.

### 2.5 Other open-source alternatives

| Model | Size | Resolution | Steps (distilled) | CPU-friendly? | Notes |
|-------|------|-----------|-------------------|--------------|-------|
| SDXL-Lightning 4-step | ~2.3 GB | 1024×1024 | 4 | Very slow (~1–2 min/image) | Best quality/speed tradeoff for SDXL |
| SDXL-Lightning 2-step | ~2.3 GB | 1024×1024 | 2 | Faster (~40s) | Slightly lower quality |
| LCM-LoRA SDXL | ~2.3 GB + LoRA | 1024×1024 | 1–4 | Moderate | LoRA adapter on SDXL base |
| SD 1.5 | ~4 GB (all-in-one) | 512×512 | 20–30 | Yes (~5–15s) | Lower resolution, older |
| SD 1.5 + LCM-LoRA | ~4 GB + LoRA | 512×512 | 4 | Yes (~3–8s) | Fast CPU generation, lower quality |
| Kandinsky 2.2 | ~3 GB | 512×512 | 20–30 | Moderate | Uses CLIP + transformer fusion |
| Würstchen | ~3 GB | 512×512 | 20–30 | Moderate | Efficient VQ-GAN + transformer |

---

## 3. Audit: Current vs. Best Practices

### 3.1 Current Architecture Overview

```
User Prompt
    │
    ▼
ImageGenerationRequest (pydantic schema, _schemas.py:128)
    │  Fields: prompt, negative_prompt, aspect_ratio, model, provider,
    │  num_variations, seed, reference_asset_id, mask_asset_id
    │
    ▼
resolve_provider("image", "auto", settings)  ──► [OpenAI, Google, Local]
    │  (providers.py:594)                           auto = first configured
    │  auto: prefers cloud (OpenAI→Google), falls
    │      through to Local (always configured)
    │
    ▼
provider.generate(GenRequest)
    │  GenRequest fields: kind, prompt, negative_prompt, aspect_ratio,
    │  duration, seed, num_variations, reference_image, mask_image,
    │  voice, model, extra
    │
    ├── OpenAIImageProvider  ──► openai SDK (gpt-image-1 / DALL·E)
    ├── GoogleImageProvider  ──► Google Gemini API (gemini-2.5-flash-image)
    └── LocalImageProvider   ──► PIL + numpy (PROCEDURAL, not AI)
                                 ▪ 7-keyword palette match
                                 ▪ gradient + random ellipses
                                 ▪ 0.6 Gaussian blur
                                 ▪ reference_image → blend only
                                 ▪ NO inpainting (mask ignored)
                                 ▪ NO negative_prompt
                                 ▪ seed only in metadata (not used for generation)

    │
    ▼
persist_bytes() → MediaAsset → /api/artifacts/{user}/{file}.png
    meta: {ai: False, provider: "local-procedural", model: "jalora-procedural"}
```

**Two entry points** both use this layer:
- `_sync_generate` in `generate.py:67` (REST API: `POST /api/creative/generate/image`)
- `generate_image` in `creative_tools.py:59` (chat agent tool)

Both wrap the provider call with the same fallback logic: catch `CreativeEngineError`
with fallback codes → retry on `LocalImageProvider`.

### 3.2 What's Good (Strengths)

| Aspect | Current State | Assessment |
|--------|--------------|------------|
| Provider abstraction | `ImageProvider` base class with `.generate()`, `.configured()`, `.estimate_cost()`, `.capabilities` | ✅ Clean, extensible — new providers slot in without touching API/tools |
| Dual entry points | REST API + chat agent tool both delegate to `providers` layer | ✅ DRY — single source of truth for generation |
| Auto-fallback design | Cloud → local fallback on quota/auth errors | ✅ User always gets *some* output |
| Honest metadata | `ai=False` on local outputs, `_provider_message` explains the limitation | ✅ Transparent labelling |
| Schema support | `ImageGenerationRequest` already accepts `negative_prompt`, `seed`, `reference_asset_id`, `mask_asset_id`, `num_variations` | ✅ Future-proof — just needs a provider that uses them |
| Capability registry | `capability_registry()` returns secret-free provider capabilities to the frontend | ✅ Good for UI feature gating |
| Error classification | `_map_error` and `_google_code` map provider errors to `CreativeEngineError` codes | ✅ Clean error model |
| Existing editing engine | `image_engine.py` has full Pillow+numpy image manipulation (adjustments, curves, filters, transforms, compositing, inpainting masks, background removal) | ✅ Can be reused |
| Test isolation | `test_creative.py` and `test_phase_d_generation.py` cover the full flow | ✅ Good test coverage (but weak assertions) |

### 3.3 What's Wrong (Weaknesses) — Compared to Best Practices

| Issue | Current | Best Practice (from research) | Gap |
|-------|---------|------------------------------|-----|
| **Local fallback is procedural, not a model** | 7-keyword palette + random ellipses + blur | Real diffusion model synthesises object semantics from prompts | **Critical** — root cause of "can't generate requested images" |
| **Negative prompt ignored locally** | `negative_prompt` in capabilities = False | SDXL/SD1.5 pipelines natively support `negative_prompt` | Medium — user's "no text" request silently dropped |
| **Seed not used for generation** | Seed hashed for random palette only | `torch.Generator(seed)` controls noise init → reproducibility | Medium — seed in UI appears to do nothing |
| **No inpainting via mask** | `inpainting: False` in capabilities; mask ignored in `LocalImageProvider.generate` | SDXL inpainting pipeline uses `mask_image` + `prompt` to fill regions | Medium — reference + mask path returns to procedural |
| **img2img is just a blend** | `reference_image` → `Image.blend(ref, img, 0.45)` | True img2img adds controlled noise (`strength`) then denoises with prompt | Medium — "make my fox wearing a hat" just overlays abstract art on the photo |
| **No prompt engineering** | Prompt passed verbatim | DALL·E 3 uses ChatGPT for prompt rewriting; SD community uses detailed prompts (style, lighting, composition, quality keywords) | Medium — short prompts yield poor SD results |
| **Silent fallback** | Cloud error → local fallback without user warning before generation | Should warn user *before* generation that no AI provider is configured | UX — user submits prompt, gets abstract art, is confused |
| **No SDXL-Lightning / fast distillation** | Not present | SDXL-Lightning generates 1024×1024 in 2–4 steps with CFG=0 — practical for local | Opportunity — makes CPU generation viable |
| **No model download caching** | N/A | HF `from_pretrained` downloads to `~/.cache/huggingface` — needs one-time download | Operational — first run downloads 2.3 GB |
| **Test assertions are weak** | `test_creative.py:46` only checks `file_type == "image"` | Should verify provider metadata (`ai=True`, `model` is a real diffusion model) | QA — tests pass on procedural output |
| **No CPU offload / memory optimization** | N/A | diffusers `enable_model_cpu_offload()`, `enable_attention_slicing()`, `torch.set_num_threads()` | Operational — needed for CPU inference |
| **Missing torch dependency** | Not in `requirements.txt` | `torch>=2.6` + `diffusers` + `transformers` + `safetensors` + `huggingface_hub` needed | **Critical** — the entire local AI path requires these |
| **No HF token config** | `JALORA_OPENAI_API_KEY` and `JALORA_GOOGLE_API_KEY` exist, but no `JALORA_HF_TOKEN` | Needed for gated models (SDXL-Lightning may require auth) | Config — new env var needed |
| **Capabilities not surfaced to UI** | `capability_registry()` exists but frontend doesn't use it | Frontend should disable/grey out features the selected provider doesn't support | UX — user tries inpainting but it silently doesn't work |
| **No generation progress** | Synchronous (blocks until done) | Long-running local generation should be async (like video jobs) with progress | UX — CPU generation at 30 steps takes minutes |

### 3.3 Environment Constraints

| Constraint | Value | Impact |
|-----------|-------|--------|
| Platform | Windows 11, Intel CPU (no NVIDIA GPU) | Must use CPU inference — no CUDA acceleration |
| RAM | 15.8 GB total, 3.5 GB available | SDXL (6 GB) is heavy; SD 1.5 (4 GB) is more feasible |
| CPU cores | 8 logical | `torch.set_num_threads(4–6)` to leave headroom |
| Disk | 74.8 GB free | SDXL fp16 ≈ 2.3 GB download + cache — fits |
| torch | NOT installed | Must add to `requirements.txt` (CPU-only wheel, ~200 MB) |
| diffusers | NOT installed | Must add |
| transformers | NOT installed | Must add (CLIP text encoders for SD) |
| PIL | 12.3.0 installed | ✅ Already available |

---

## 4. Recommendation

### 4.1 Chosen Approach: Tiered Local Diffusion Provider

Replace the `LocalImageProvider`'s procedural generation with a **real diffusion model**
using Hugging Face `diffusers`, with a **tiered fallback chain** that adapts to
hardware capabilities:

```
Image generation resolution:
  auto mode → [OpenAI DALL·E] → [Google Imagen] → [Local SDXL-Lightning] → [Local SD 1.5 + LCM-LoRA] → [procedural (last resort)]
                                                   ↑ GPU available? full SDXL
                                                   ↑ No GPU, 12GB+ RAM? SDXL-Lightning 4-step
                                                   ↑ No GPU, 8GB+ RAM? SD 1.5 + LCM-LoRA 4-step  
                                                   ↑ Very constrained? keep procedural (abstract)
```

**Why this approach:**
1. **Closes the root-cause gap**: A real diffusion model synthesises recognisable
   scenes from text prompts — exactly what the user needs.
2. **Plugs into the existing abstraction**: `LocalImageProvider` already implements
   `ImageProvider`, has `capabilities` dict, and is returned by `resolve_provider`.
   Swapping the `.generate()` body is a contained change.
3. **Graceful degradation**: If torch/diffusers aren't installed or the model can't
   load, fall back to the existing procedural engine (no regression).
4. **CPU-viable**: SDXL-Lightning at 2–4 steps with CFG=0 produces acceptable
   1024×1024 images in 1–2 minutes on CPU. SD 1.5 + LCM-LoRA at 4 steps is faster
   (~5–15 seconds) if quality is acceptable.
5. **Best practices covered**: Negative prompts, seeds, img2img (strength), inpainting
   (mask), prompt engineering — all natively supported by the diffusers pipelines.

### 4.2 Model Selection

| Tier | Model | Steps | CFG | Resolution | Est. CPU time (8-core) | RAM needed | Pros/Cons |
|------|-------|-------|-----|-----------|----------------------|-----------|-----------|
| **T1** | `stabilityai/stable-diffusion-xl-base-1.0` | 2–4 | 0 (Lightning) | 1024×1024 | 1–2 min | 6 GB | Best quality; SDXL-Lightning distilled |
| **T1** | `ByteDance/SDXL-Lightning` (4-step) | 4 | 0 | 1024×1024 | ~1 min | 6 GB | Same as above, explicit Lightning |
| **T2** | `runwayml/stable-diffusion-v1-5` + `latent-consistency/lcm-lora-sdv1-5` | 4 | 0 | 512×512 | 5–15s | 4 GB | Fast, lower res; good fallback |
| **T3** | Existing procedural | 0 | N/A | 1280×720 | <1s | <500 MB | Abstract only, last resort |

**Decision:** Default to **SDXL-Lightning 4-step** (T1) if the system has ≥6 GB
free RAM; fall back to **SD 1.5 + LCM-LoRA 4-step** (T2) if RAM is constrained
(4–6 GB); keep procedural (T3) as absolute last resort.

**Why SDXL-Lightning 4-step:**
- From the README: `guidance_scale=0`, `num_inference_steps=4`,
  `EulerDiscreteScheduler` with `timestep_spacing="trailing"`.
- 1024×1024 native resolution matches SDXL's training.
- 4-step quality is described as "amazing" in the README.
- `sdxl_lightning_4step_unet.safetensors` is a full UNet replacement (better
  quality than LoRA) — just 137 MB download (the UNet checkpoint).
- The base model (`sd_xl_base_1.0.safetensors`, ~2.3 GB) needs to be downloaded once.

**Alternative considered: Kandinsky 2.2** — Smaller and supports text-to-image +
inpainting, but lower quality than SDXL. Not recommended as the primary.

**Alternative considered: GGUF quantized models** — Very fast on CPU (~2–4× speedup)
via `llama.cpp`-style backends. Not yet mature in the diffusers ecosystem for SDXL.
Mention in the plan as a future optimization but not a primary option.

### 4.3 API Mapping (How the diffusers pipelines map to the existing `GenRequest`)

The existing `GenRequest` dataclass already has all the fields needed. Here's how
they map to the diffusers API:

| `GenRequest` field | SDXL pipeline param | Notes |
|---|---|---|
| `prompt` | `prompt` / `prompt_2` | `prompt_2` = prompt (same for both encoders unless specified) |
| `negative_prompt` | `negative_prompt` / `negative_prompt_2` | **New**: currently dropped, now supported |
| `aspect_ratio` | `height` / `width` | Resolve ratio → pixel dimensions (1024×1024, 1152×896, 896×1152, etc.) |
| `seed` | `generator` (torch.Generator(seed)) | **New**: currently unused, now controls noise init |
| `num_variations` | call pipeline N times (loop) | `num_images_per_prompt` not ideal — SDXL-Lightning has fixed denoising, loop for variations |
| `reference_image` | `image` (img2img) + `strength` | **Major**: replace `Image.blend` with true SDEdit/denoising |
| `mask_image` | `mask_image` (inpainting) | **New**: enable `StableDiffusionXLInpaintPipeline` |

---

## 5. Implementation Plan

### Phase 1: Foundation (No functional change yet — setup + config)

#### Step 1.1 — Add dependencies to `requirements.txt`
- `torch>=2.6` (install CPU-only variant: `pip install torch --index-url https://download.pytorch.org/whl/cpu`)
- `diffusers>=0.41.0`
- `transformers>=4.44.0`
- `safetensors>=0.4.5`
- `accelerate>=0.31.0` (optional, for memory optimization)
- `huggingface_hub>=0.25.0`
- Add a `# Creative Studio: local AI image generation (lazy import)` comment block,
  consistent with the existing "Optional domain backends (imported lazily if present)"
  pattern for `faster-whisper` / `piper-tts`.

**Rationale:** torch is a heavy dependency (~200 MB CPU wheel). Must be installed
explicitly with the CPU index URL. Follow the existing lazy-import pattern so the
app still runs without these packages.

#### Step 1.2 — Add config settings to `config.py`
Add under the Creative Studio section:
```python
# Local AI image generation (diffusers / Stable Diffusion).
# Lazy-imported: the app runs without these packages, falling back to
# the procedural engine. When installed, the local provider attempts to
# download/cache the configured model on first use.
image_local_model: str = "sdxl-lightning"  # sdxl-lightning | sd15-lcm | sd15-default
image_local_steps: int = 4  # inference steps (Lightning/LCM models use 1-8)
image_local_guidance_scale: float = 0.0  # 0.0 = no CFG (distilled models)
image_local_strength: float = 0.75  # img2img denoising strength (0=keep ref, 1=full regen)
huggingface_token: str | None = None  # for gated models (env: JALORA_HF_TOKEN)
image_local_device: str = "auto"  # auto | cpu | cuda | mps
image_local_max_ram_gb: float = 4.0  # minimum free RAM to attempt SDXL
```

Add `huggingface_token` to the `_empty_str_is_none` validator list.

**Rationale:** Gives operators per-model and per-performance knobs without code changes.

#### Step 1.3 — Create model registry module
New file: `app/services/creative/model_registry.py`
- `MODEL_SPECS: dict` — maps names → `{pipeline_class, model_id, torch_dtype, steps, guidance_scale, width, height, ram_gb_required, description}`.
- E.g.:
  ```python
  "sdxl-lightning-4step": {
      "class": "StableDiffusionXLPipeline",
      "model_id": "stabilityai/stable-diffusion-xl-base-1.0",
      "unet_overrides": "ByteDance/SDXL-Lightning::sdxl_lightning_4step_unet.safetensors",
      "scheduler": "EulerDiscrete",
      "timestep_spacing": "trailing",
      "steps": 4, "guidance_scale": 0.0, "width": 1024, "height": 1024,
      "dtype": "float16", "ram_gb": 6.0,
  }
  ```
- `select_model(ram_available: float, device: str) -> str` — picks the best tier
  based on available RAM and device.
- `load_pipeline(model_name: str, device: str) -> DiffusionPipeline` — cached
  singleton loader with `lru_cache` or module-level `_CACHE` dict.

**Rationale:** Centralises model config and makes adding new models trivial.

### Phase 2: Replace LocalImageProvider.generate() (Core change)

#### Step 2.1 — Rewrite `LocalImageProvider.generate()`
- Rename class internally: `LocalImageProvider` → keep name for `resolve_provider`
  compatibility, but add a sub-type: `LocalImageProvider` now delegates to either
  the **diffusion model** (if torch/diffusers installed + model loaded) or the
  **procedural engine** (fallback, as currently implemented).
- Move the existing procedural logic into a private method:
  `_procedural_generate(req) -> list[GenOutput]` (what `generate()` does today).
- New `generate()`:
  ```python
  def generate(self, req):
      # Try the AI diffusion path first
      outputs = self._ai_generate(req)
      if outputs is not None:
          return outputs
      # Fallback to procedural (existing behavior)
      return self._procedural_generate(req)
  ```
- `_ai_generate(req)`:
  1. Lazy-import torch + diffusers (return None on ImportError).
  2. Select model via `model_registry.select_model()` based on RAM.
  3. Resolve aspect ratio → width/height (SDXL supports 1024×1024, 1152×896, 896×1152, etc.).
  4. Create `GenRequest` → seed handling: if `req.seed` is None, generate a random
     seed and record it in metadata.
  5. Load pipeline (cached).
  6. Determine the mode:
     - **img2img**: if `req.reference_image` is set (but no mask) → use
       `StableDiffusionImg2ImgPipeline` with `strength=req.extra.get("strength", 0.75)`.
     - **inpainting**: if `req.mask_image` is set → use
       `StableDiffusionXLInpaintPipeline` with `image` + `mask_image` + `prompt`.
     - **txt2img**: plain text → `StableDiffusionXLPipeline`.
  7. Call `pipe(...)` with: `prompt`, `negative_prompt`, `num_inference_steps`
     (from config or model spec), `guidance_scale` (config), `generator=torch.Generator(seed)`,
     `height`, `width`, `output_type="pil"`.
  8. For SDXL-Lightning: also set the scheduler to `EulerDiscreteScheduler` with
     `timestep_spacing="trailing"` and `guidance_scale=0`.
  9. Convert output PIL images to `GenOutput` bytes (PNG), with `meta = {"ai": True,
     "provider": "local-ai", "model": model_name, "seed": seed}`.
  10. Handle `num_variations` by calling the pipeline N times with different seeds.
  11. Memory optimization: `pipe.enable_model_cpu_offload()` if no GPU;
     `pipe.enable_attention_slicing()` to reduce peak memory;
     `torch.set_num_threads(min(4, cpu_count - 2))`.

**Rationale:** This is the single most important change. It transforms the "local
fallback" from abstract art to real AI image generation.

#### Step 2.2 — Update `LocalImageProvider.capabilities`
Change from `procedural: True` to:
```python
capabilities = {
    "text_to_image": True, "image_to_image": True, "inpainting": True,
    "variations": True, "transparent_bg": False, "negative_prompt": True,
    "seed": True, "max_variations": 4, "procedural": False, "ai": True,
}
```
Note: `ai` should also be updated (currently `ai = False` on the class). When the
AI path is active, return `ai=True` in metadata; when falling back to procedural,
return `ai=False`.

**Rationale:** The capabilities dict must accurately reflect what the provider
actually supports now.

#### Step 2.3 — Update `resolve_provider()`
Add a new provider name `"local-ai"` that resolves to the AI-capable local provider.
In `auto` mode, after cloud providers fail, try `local-ai` before falling back to
procedural. This allows users to explicitly request `provider: "local-ai"`.

Add `"local-ai"` to the `_image_providers()` list (before `LocalImageProvider`).

### Phase 3: Prompt Engineering & Quality

#### Step 3.1 — Add a prompt enhancer service
New file: `app/services/creative/prompt_engine.py`
- `enhance_prompt(prompt: str, model_type: str) -> str` — expands short/clip-style
  prompts into detailed descriptions. Based on SDXL community best practices:
  - Append quality keywords: "highly detailed, 8k, sharp focus, professional
    photography" for photorealistic prompts.
  - Append style keywords based on detected keywords: "cinematic lighting",
    "ultra-fine detail", "masterpiece, best quality".
  - Keep original prompt intact, append enhancement.
- `build_prompt(prompt, negative_prompt, aspect_ratio) -> (str, str)` — combines
  the prompt with aspect-ratio guidance (similar to Google's current approach but
  integrated into the pipeline call, not appended as text).

**Rationale:** SDXL significantly benefits from detailed prompts. DALL·E 3 proves this
by using ChatGPT for prompt rewriting. Without enhancement, short prompts like "a cat"
produce weak results.

#### Step 3.2 — Default negative prompt
Add a sensible default negative prompt if the user doesn't provide one:
```
"deformed, disfigured, bad anatomy, extra limbs, missing limbs, text, signature,
watermark, blurry, low quality, worst quality, normal quality, jpeg artifacts,
cloned face, disfigured, malformed hands"
```
This matches the common SDXL community negative prompt.

### Phase 4: img2img & Inpainting

#### Step 4.1 — Wire up reference image as img2img
In `_sync_generate()` (generate.py) and `generate_image()` (creative_tools.py),
the `reference_image` and `mask_image` are already resolved from asset IDs and
passed into `GenRequest`. The new `LocalImageProvider` should:
- If `reference_image` is set and `mask_image` is NOT set → use `StableDiffusionImg2ImgPipeline`
  with `image=reference_image`, `strength=req.extra.get("strength", 0.75)`.
- If `mask_image` is set → use `StableDiffusionXLInpaintPipeline` with
  `image=reference_image`, `mask_image=mask_image`, `prompt=req.prompt`.

#### Step 4.2 — Update `_sync_generate` and `creative_tools.generate_image`
Pass `strength` into `GenRequest.extra` from the API request (add optional `strength`
field to `ImageGenerationRequest`).

### Phase 5: Async Generation & Progress

#### Step 5.1 — Make local AI generation async
Local CPU inference at 4 steps takes 30–90 seconds (depending on model tier). The
current `generate_image` and `_sync_generate` are synchronous. For the REST API,
image generation is currently sync (video is async via `jobs.py`). Options:
- **Option A**: Keep image sync but increase `tool_timeout_seconds` and warn the user.
- **Option B**: Make local AI image generation async (submit to the same background
  job system used by video), return a job ID, poll for completion.
- **Recommendation**: Option B — reuse the existing `jobs.py` async pattern. Add
  `image_provider_kind` to the job submission, and when provider is `"local"`,
  dispatch to a background thread. The REST endpoint stays sync for cloud providers
  (fast) but for local AI, it should return a job handle or run synchronously with
  a longer timeout.

**Detailed decision**: Since `image_provider: str = "auto"` may resolve to local,
and local AI is slow, the safest approach is:
- If provider is cloud (OpenAI/Google): keep sync (fast, <5s).
- If provider is local AI: make it async via the `jobs.submit_generation()` path
  (same as video). Return `job_id` + poll endpoint.

This requires changes to `generate.py` (the image endpoint) and the frontend
(`CreativeStudioView.tsx` already has a render-loading pattern that could be reused).

### Phase 6: Frontend Changes

#### Step 6.1 — Surface provider/capabilities to the UI
- Call the existing `/capabilities` endpoint (already implemented in `generate.py:37`)
  to get provider capabilities.
- Display the active provider and whether it's AI or procedural.
- Disable/grey out inpainting, negative prompt, seed fields when the selected
  provider doesn't support them (use the `capabilities` dict).

#### Step 6.2 — Warn before procedural fallback
In `CreativeStudioView.tsx`'s `handleGenerateImage`, before calling the API:
- If the resolved provider will be `local-procedural` (no cloud key + no AI path),
  show a warning: "No AI image provider is configured. I'll generate abstract
  procedural art instead of a recognisable image. Configure an OpenAI or Google API
  key, or install local AI dependencies (diffusers + torch) for real AI images."

#### Step 6.3 — Add img2img / inpainting UI controls
- `reference_asset_id`: select an existing image to use as img2img source.
- `mask_asset_id`: draw/select a mask region for inpainting.
- `strength` slider (0.0–1.0) for img2img.
- `negative_prompt` text field.
- `seed` field (number or "random").

#### Step 6.4 — Add generation progress indicator
For local AI generation (async), show a progress spinner/status similar to the
video generation flow.

### Phase 7: Testing Strategy

#### Step 7.1 — Update existing tests
`tests/test_creative.py:39–46`:
- Change the image generation assertion to also check the provider metadata:
  ```python
  assert gen_data["provider"] in ("openai", "google", "local-ai")
  # When local-ai is active, verify it's actually an AI model, not procedural
  if gen_data["provider"] == "local-ai":
      assert gen_data["ai"] is True
  ```
- If no cloud key and no torch installed, the test should accept the procedural
  fallback gracefully (test environment may not have torch).

#### Step 7.2 — Add new tests
`tests/test_phase_d_image_generation.py`:
- Test `model_registry.select_model()` tiers based on RAM.
- Test `prompt_engine.enhance_prompt()` adds quality keywords.
- Test `LocalImageProvider.capabilities` includes `negative_prompt: True`.
- Test img2img path when `reference_image` is set and torch is available.
- Test inpainting path when `mask_image` is set and torch is available.
- Test fallback to procedural when torch/diffusers not installed.
- Test seed reproducibility (same seed → same image).

**Test environment note:** Since torch may not be installed in CI, use
`pytest.importorskip("torch")` / `pytest.importorskip("diffusers")` for AI-specific
tests, and always test the procedural fallback path.

#### Step 7.3 — Mock-first test pattern
Follow the existing pattern from `test_phase_d_media.py`: when no provider is
configured, tests should not fail — they should verify graceful degradation
(like the existing `test_transcribe_audio_offline_mock`).

### Phase 8: Operational / Deployment

#### Step 8.1 — Model download on first run
- Models are downloaded to `~/.cache/huggingface` (HF default) on first use.
- SDXL base (2.3 GB) + Lightning UNet (137 MB) ≈ 2.5 GB one-time download.
- Add a startup health check: `app startup → if image_local_model == "sdxl-lightning",
  attempt to import torch; if ImportError, log a warning + add to startup warnings`.
- Consider a CLI command `python -m jalora.cache_model sdxl-lightning` to pre-download.

#### Step 8.2 — Update `.env.example`
Add:
```
JALORA_HF_TOKEN=
JALORA_IMAGE_LOCAL_MODEL=sdxl-lightning
JALORA_IMAGE_LOCAL_STEPS=4
```

#### Step 8.3 — Update `config.py validate_settings()`
Add a startup warning when `image_provider == "auto"` and no cloud key is configured:
```python
if settings.image_provider == "auto" and not settings.openai_api_key and not settings.google_api_key:
    warnings.append(
        "No AI image provider configured. Install torch+diffusers and set "
        "JALORA_IMAGE_LOCAL_MODEL for local generation, or configure an "
        "OpenAI/Google API key."
    )
```

---

## 6. Execution Order (Implementation Sequence)

```
1. requirements.txt      → add torch, diffusers, transformers, safetensors
2. config.py             → add image_local_* settings, HF token
3. model_registry.py     → NEW: model specs + selection + loader
4. prompt_engine.py      → NEW: prompt enhancement + negative prompt defaults
5. providers.py          → rewrite LocalImageProvider.generate() + capabilities
6. _schemas.py           → add `strength` field to ImageGenerationRequest
7. generate.py           → pass strength to GenRequest.extra
8. creative_tools.py     → pass strength, update _provider_message
9. frontend              → surface capabilities, warnings, img2img/inpainting UI
10. tests               → update + add tests (mock-first, importorskip for AI)
11. .env.example          → add HF token + local model settings
12. docs                  → update plan doc to "implemented" status
```

**Phases that can be done in parallel:**
- Steps 1–2 (config) + Step 4 (prompt_engine) + Step 3 (model_registry) — independent.
- Step 6 (_schemas.py) + Step 7 (generate.py) + Step 8 (creative_tools.py) — depend on
  the provider interface but can be done once the GenRequest/GenOutput contract is finalised.

**Dependency chain:**
- `diffusers` pipeline `__call__` signatures were verified from source — the API
  contract is stable. `from_pretrained`, `.to(device)`, `pipe(prompt=...)`,
  `pipe(img2img(...))`, `pipe(inpaint(...))` are all standard.
- The `ImageProvider` interface is unchanged — only the `generate()` body,
  `capabilities` dict, and `ai` attribute change.

---

## 7. Risk Assessment & Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| torch installation fails / too large | Medium | High | CPU-only wheel (~200 MB); lazy import means app runs without it; document the CPU install command |
| SDXL-Lightning produces poor quality on CPU | Medium | Medium | Default to 4-step; allow `JALORA_IMAGE_LOCAL_STEPS` override; fall back to SD 1.5 + LCM-LoRA if quality unacceptable |
| First-run model download (2.3 GB) takes long | Medium | Medium | Pre-download via CLI command; cache to disk; show progress to user |
| CPU generation takes too long (>2 min) for UX | High | Medium | Make generation async with progress polling (Phase 5); show spinner |
| Memory pressure (6 GB needed for SDXL) | Medium | High | RAM-based model tier selection (T1/T2/T3); `enable_attention_slicing`; `enable_model_cpu_offload` |
| SDXL-Lightning requires HF auth (gated?) | Low | Medium | `ByteDance/SDXL-Lightning` is NOT gated (confirmed via API); no HF token needed for base or Lightning. Token only needed for SDXL inpainting or other gated models |
| Tests fail in CI without torch | High | Medium | Use `pytest.importorskip("torch")` for AI-specific tests; always test procedural fallback |
| Prompt quality still poor with simple prompts | Medium | Medium | Prompt enhancement service (Phase 3) + default negative prompt |
| Breaking existing cloud provider path | Low | High | Keep OpenAI/Google providers unchanged; only change LocalImageProvider |
| Inpainting pipeline not available publicly | Medium | Medium | SDXL inpainting is available as `diffusers/stable-diffusion-xl-inpainting` (gated, requires HF token) or via the base model + inpainting LoRA. If unavailable, fall back to img2img with a full-image mask (less ideal but functional) |
| Windows-specific torch path issues | Low | Low | `torch` supports Windows x86_64 natively; CPU-only wheel works |

---

## 8. Open Questions (for the user before coding begins)

1. **Which model tier to default to?**
   - SDXL-Lightning 4-step (best quality, 1024×1024, ~1 min CPU) — **recommended**
   - SD 1.5 + LCM-LoRA (faster, 512×512, ~5–10s CPU) — if speed is priority over quality
   - Recommendation: Start with SDXL-Lightning 4-step; add SD 1.5 + LCM-LoRA as a
     RAM-constrained fallback tier.

2. **Should local AI generation be synchronous or async?**
   - Current image endpoint is sync. Local CPU generation at 4 steps takes ~1 minute.
   - Recommendation: **Async** (reuse `jobs.py` pattern from video generation) for
     local AI, sync for cloud providers.

3. **What's the default negative prompt?**
   - The SDXL community standard negative prompt is recommended. Should we use a
     fixed default or let the user always provide one?
   - Recommendation: Always apply a sensible default if the user doesn't provide one,
     but let the user override via `negative_prompt`.

4. **Should we add a "model zoo" config that lets the user pick from multiple local
   models at runtime?** Or keep it as a single server-level config?
   - Recommendation: Server-level config (`JALORA_IMAGE_LOCAL_MODEL`) with the
     existing `provider: "local"` override in the API. Don't over-engineer the UI
     model picker for now.

5. **GPU detection**: Should we auto-detect CUDA at startup and enable GPU acceleration
   when available (future-proofing for a deployment with GPU)?
   - Recommendation: Yes — `image_local_device: str = "auto"` already covers this;
     the loader checks `torch.cuda.is_available()` and falls back to CPU.

---

## 9. What NOT to Change (Out of Scope)

- **OpenAI/Google providers**: Keep as-is. They work correctly when keys are configured.
- **Video providers**: Out of scope (video generation is a separate concern; the
  local video provider uses Ken Burns motion, which is fine).
- **Audio providers**: Out of scope.
- **image_engine.py**: The editing engine is already solid (Pillow + numpy with
  adjustments, filters, compositing, masks, background removal). No changes needed.
- **Frontend tab structure**: Keep the existing "AI Image Generator" tab. Just add
  controls to it.
- **Database schema**: `GenerationJob` and `MediaAsset` models already support
  `provider`, `model`, `meta` fields. No schema changes needed.

---

## 10. Summary of Changes Checklist

- [ ] **Step 1.1**: `requirements.txt` — add `torch`, `diffusers`, `transformers`,
      `safetensors`, `accelerate`, `huggingface_hub`
- [ ] **Step 1.2**: `config.py` — add `image_local_model`, `image_local_steps`,
      `image_local_guidance_scale`, `image_local_strength`, `huggingface_token`,
      `image_local_device`, `image_local_max_ram_gb`; update validation validators
- [ ] **Step 1.3**: `app/services/creative/model_registry.py` — NEW file
- [ ] **Step 2.1**: `providers.py` — rewrite `LocalImageProvider.generate()` with
      diffusers AI path + procedural fallback; add img2img + inpainting pipelines
- [ ] **Step 2.2**: `providers.py` — update `LocalImageProvider.capabilities` + `ai`
- [ ] **Step 2.3**: `providers.py` — add `local-ai` to `_image_providers()`, update
      `resolve_provider()` if needed
- [ ] **Step 3.1**: `app/services/creative/prompt_engine.py` — NEW file
- [ ] **Step 3.2**: `providers.py` — add default negative prompt in `LocalImageProvider`
- [ ] **Step 4.1**: `providers.py` — wire img2img + inpainting in `_ai_generate()`
- [ ] **Step 4.2**: `_schemas.py` + `generate.py` + `creative_tools.py` — pass `strength`
- [ ] **Step 5.1**: `generate.py` — make local AI image generation async (optional)
- [ ] **Step 6.1–6.4**: `frontend-react/src/views/CreativeStudioView.tsx` — capabilities,
      warnings, img2img/inpainting UI, progress
- [ ] **Step 7.1–7.3**: `tests/` — update `test_creative.py`, add
      `test_phase_d_image_generation.py`
- [ ] **Step 8.1–8.3**: `.env.example`, `config.py` validation, CLI pre-download
