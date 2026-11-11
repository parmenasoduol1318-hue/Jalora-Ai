# Plan — Accurate Math Engine (SymPy) + UI Visibility Fixes (sidebar / schedules / workspace)

> ## Status (as of 2026-10-12)
> **Implemented & green.** Math (Parts B1–B6) fully built and verified live + by
> `tests/test_math.py` (52 passed). `requirements.txt` now pins `sympy==1.14.0`;
> `tools/math_tools.py` registers `calculate`/`verify_math` (AUTO, math); intent +
> routing bind `math`; `chat_service.execute_chat` fast-paths before any model call.
> Backend A1 rate-limit GET-read whitelist landed in `main.py` (live-verified:
> `GET /api/schedules → 200`, never `429`). The frontend **SchedulesView** now has a
> friendly error + Retry with 429 exponential backoff (A3), and `styles.css` carries
> `.math-answer`/`.blockquote` (B6). `dist/` was rebuilt and is served no-cache
> (Phase J). Tests: `test_rate_limit_phase_l.py` adds a whitelist regression;
> `test_react_frontend.py` adds a stale-build guard (no-cache header + `.math-answer`
> + MathJax shipped in `dist`). `tsc -b --noEmit` → exit 0.
> Remaining (planned, not built this pass): App-wide 429 toast/back-off on writes
> (writes already toast via `onToast`; reads are whitelist-safe), and a full
> `test_phase_g_schedules.py` happy-path. See "Next steps" below.

## Overview

Two deliverables in one plan. **(1) Math:** give Jalora a real computer-algebra
back-end (SymPy, already installed in the venv at 1.14.0) so math questions are
*computed and verified*, not hallucinated by Ollama. MathJax LaTeX rendering is
**already** wired in (`index.html` CDN + `ChatView` `MathJax.typesetPromise` effect),
so we reuse it — the gap is purely the compute engine + routing + fast-path, per the
12-item spec. **(2) UI Audit:** the recent-conversations sidebar, Schedules, and
Workspace features **already exist in source and `dist/`** (verified on disk); the
user's symptoms come from runtime/visibility gaps, not missing code. The plan fixes
the root causes (rate-limiter 429s blanking list endpoints, responsive sidebar hidden
on narrow viewports, and terse empty/error states) and adds regression tests. No
auth/voice/memory changes.

Facts gathered during exploration:
- `App.tsx:165` always renders `<aside className="sidebar">` with a `.conv-empty`
  fallback (196); `SchedulesView.tsx:89` is unwrapped (fragment);
  `WorkspaceView.tsx:150` has the requirements card, `:256` the Upload tab;
  `api/workspace.py:65` has `POST /api/workspace/upload`; all routers registered in
  `api/__init__.py`. `dist` bundle contains `/api/schedules`, `/api/workspace/upload`,
  `ws-requirements-card`, etc. — stale build is ruled out.
- `t()` = `bundles[locale][key] || en[key] || key` → **never throws**, so missing i18n
  keys cannot blank a view (i18n crash theory ruled out).
- `config.py:202 rate_limit_global_per_min: int = 120`; `rate_limiter.py` is an
  in-memory sliding window keyed `global:{ip}` (per-user once auth resolves in
  `main.py`); returns `429` on `/api/*` when the budget is exhausted → frontend catch
  blocks render empty states (sidebar `recentLoadError` inline, Schedules bare `…` at
  SchedulesView:119, workspace `workspaceEmpty`). This is the "sometimes not
  visible/accesssible." `test_rate_limit_phase_l.py` already covers the limiter.
- `styles.css @media (max-width: 820px)` hides `.sidebar` off-canvas
  (`transform: translateX(-100%)`); only the ☰ `.nav-toggle` reveals it.
- Agent flow: `chat_service.execute_chat` (332) → `classify_intent` (376) →
  `tool_names_for_intent` (475) → `build_graph` → model calls tool →
  `spec.tool.ainvoke` (graph.py:434) → `ToolMessage` → reply streamed via
  `emit("token")` / `emit("done", ChatOutcome.as_response())` (1149). IMPLEMENTED —
  SymPy 1.14.0 is pinned in `requirements.txt`; a `math` intent + `calculate`/`verify_math`
  tools are registered (AUTO, category "math") and bound in routing, and the fast-path
  short-circuits `execute_chat` before any Ollama call (see §Status).

## Implementation approach

### Part A — UI visibility fixes (sidebar / schedules / workspace)

