# Plan — Agent Upgrade: make Jalora a working general-purpose assistant (Ollama-first)

## Overview

Current behavior is broken by **two precise, fixable causes** — not a missing backend:

1. **No model provider is reachable.** Ollama is installed with the spec's models
   (`qwen2.5:0.5b/1.5b/7b`, `llama3.2:latest`, already pulled) but its **daemon is not
   running**, and `list_secrets` is empty (no Gemini/Openai/etc. keys). So every
   `chat`-intent turn (`POST /api/chat`) fails and the frontend renders
   "couldn't finish the reply." **Math is the only thing that works** because
   `chat_service.execute_chat` (lines 408–470) runs `math_engine.solve` (SymPy,
   offline) as a fast-path that bypasses the model entirely.

2. **Math classification edge cases — not a missing CAS.** `math_engine.py`
   *already* contains full SymPy CAS (`_do_integral`→`sp.integrate`,
   `_do_derivative`→`sp.diff`, `_do_limit`, equation solving via `sp.solve`/`factor`).
   The failures are mis-classifications:
   - `intergrate 3x^2` (your typo) → `_CALC` regex misses the misspelling →
     intent=`arithmetic` → "couldn't parse … as a numeric expression".
     (Correctly-spelled `integrate 3x^2` already returns `x**3`.)
   - `2 - 5x + 6 = 0` → `_EQU` needs a letter immediately before `=` → mis-routed to
     arithmetic → it summed `[2,5,6,0]` → `13` (wrong).
   - `x^2 - 5x + 6 = 0` works only because the leading `^2` pushes it into algebra.

Fix = (1) start the Ollama daemon so all chat/research/coding runs through a real
model; (2) harden `classify_math`/`solve` so any `=` → algebra and calculus verbs
tolerate typos. Web search, browser automation, email/SMS/WhatsApp/voice, booking
and scheduling are larger and **secret/network-gated** — planned in Phases 2–5, but
most cannot be E2E-tested in this sandbox (no Chromium binary to install — Playwright
CDN blocked — and no provider egress without secrets).

## Implementation approach

### Phase 1 — unblock conversation + fix math (sandbox-verifiable, do this first)

**Step 1 — Start the Ollama daemon (process only, no code).**
```
ollama serve            # background on localhost:11434
```
Verify `curl http://127.0.0.1:11434/api/tags` → model list; then a general QA through
`POST /api/chat` returns a real reply from `qwen2.5:7b`. After this, "all tasks that
are not maths" route to a live local model instead of erroring.

**Step 2 — Add cloud fallback keys as secrets (not code).** Ollama stays primary only
if `JALORA_DEFAULT_PROVIDER=ollama` is set; otherwise `auto` prefers cloud. Provision
via the secrets workflow:
- `JALORA_GOOGLE_API_KEY` (Gemini — your requested primary fallback)
- `JALORA_OPENAI_API_KEY` (OpenAI backup)
`model_router.build_provider_specs` already reads these; `provider_status()` reports them.
No code change — provisioning only.

**Step 3 — Harden `classify_math` / `solve` (small, SymPy-backed, testable).**
File: `app/services/math_engine.py`.
- (a) Equation detection: any `=` → `algebra` intent, attempt `sp.solve`/`sp.factor`;
  relax `_EQU` so it matches `2 - 5x + 6 = 0` (drop "letter immediately before `=`").
- (b) Calculus-verb tolerance: normalize misspellings before matching:
```python
_CALC_VERB_FIX = {"intergrate": "integrate", "diferentiate": "differentiate",
                  "derivitive": "derivative", "limmit": "limit"}
def _fix_calc_verbs(t: str) -> str:
    for bad, good in _CALC_VERB_FIX.items():
        t = re.sub(rf"\b{bad}\b", good, t, flags=re.I)
    return t
# `_try_calculus`/`_do_integral` normalize the verb before regex matching.
```
- (c) Guard `_do_integral` so a still-unparseable body yields a structured
  "I don't recognize the operation" error (never the arithmetic `13`-style sum).

Tests: `integrate 3x^2`→`x^3`; `intergrate 3x^2`→`x^3`; `x^2 - 5x + 6 = 0`→`2,3`;
`2 - 5x + 6 = 0`→solved as equation (no longer `13`). Extend `tests/test_math.py`.

### Phase 2 — memory, planning, autonomy (largely present — verify + wire)
`solutions/math_engine` aside, `chat_service` already injects memory (`sql`
vectorstore), summarizes long history, and runs the LangGraph agent
(`app/agents/graph.py`) over `AgentRun`/`Task`/`Approval` models with pause/resume/cancel.
Verify: (a) `memory_vectorstore=sql` resolves; (b) history summarization threshold
respected; (c) approval flow for sensitive tools. This is a test/verify phase, not a
build phase — no structural gap.

### Phase 3 — research, browser, diagrams (secret/network-gated)
- **Web research**: `search_provider` is `mock` in the test env. Switch to `duckduckgo`
  for live (fragile) or add `JALORA_TAVILY_API_KEY` secret for reliable search.
- **Browser automation**: `browser_driver="auto"`; real driver needs Playwright+Chromium.
  **Sandbox could not install Chromium** (Playwright CDN unreachable) → keep
  `fake`/`mock` for offline tests; real browser only where Chromium can install.
  SSRF guard (`browse_allow_private=False`) is already in place.
- **Diagrams (3D vectors)**: `apply_visual_postprocessing` (`app/services/visuals.py`)
  exists. Add an SVG/matplotlib diagram tool that emits a renderable `<svg>`/canvas
  referenced via `ChatOutcome.diagrams` so "explain 3D vectors" returns a diagram.