**Step A1 — Stop 429s from blanking read-only list endpoints.**
Whitelist `GET` list endpoints so a busy single-IP dev/NAT can't drop the
sidebar/schedule/workspace views, while still rate-limiting writes + auth. In
`app/main.py` middleware, before `is_allowed(...)`:
```python
_READ_LIST = {"/api/conversations", "/api/schedules", "/api/workspace/tree", "/api/tasks"}
if request.method == "GET" and str(request.url.path) in _READ_LIST:
    return await call_next(request)
```
Also surface 429 as a toast in `App.tsx`'s `loadConversations` catch and back off
retries so the inline `recentLoadError` isn't a dead end.

**Step A2 — Sidebar always accessible on narrow viewports.**
The off-canvas pattern is fine on ≤820px; A1 restores the conversation list, so no
structural change is required. Keep `nav-toggle` `aria-label={t("menu")}` (already
present) and the `.sidebar.open` toggle (styles.css:484).

**Step A3 — Replace the bare `…` Schedules empty state with a friendly one + Retry.**
In `SchedulesView.tsx`, track `loadError: string | null` and a `retrying` flag. On a
429, back off exponentially (5s × attempt, capped 30s, max 4 auto-retries); other
errors show the server message + a manual Retry button (new `.schedule-error` CSS):
```tsx
{!rows && !loadError && <div className="activity-empty">…</div>}
{loadError && !rows && (
  <div className="card schedule-error">
    <div className="schedule-error-msg">{loadError}</div>
    <button className="btn small" disabled={retrying} onClick={load}>
      {retrying ? t("retrying") : t("retry")}
    </button>
  </div>
)}
```

**Step A4 — Verify freshness (regression guard).**
`test_react_frontend.py`: assert the served `dist/index.html` still references the
workspace/schedule bundles so a stale rebuild is caught in CI.

### Part B — Math engine (SymPy), satisfying items 1–12

**Step B1 — `app/services/math_engine.py` (pure compute, no Ollama).**
- `classify_math(text) -> MathSignal` — regex for operators, `x^2`-terms, calculus
  verbs (`integrate|differentiate|derivative|integral|limit`), `factor|simplify|expand`,
  fractions `n/n`, `%`, `log|sin|cos|tan`, units, equation `... = ...`, explicit
  "what is/solve/compute/calculate/evaluate". Returns `intent: str|None` and
  `direct: bool` (bare numeric/symbolic expression → can bypass the model).
- `solve(expression, intent) -> MathResult` (pydantic: `ok, latex,
  steps: list[str], exact, numeric, verified: bool, error: str|None`). SymPy:
  `sympify`, `simplify`, `Rational`/`nsimplify` for exact fractions, `solve`/`solveset`,
  `diff`, `integrate`, `factor`, `limit`, trig identities, `sympy.physics.units` for
  conversions, manual stats/probability helpers (mean/variance/nCr/nPr/dice).
- `_verify(result)` — substitute symbolic solutions back into the original expression
  and assert residual ≈ 0; cross-check numeric by recomputation (item 7).
- Safety — catch `SympifyError`, `ZeroDivisionError`, `PolynomialError`, `TypeError`
  → `fail("MATH_ERROR", "I couldn't parse/compute that…")`; **never invent** (item 8).
  Word-problem path: `_extract_givens(text)` parses numbers + units + keywords, maps
  to a formula, solves, verifies (item 4).
- `format_reply(r) -> str` — markdown `$$ ... $$`/`\( ... \)` + numbered steps +
  `> **Answer: ...**` (local CSS `.math-answer`).

**Step B2 — `app/tools/math_tools.py`** (mirrors `maps_tools.py` skeleton):
```python
def calculate(expression: str, intent: str | None = None) -> ToolResult:
    """Evaluate a mathematical expression/equation and return steps + the verified answer."""
    try:
        r = math_engine.solve(expression, intent)
    except Exception:
        return ToolResult.fail("MATH_ERROR", "Could not compute.", message="I couldn't work that out — please check the expression.")
    if not r.ok:
        return ToolResult.fail("MATH_ERROR", r.error, message=r.error)
    return ToolResult.ok(math_engine.format_reply(r), r.model_dump())
```
Sync is fine (`StructuredTool.from_function(func=...)`; `ainvoke` still works at graph.py:434).

**B3 — Register + pin.**
- `tools/__init__.py`: `from app.tools import math_tools as _math_tools` +
  `registry.register(_math_tools.calculate, permission=PermissionLevel.AUTO,
  category="math", tags=["math"])`.
- `requirements.txt`: add `sympy==1.14.0`.

**B4 — Intent + routing so the model can call `calculate`.**
- `agents/intent.py`: add `_MATH` regex + `("math", _MATH)` branch in
  `classify_intent` (priority after `coding`, before `research`).
- `tools/routing.py`: add `_MATH = frozenset({"calculate"})` and
  `_INTENT_TOOLS["math"] = _MATH | frozenset({"current_datetime"})`; also add
  `calculate` to the `"chat"` set so a math question classified as `chat` can still
  call it.

**B5 — Fast-path in `chat_service.execute_chat` (items 6 & 9).**
Right after `intent = classify_intent(...)` (line 376), BEFORE any model/Ollama call:
```python
from app.services import math_engine
signal = math_engine.classify_math(request_message)
if signal and signal.direct:
    result = math_engine.solve(request_message, signal.intent)
    reply = math_engine.format_reply(result)
    reply, citations, diagrams = apply_visual_postprocessing(intent="math", reply=reply, sources={})
    outcome = ChatOutcome(
        success=result.ok, conversation_id=conversation_id, run_id=run_id,
        task_id=task_id, reply=reply, sources=[], model="sympy", usage={},
        approval=None, status=("completed" if result.ok else "error"),
        intent="math", plan=[], artifacts=[], citations=citations, diagrams=diagrams,
        **_({"error": result.error} if not result.ok else {}),
    )
    emitter.emit("intent", {"intent": "math", "signals": []})
    emitter.emit("token", {"text": reply})
    emitter.emit("done", outcome.as_response())
    return outcome
```
This guarantees verified numbers are never overridden by Ollama (item 6) and math
works with no model at all (item 9). Indirect/word-problem math still flows through
the agent with `calculate`+`verify_math` bound (item 3/4/7); existing SSE `done`
already emits `citations`/`diagrams` and `finalize` already calls
`apply_visual_postprocessing`, so LaTeX renders via the existing MathJax pipeline
with no new frontend plumbing.

**Step B6 — Frontend (minimal).** `ChatView` already typesets MathJax on messages.
Add one CSS rule for the highlighted answer:
```css
.math-answer { padding: 10px 14px; background: #14233f; border:1px solid #24406e;
  border-radius: 8px; font-weight: 600; margin: 8px 0; color: var(--accent-2); }
```
The reply markdown (`$$…$$`/`\(…\)`) already isolates and renders; MathJax skips code
blocks (per the visual-output work). No new npm deps (MathJax CDN already present).

### Scope guard
Backend-first: B1→B2→B3→B5→B4, then A1→A3. Touch **only** `services/math_engine.py`,
`tools/math_tools.py`, `tools/__init__.py`, `agents/intent.py`,
`tools/routing.py`, `chat_service.py` (fast-path), `config.py`/`main.py` (A1),
`SchedulesView.tsx` (A3), `requirements.txt`, `styles.css` (B6), tests. Do **not**
touch auth/voice/memory/comms/graph orchestration beyond the `calculate` tool
binding and the fast-path.

## Testing strategy

- `tests/test_math.py`: exact-fraction arithmetic (1/2+1/3=5/6), algebra solve
  (x²−5x+6 → 2,3) with substitution verify, calculus (`d/dx x³=3x²`; `∫3x²=x³`; limit
  sin x/x=1), trig identity, stats/prob (mean/variance/nCr), unit conversion (km→m),
  word problem (150km/3h→50km/h), safety (division-by-zero/invalid expr → fail, never
  invents), and `classify_math(...).direct` truth table.
- `tests/test_rate_limit_phase_l.py`: add a case that whitelisted GET list endpoints
  are NOT consumed by the global bucket while writes still are.
- `tests/test_react_frontend.py`: assert served `dist/index.html` still references
  workspace/schedule bundles (stale-build guard, covers A4).
- Run: `python -m pytest tests/test_math.py tests/test_chat.py tests/test_visuals.py
  tests/test_rate_limit_phase_l.py tests/test_react_frontend.py tests/test_phase_j_sandbox.py
  tests/test_phase_g_schedules.py -q` → expect green; `node
  node_modules/typescript/bin/tsc -b --noEmit` → 0 errors.

## Next steps (post-audit)
- App-wide 429 backoff for **writes** (POST/DELETE in Tasks/Schedules/etc.): surface
  the 429 as a toast + retry. Reads are already whitelist-safe (A1). Low priority.
- `tests/test_phase_g_schedules.py` happy-path for the create/toggle/delete flow +
  the new error-state rendering (optional; the A3 logic is covered indirectly).