### Phase 4 — voice & communication (credential-gated stubs)
All `mock`-first in `config.py`; each becomes live only with a secret + egress:
- SMS/WhatsApp/Voice → `comms_provider="twilio"` + `JALORA_TWILIO_ACCOUNT_SID`/
  `TWILIO_AUTH_TOKEN`/`TWILIO_FROM_NUMBER`; `calls_base_url` must be public for webhooks.
- Email → `email_provider="gmail"` + `JALORA_GMAIL_CLIENT_ID`/`GMAIL_CLIENT_SECRET`
  (OAuth); `gmail_callback_url` already derives from `public_base_url`.
- Voice (mic/STT→LLM→TTS) → `voice_stt_provider="faster_whisper"`,
  `voice_tts_provider="piper"`, voice LLM = Ollama (already Ollama-first in config).
Implement the real code paths behind the existing `auto|mock` switch; test only the
`mock` path offline here — live paths need provider credentials and network we don't have.

### Phase 5 — scheduling, security, perf, production-readiness
- **Scheduling**: `scheduler_enabled` (off in test; on in prod) + `redis_url` for
  multi-worker; `Task` model + retry/pause/resume already modelled.
- **Security**: JWT auth (done), per-tool `PermissionLevel` + approvals (done), secrets
  via env (done — currently none), audit logs (exist), rate limits (exist), SSRF guard
  (exist), Phase‑J sandbox (done). Harden: rotate dev JWT secret, refuse prod without
  secrets.
- **Perf**: `metrics_enabled=True`; add latency / time-to-first-token + an accuracy eval
  set; cache safe repeatable ops; record known-failure patterns.

## Testing strategy
- Phase 1: `curl http://127.0.0.1:11434/api/tags` (daemon up); `POST /api/chat {"message":"Who is the current president of Kenya?","stream":false}` returns a non-empty `reply` from qwen2.5:7b; `math_engine` unit tests for integrate (incl. the `intergrate` typo), equation solve, and the `2 - 5x + 6 = 0` regression (no longer `13`). Gate: `pytest tests/test_math.py -q` 0 failures + a live `curl` QA returning a real assistant reply. Phase 2–4 live paths are tested only with provisioned secrets + network, which are out of scope for this sandbox and stay mock.

## Implementation status (live — done in this session)

**Phase 1 — DONE & verified live.**
- Ollama daemon confirmed up on `http://127.0.0.1:11434` (models `qwen2.5:0.5b / 1.5b / 7b`, `llama3.2`).
- `JALORA_DEFAULT_PROVIDER=ollama` makes the router Ollama‑first (your rule). This single change flipped non‑math chat from `502 "couldn't finish the reply"` to **HTTP 200 + a correct answer** ("The current president of Kenya is William Ruto").
- Root cause of the old 502: `auto` priority tried OpenAI first using an **exhausted key** present in the inherited environment (429 `credit_balance_exhausted` surfaced as 502). With Ollama‑first, that key is no longer touched.
- Math fix applied to `app/services/math_engine.py`: `_fix_calc_verbs()` normalizes calculus‑verb typos (`intergrate`→`integrate`, …) before classification *and* before `_try_calculus`, so `intergrate 3x^2` now routes to `sp.integrate` → `x**3`. Equations already solved via `solve()`'s `if "=" in text: _try_equation`.
- Verified: `integrate 3x^2`→`x³`, `intergrate 3x^2`→`x³`, `x²−5x+6=0`→`2,3`, `2−5x+6=0`→`8/5` (no `13`). In‑process + E2E through `/api/chat` (`model=sympy`, `intent=math`). `test_math.py` = 60 passed.
- Robustness bump: `JaloraChatModel.timeout` 120→300s so CPU inference under load doesn't `ReadTimeout`; existing OOM‑degrade (`_retryable_ollama`, `ollama_memory` 4‑tier ladder + background unloader) already handles true out‑of‑memory by stepping down to a lighter model.

**Tests:** `test_math.py` 60 passed (incl. 2 new regression tests). Full backend gate `577 passed, 6 skipped, 3 errors` — the 3 errors are `ModuleNotFoundError: chromadb` in `test_memory_v2.py` (Chroma is an **optional** dep; runtime uses `JALORA_MEMORY_VECTORSTORE=sql`). Zero regressions from the edits. tsc + vite were green earlier and no frontend code changed.

**Servers live now:** backend `be5` (`uvicorn app.main:app`, `:8000`, Ollama‑first, 300s); frontend `fe2` (`vite`, `:5173`, `/api`→`:8000` proxy).

**Open / secret‑gated (cannot be E2E‑proven in this sandbox — needs your credentials + egress):**
- Cloud fallbacks require secrets: set `JALORA_GOOGLE_API_KEY` (Gemini) and `JALORA_OPENAI_API_KEY` (OpenAI) as **secrets** to enable the fallback chain. The OpenAI key currently in the environment is **exhausted** (no credits) — either add credits or rely on Ollama‑first.
- Web search: `JALORA_SEARCH_PROVIDER=mock` now → switch to `duckduckgo` (fragile) or add `JALORA_TAVILY_API_KEY`.
- Browser automation: Playwright **Chromium cannot be installed here** (its CDN is unreachable in this sandbox) → stays `JALORA_BROWSER_DRIVER=fake`; enable `real` + install Chromium elsewhere.
- Email/SMS/WhatsApp/Voice/booking: providers are `mock`‑first; enable with `JALORA_TWILIO_*`, `JALORA_GMAIL_*`, and real egress + a public webhook URL.
- Scheduling: `JALORA_SCHEDULER_ENABLED=false` here; enable in prod (with `redis_url` for multi‑worker).