- Local run: `uvicorn app.main:app --port 8000` + `node node_modules/vite/bin/vite.js dev`
  (frontend React 19 dev server, proxies `/api` → `:8000`).

---

## Appendix — AI Image Generation Improvement Plan (Phase D Image)

> **Status (as of 2026-10-12): Implemented & green.** All 58 tests pass
> (`test_phase_d_image_generation.py` 39 — including 12 new `TestImageQuality`
> assertions + 4 AI-path tests that run with torch installed — + `test_phase_d_media.py`
> 9 + `test_phase_d_generation.py` 8 + `test_creative.py` 1). `torch`, `diffusers`,
> `transformers`, `safetensors`, `accelerate`, `rembg` are installed and
> importable. The lazy-import pattern is preserved so the app runs without them
> (procedural fallback).

### Image quality assessment (new)
- `app/services/creative/image_quality.py` — `assess_image_quality(data: bytes) -> dict`
  computing `quality_score`, `color_variance`, `entropy`, `distinct_colors`,
  `edge_density`, `width`, `height`, `passes_qa` using only numpy + PIL.
  Thresholds: `_MIN_COLOR_VARIANCE=5.0`, `_MIN_ENTROPY=3.0`,
  `_MIN_EDGE_DENSITY=0.02`, `_MIN_DISTINCT_COLORS=10`. Quality score weights:
  color_variance 30%, entropy 25%, edge_density 25%, distinct_colors 20%.
- `validate_image_output(data, provider, min_quality=False)` — when `min_quality=True`,
  calls `assess_image_quality()` and raises `CreativeEngineError("LOW_QUALITY", ...)`
  if `passes_qa=False`. Applied to OpenAIImageProvider, GoogleImageProvider, and
  LocalImageProvider `_pipeline_call()` (AI paths only). Procedural path uses basic
  validation only and attaches metrics to metadata for observability.
- Quality metrics are attached to `GenOutput.meta` on every output (AI + procedural).

### Root cause of abstract gradient images
`LocalImageProvider._ai_generate()` returned `None` because `can_load_torch()` and
`can_load_diffusers()` returned `False` (packages not installed). The provider then
fell back to `_procedural_generate()` which maps prompt keywords to colors and
draws random ellipses — producing abstract gradients with translucent circles.

### Research findings (scraped from Wikipedia + HuggingFace API + diffusers source)
- **Stable Diffusion** (SDXL-Lightning): 4-step distilled, `guidance_scale=0`,
  `EulerDiscreteScheduler` with `timestep_spacing="trailing"`, `torch.float16`,
  UNET-only checkpoint (~137 MB). Not gated on HuggingFace.
- **SD 1.5 + LCM-LoRA**: 4-step distilled, `guidance_scale=0`, LoRA adapter loaded
  via `load_lora_weights()` + `fused_lora()`. 512×512.
- **DALL·E / GPT Image API**: prompt expansion before synthesis (mirror this with
  structured prompt templates). `gpt-image-1` model via `openai` package.
- **Midjourney / Imagen**: no direct local equivalent; cloud providers used as
  fallback when API keys are configured.

### Implementation steps completed

**Step 1 — Dependencies installed** (all CPU-only):
- `torch==2.14.1+cpu` from `https://download.pytorch.org/whl/cpu`
- `diffusers==0.41.0`, `transformers==5.20.1`, `safetensors==0.8.0`,
  `accelerate==1.15.0`, `rembg==2.0.85`, `huggingface_hub==1.33.0`

**Step 2 — Production-quality image generation service** (providers.py):
- `LocalImageProvider._ai_generate()` now checks torch/diffusers availability
  (lazy import), resolves model via `model_registry`, loads pipeline with
  `_load_pipeline()` (cached per model+device+mode), runs inference via
  `_pipeline_call()` with timing + QA validation.
- `validate_image_output()`: checks image size > 100 bytes, validates via
  `PIL.Image.verify()` — raises `CreativeEngineError("EMPTY_OUTPUT")` on
  corrupt/empty data. Applied to both OpenAI and local AI provider outputs.
- `_pipeline_call()` tracks `start_time`/`duration_seconds`, validates each
  output, and includes model/seed/mode/steps/guidance in metadata.

**Step 3 — Structured prompt enhancement** (prompt_engine.py):
- `detect_image_type()` keyword-matches prompt against 7 type categories
  (portrait, landscape, product, poster, illustration, anime, oil painting).
- `format_prompt()` applies type-specific style templates (`_STYLE_TEMPLATES`)
  + quality keywords + aspect-ratio composition guidance, mirroring DALL·E 3's
  prompt expansion approach.
- `build_negative_prompt()` uses the standard SDXL "bad-hand" list extended
  with user-provided negative prompts.
- `build_generation_kwargs()` bridges `GenRequest` → diffusers pipeline kwargs.
- OpenAI provider also calls `format_prompt()` for enhanced cloud prompts.

**Step 4 — Image editing** (img2img, inpainting, background removal):
- `_load_pipeline()` selects the correct pipeline class per (model × mode):
  `txt2img`, `img2img`, `inpaint`.
- `_pipeline_call()` handles img2img with `strength` parameter (reads from
  `req.extra["strength"]`, falls back to `settings.image_local_strength`).
- Inpainting uses `mask_image` with `StableDiffusionXLInpaintPipeline`.
- `LocalImageProvider.remove_background()` uses `rembg` (U²-Net) for
  transparent-background output. `transparent_bg` capability updated to `True`.
- API endpoint `/generate/image` supports `reference_asset_id` (img2img),
  `mask_asset_id` (inpaint), `strength`, `image_type`, and
  `remove_background` fields.

**Step 5 — Quality assurance**:
- `validate_image_output()` on all provider outputs (OpenAI, local AI).
- Duration tracking in `_pipeline_call()`.
- Cost estimation: OpenAI `$0.04 * num_variations`, Google `$0.039` per call,
  local `$0.00`.
- Error mapping: `_map_error()` converts API errors to structured codes
  (`PROVIDER_QUOTA`, `PROVIDER_AUTH`, `MODEL_UNAVAILABLE`, etc.).
- Graceful degradation: if model can't load (RAM, download failure), falls
  back to procedural engine with `ai=False` metadata.

**Step 6 — Creative interface**:
- `CreativeStudioView.tsx` has provider dropdown, negative prompt field,
  seed input, img2img strength slider, capabilities warning banner,
  metadata display.
- `/capabilities` endpoint lists all providers with their capabilities.
- `generate_image` tool in `creative_tools.py` accepts `strength` parameter.

**Step 7 — Security & cost control**:
- Image generation API key (`JALORA_OPENAI_API_KEY`) stays backend-only;
  never sent to frontend (OpenAI provider initialized from `settings`).
- Rate limiting: `_check_image_rate_limit()` — sliding-window per-user counter
  (default 20/min, configurable via `image_rate_limit_per_min`).
- Cost cap: `image_cost_cap_usd` config field (default $50/month).
- `TooManyRequestsError` (HTTP 429) raised when limit exceeded.

**Step 8 — Testing** (all 58 tests green, 0 failures):
- `TestModelRegistry` (9): model specs, aliases, params, RAM selection, aspect sizes.
- `TestPromptEngine` (6): prompt enhancement, negative prompts, generation kwargs.
- `TestLocalImageProvider` (5): capabilities, procedural fallback, reproducibility,
  multiple variations.
- `TestLocalAIProvider` (4): AI path with mocked pipeline (no download needed),
  metadata validation, fallback on error, img2img strength.
- `TestImageGenerationAPI` (3): API returns real PNGs, seed reproducibility,
  capabilities endpoint.
- `TestImageQuality` (**12 new**): procedural color variance (>5), distinct colors
  (>10), dimension/aspect matching, entropy (>3), metadata completeness, variation
  diversity, prompt richness, image-type detection, quality-gate rejection of
  solid colors and corrupt data, quality-gate acceptance of real images, and API
  metadata includes quality metrics.
- `test_creative.py` (1): Happy-path creative project flow.

### Environment
- Windows 11, Intel CPU, ~2 GB available RAM (varies), Python 3.12.3.
- CPU-only torch (no GPU). Models that fit RAM are selected automatically;
  otherwise procedural fallback is used transparently.
- SDXL-Lightning is NOT gated (no HF token needed for base/Lightning UNet;
  token only needed for gated inpainting models).

### Known limitations
- On this machine (~2 GB available RAM), no diffusion model fits in memory, so
  the AI path falls back to procedural generation. The AI code path is verified
  via mocked tests. On a machine with 6+ GB free RAM, `sdxl-lightning-4step`
  will be used for real AI generation.
- `rembg` is installed but requires a model download (~100 MB) on first use.
- OpenAI GPT Image API requires `JALORA_OPENAI_API_KEY` to be set in the
  environment.
