"""Application configuration loaded from environment / .env.

Security rules:
  * API keys are only ever read from the environment, never from source code.
  * Settings are validated at startup; production refuses weak secrets.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("jalora.config")

BASE_DIR = Path(__file__).resolve().parents[2]  # .../jalora

DEV_SECRET = "jalora-development-insecure-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JALORA_",
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "development"
    app_name: str = "Jalora AI"
    version: str = "0.1.0"

    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'jalora.db'}"
    data_dir: Path = BASE_DIR / "data"
    upload_dir: Path = BASE_DIR / "data" / "uploads"
    # Phase D (§55): generated files (pdf/docx/xlsx/csv) land here per user.
    outputs_dir: Path = BASE_DIR / "data" / "outputs"
    # Creative Studio: creative project media + render outputs live under the
    # same per-user upload root (served by the owner-only /api/artifacts route).
    creative_dir: Path = BASE_DIR / "data" / "uploads"

    jwt_secret: str = DEV_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # AI providers -----------------------------------------------------------
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_reasoning_model: str = "gpt-4o"
    openai_reasoning_effort: str = "medium"

    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_model: str = "claude-sonnet-4-5"

    google_api_key: str | None = None
    google_model: str = "gemini-3.5-flash-lite"

    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-20b"

    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-chat"

    mistral_api_key: str | None = None
    mistral_model: str = "mistral-small-latest"

    openrouter_api_key: str | None = None
    openrouter_model: str = "openrouter/free"

    nvidia_api_key: str | None = None
    nvidia_model: str = "nvidia/nemotron-3.5-lightning-30b-a3b"

    # Agent chat generation limits. Long research, computation, and artifact
    # workflows need more time and output room; both remain bounded/configurable.
    model_request_timeout_seconds: float = Field(default=600.0, gt=0, le=1800)
    agent_max_output_tokens: int = Field(default=8192, ge=2048, le=32768)

    # Poolside-hosted OpenAI-compatible inference.
    poolside_api_key: str | None = None
    poolside_model: str = "laguna-s-2.1"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"

    # LMStudio — local, keyless, OpenAI-compatible endpoint.
    # Runs on localhost; defaults to the standard LMStudio server port.
    lmstudio_base_url: str = "http://localhost:1234/v1"
    lmstudio_model: str = "meta-llama-3.1-8b-instruct"

    # Jan.ai — local/remote OpenAI-compatible endpoint.
    # The user sets their own Jan server URL; defaults to None (not active).
    jan_base_url: str | None = None
    jan_model: str = "llama3-8b-instruct"

    # Preferred local provider for auto-routing when no cloud key is set or as
    # the first fallback. Options: "lmstudio" (default), "jan", "ollama".
    local_provider_preference: str = "lmstudio"

    # Cohere — cloud provider with OpenAI-compatible endpoint.
    cohere_api_key: str | None = None
    cohere_model: str = "command-r-plus"

    # Cerebras Cloud — cloud provider with OpenAI-compatible endpoint.
    cerebras_api_key: str | None = None
    cerebras_model: str = "qwen-3.8-27b"

    # Perplexity — cloud provider with OpenAI-compatible endpoint.
    perplexity_api_key: str | None = None
    perplexity_model: str = "sonar"
    perplexity_strong_model: str = "sonar-reasoning"
    # Memory-aware local model loading: `ollama_model` is the capable model,
    # `ollama_mid_model` the intermediate tier, `ollama_light_model` the
    # lightweight fallback, and `ollama_survival_model` the ultra-light last
    # resort for severely RAM-constrained moments. The router picks the
    # heaviest model that fits in free RAM (minus the resident footprint of
    # the other already-loaded tier models); a background monitor also unloads
    # loaded heavy models when free RAM dips under `safe_ram_floor_gb` so
    # llama-server never OOM-crashes.
    ollama_mid_model: str = "llama3.2:latest"
    ollama_mid_model_min_free_gb: float = 2.0
    ollama_light_model: str = "qwen2.5:1.5b"
    ollama_heavy_model_min_free_gb: float = 4.0
    ollama_light_model_min_free_gb: float = 1.0
    ollama_survival_model: str = "qwen2.5:0.5b"
    ollama_survival_model_min_free_gb: float = 0.4
    ollama_safe_ram_floor_gb: float = 1.5
    ollama_memory_monitor: bool = True

    # Ollama model residency: how long to keep each tier resident after a
    # request before it is swapped out again (seconds). Heavy models are
    # released quickly because they hog RAM; the light/survival tiers stay for
    # a long time so small requests are fast.
    ollama_keep_alive_heavy_seconds: int = 120
    ollama_keep_alive_mid_seconds: int = 600
    ollama_keep_alive_light_seconds: int = 900
    ollama_keep_alive_survival_seconds: int = 3600

    # Ollama generation options (stage 4): tuned defaults sent with every
    # local model call. num_ctx scales per tier so the heavy model gets a real
    # long context while the survival model stays fast and small. top_p is
    # relaxed a touch and repeat_penalty raised to cut repetition hallucinations
    # on the small models; temperature stays at the 0.6 sweet spot.
    ollama_num_ctx_heavy: int = 32768
    ollama_num_ctx_mid: int = 16384
    ollama_num_ctx_light: int = 16384
    ollama_num_ctx_survival: int = 8192
    ollama_top_p: float = 0.9
    ollama_repeat_penalty: float = 1.15

    # Intent-based tool binding (stage 4): bind only the tools relevant to the
    # turn's intent instead of the full registry, so local models see fewer
    # tool schemas and stay on task. False = bind everything (old behaviour).
    agent_tool_routing_enabled: bool = True

    # Shared HTTP connection pool (all providers): reuse keep-alive connections
    # instead of opening a fresh client for every model call.
    http_pool_max_connections: int = 200    # Increased for better concurrency
    http_pool_max_keepalive_connections: int = 50  # Increased for faster reuse

    # Optimized timeout for local model calls - faster fallback
    ollama_timeout_seconds: float = 60.0  # Reduced from 120s for better UX
    tool_timeout_seconds: float = 45.0  # Reduced from 60s for faster tool timeouts

    # Tool results persisted in the DB are trimmed back to this many characters
    # when reloaded for a later turn so small local models never see a wall of
    # stale tool output (the "(truncated)" marker keeps the response honest).
    history_tool_truncate_chars: int = 4000

    default_provider: str = "auto"
    default_reasoning_effort: str = "medium"
    embedding_provider: str = "auto"
    search_provider: str = "duckduckgo"  # auto | duckduckgo | tavily | mock
    tavily_api_key: str | None = None
    search_timeout_seconds: float = 45.0  # per-search-provider call timeout
    public_contact_search_concurrency: int = Field(default=4, ge=1, le=8)

    # Phase D (§42-43): media understanding backends. whisper auto-preference:
    # OpenAPI audio API (key required) -> local whisper.cpp binary -> mock.
    whisper_provider: str = "auto"  # auto | openai | local | mock
    whisper_model: str = "whisper-1"
    ffmpeg_path: str = ""  # empty = auto-detect on PATH

    # ---- Creative Studio (AI image/video generation + professional editing) --
    # Master switch for the creative endpoints/UI.
    creative_enabled: bool = True
    # Uploads are validated by magic bytes; this caps the decoded size on top of
    # the per-kind media.py limits (creative assets can be large source footage).
    creative_max_upload_mb: int = 512
    # Hard ceiling on any single render/generation duration (seconds) so a job
    # cannot occupy a worker indefinitely.
    creative_max_render_seconds: int = 1800
    # Background worker concurrency for generation/render jobs (thread pool).
    creative_jobs_concurrency: int = 2
    # Provider selection for each generation media kind. `auto` prefers a cloud
    # provider with a configured key, then falls back to the local engine.
    image_provider: str = "auto"  # auto | openai | google | local
    image_model: str | None = None  # blank = provider default
    video_provider: str = "auto"  # auto | openai | google | local
    video_model: str | None = None  # blank = provider default
    audio_provider: str = "auto"  # auto | openai | google | local
    # faster-whisper size for creative transcription (tiny|base|small|...).
    creative_stt_model: str = "base"

    # Local AI image generation (diffusers / Stable Diffusion). Lazy-imported:
    # the app runs without these packages, falling back to the procedural engine.
    # When installed, the local provider downloads/caches the configured model on
    # first use. Models are tiered by VRAM/RAM requirement (see model_registry).
    image_local_model: str | None = None  # blank = auto-select best fit by RAM (see model_registry)
    image_local_steps: int = 4          # inference steps (Lightning/LCM: 1-8; default: 20-50)
    image_local_guidance_scale: float = 0.0  # 0.0 = no CFG (distilled models use CFG=0)
    image_local_strength: float = 0.75   # img2img denoising strength (0=keep ref, 1=full regen)
    huggingface_token: str | None = None  # for gated models (env: JALORA_HF_TOKEN)
    image_local_device: str = "auto"    # auto | cpu | cuda | mps
    image_local_max_ram_gb: float = 4.0  # minimum free RAM to attempt SDXL tier

    # Rate limiting for creative generation endpoints (per authenticated user).
    image_rate_limit_per_min: int = 20  # txt2img/img2img/inpaint calls per minute
    # Cost control: per-user monthly credit cap for cloud image generation (USD). 0 = unlimited.
    image_cost_cap_usd: float = 50.0

    # Browser automation (§7–8): private/internal addresses blocked by default
    browse_allow_private: bool = False
    browser_timeout_seconds: float = 20.0
    # Phase C: persistent session driver. "real" = Playwright, "fake" = offline
    # deterministic driver (tests), "auto" = real when playwright is importable.
    browser_driver: str = "auto"  # auto | real | fake
    browser_session_idle_seconds: float = 900.0  # safety net close for idle sessions

    # Phase C (§18): login credentials are encrypted at rest with a Fernet key.
    # Set JALORA_CREDENTIALS_ENCRYPTION_KEY to a strong random key in production;
    # it derives from JWT secret when unset (dev convenience only).
    credentials_encryption_key: str | None = None

    # Comms (§14–18): mock-first; supply a NEW key (never reuse exposed creds §77).
    # `auto` prefers Telnyx when configured, then Twilio, then the offline mock.
    comms_provider: str = "auto"  # auto | mock | twilio | telnyx
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None       # SMS, e.g. +15005550006
    twilio_whatsapp_from: str | None = None     # WhatsApp, e.g. +14155238886

    # Telnyx (§14–18 voice/messaging): the primary provider. Voice uses the
    # TeXML (TwiML-compatible) product, so the existing <Say>/<Gather>/<Stream>
    # logic is reused. `account_sid` is the TeXML account UUID used in the
    # /v2/texml/Accounts/{sid}/Calls path; `application_id` is the TeXML
    # application that supplies the number + webhook; `messaging_profile_id`
    # routes outbound SMS/WhatsApp.
    telnyx_api_key: str | None = None
    telnyx_public_key: str | None = None           # Ed25519 pubkey (webhook verify)
    telnyx_account_sid: str | None = None          # TeXML account UUID
    telnyx_application_id: str | None = None       # TeXML application (ApplicationSid)
    telnyx_connection_id: str | None = None        # Call Control connection (optional)
    telnyx_from_number: str | None = None          # voice caller ID + SMS sender
    telnyx_whatsapp_from: str | None = None        # WhatsApp-enabled number
    telnyx_messaging_profile_id: str | None = None # SMS/WhatsApp messaging profile

    # Voice calls (§31-33, §57): turn/duration budgets enforced by the caller
    # regardless of provider; `calls_base_url` is the public base URL where the
    # TwiML answer/gather/status webhooks and the media-stream WS are reachable.
    call_max_turns: int = 20
    call_max_duration_seconds: int = 600
    calls_base_url: str = "http://localhost:5173"

    # Real-time voice pipeline (in-app live call, priority 1). Unlike the batch
    # whisper chain, this streams audio both ways: STT -> LLM -> TTS. Local
    # engines are preferred (Ollama-first, keyless, offline-capable); cloud
    # providers are opt-in fallbacks. Every provider resolves local -> cloud ->
    # mock, so the feature degrades to a deterministic offline loop rather than
    # failing when an engine or key is missing.
    voice_enabled: bool = True
    voice_stt_provider: str = "auto"   # auto | faster_whisper | whisper_cpp | deepgram | openai | mock
    voice_llm_provider: str = "auto"   # auto | ollama | cloud
    voice_tts_provider: str = "auto"   # auto | piper | kokoro | openai | mock
    # Ollama-first latency guard: if a local turn's time-to-first-token exceeds
    # this budget the session may switch to `voice_fallback_provider` for the
    # rest of the call. 0 keeps strict Ollama-first (never auto-switch).
    voice_latency_budget_ms: int = 1200
    voice_fallback_provider: str | None = None
    # Live-voice RAM guard (§1.14/§1.17): reject a model whose estimated resident
    # footprint exceeds this budget (MB) in favour of a lighter route, so a
    # heavy model can't OOM the host on a memory-constrained machine. 4096 MB
    # is safe on a 16 GB host alongside STT/TTS; lower it for tiny boxes.
    voice_model_memory_budget_mb: int = 4096
    # Phone transport (Phase 2/3): Twilio Media Streams bridging to VoiceSession.
    # `voice_public_url` is the externally reachable base (scheme+host) Twilio
    # uses for webhooks and the media WebSocket; falls back to `calls_base_url`
    # with http->ws. Inbound calls are handled only when explicitly enabled and
    # mapped to an owner (email) so the AI knows whose calendar/contacts to use.
    voice_public_url: str | None = None
    voice_inbound_enabled: bool = False
    voice_inbound_email: str | None = None
    voice_stream_token_seconds: int = 3600  # e.g. "groq" (a configured cloud provider)
    # Local STT (faster-whisper): model size + compute type. int8 on CPU keeps
    # the resident footprint small enough to co-exist with Ollama.
    voice_stt_model: str = "base"
    voice_stt_compute_type: str = "int8"        # int8 | int8_float16 | float16 | float32
    # Local TTS (Piper): path to a downloaded .onnx voice (see voice/README).
    piper_voice_path: str = ""
    # Audio contract: inbound browser PCM16 @ voice_sample_rate; Piper emits
    # voice_tts_sample_rate (resampled server-side before it is sent back).
    voice_sample_rate: int = 16000
    voice_tts_sample_rate: int = 22050
    voice_max_seconds: int = 600
    # Voice latency / speed tuning (Phase 1 live call):
    #   * voice_vad_silence_ms — trailing silence before a user's utterance is
    #     finalised and sent to STT. 300ms keeps real-time feel vs. the 800ms
    #     conservative default; raise for noisy environments.
    #   * voice_fast_model — optional smaller Ollama/cloud model the live-voice
    #     path prefers for speed; empty = use the normal tier ladder.
    #   * voice_stream_tokens — when True, LLM tokens are pushed incrementally
    #     into TTS so the assistant speaks while still generating (overlapped
    #     speech). When False (the default for now, to keep the buffered path
    #     and the existing offline tests stable), the whole reply is buffered
    #     first. Flip to True once the streaming TTS sink is proven offline.
    voice_vad_silence_ms: int = 300
    voice_fast_model: str | None = None
    voice_stream_tokens: bool = False
    # Optional cloud STT providers (only used when selected/falling back).
    deepgram_api_key: str | None = None

    @property
    def voice_available(self) -> bool:
        return bool(self.voice_enabled)

    @property
    def twilio_comms_configured(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token)

    @property
    def telnyx_comms_configured(self) -> bool:
        """Telnyx is ready for voice/messaging once key + account + sender exist."""
        return bool(
            self.telnyx_api_key and self.telnyx_account_sid
            and self.telnyx_from_number
        )

    @property
    def phone_comms_configured(self) -> bool:
        """True when either telephony provider is configured."""
        return self.telnyx_comms_configured or self.twilio_comms_configured

    # The externally reachable base URL of the running app (no trailing path).
    # Single source for public URLs: the OAuth callback derives from it, so dev
    # (:5173 Vite) / served (:8000) / deployed only differ by this one value.
    public_base_url: str = "http://localhost:5173"
    # Browser destination after an OAuth flow or device pairing is completed.
    # Keep distinct from public_base_url, which must remain the backend origin
    # for provider callbacks.
    frontend_base_url: str | None = None

    # Phase E (§34-38, §76): Gmail OAuth2. The official sending address for
    # the app is the default; client id/secret are real credentials and must
    # only ever come from the environment.
    email_provider: str = "auto"  # auto | mock | gmail
    gmail_client_id: str | None = None
    gmail_client_secret: str | None = None
    # Optional override; when empty the callback URL is derived from
    # `public_base_url`. Whatever value is used MUST be registered byte-for-byte
    # as an "Authorized redirect URI" on the Google OAuth client.
    gmail_redirect_uri: str | None = None
    gmail_from: str = "jaloraai1318@gmail.com"  # official app address (public)
    gmail_push_token: str | None = None          # inbound webhook signature (set in prod)
    # Optional PEM CA bundle for environments with a managed TLS inspection root.
    # Empty means use the operating system trust store when available.
    gmail_ca_bundle: str | None = None

    # Phase N (§34-38): generic OAuth2 framework for personal-assistant
    # providers. A provider's Connect button lights up only when both its client
    # id and secret are present (env `JALORA_<PROVIDER>_CLIENT_ID/SECRET`);
    # otherwise the UI shows "Add credentials to connect". Google (Gmail,
    # Calendar, Drive) reuses the Gmail client id/secret above.
    slack_client_id: str | None = None
    slack_client_secret: str | None = None
    github_client_id: str | None = None
    github_client_secret: str | None = None
    notion_client_id: str | None = None
    notion_client_secret: str | None = None
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None
    dropbox_client_id: str | None = None
    dropbox_client_secret: str | None = None

    # Phase N (§34-38, §76): Payments & travel integrations.
    # Amadeus is env-keyed (developer client-credentials). In-chat live bus
    # inventory and booking require an authorized BuuPass partner integration.
    # Until then, bus requests hand off to BuuPass's public route pages.
    payments_provider: str = "auto"  # auto | mock | stripe
    stripe_client_id: str | None = None
    stripe_client_secret: str | None = None
    travel_provider: str = "auto"  # auto | mock | amadeus
    amadeus_client_id: str | None = None
    amadeus_client_secret: str | None = None
    amadeus_base_url: str = "https://test.api.amadeus.com"

    # Phase I (§63-68, §76): personal-assistant integrations. All are mock-first
    # and keyless-capable so the whole assistant works offline; a configured
    # account/key upgrades auto -> the real provider.
    calendar_provider: str = "auto"   # auto | mock | google
    drive_provider: str = "auto"      # auto | mock | google
    contacts_provider: str = "auto"   # auto | local | google
    maps_provider: str = "auto"       # auto | osm | mock
    weather_provider: str = "auto"    # auto | openmeteo | mock
    translate_provider: str = "auto"  # auto | mock | openai
    # Optional Google Maps API key (the Maps/Places adapter is optional; OSM is
    # the keyless default). Never hard-code it — environment only.
    google_maps_api_key: str | None = None
    # Nominal driving speed (km/h) used to estimate durations for the offline
    # maps mock; real OSRM returns its own duration.
    maps_mock_speed_kmh: float = 40.0
    # Fallback "City, Country" for weather when neither a request nor the user
    # profile supplies a location.
    default_location: str = ""

    # Features ---------------------------------------------------------------
    memory_enabled: bool = True
    memory_vectorstore: str = "auto"  # auto | sql | chroma
    chroma_dir: Path = BASE_DIR / "data" / "chroma"
    rate_limit_auth_per_min: int = 120  # Increased from 60 for faster API access
    # Phase L (§74): global per-IP rate limit for all /api/ routes (multi-worker
    # safe when redis_url is set). 0 disables the global middleware.
    rate_limit_global_per_min: int = 240  # Increased from 120 for higher throughput
    # Phase L (§74): session lifetime for refresh tokens (days)
    refresh_token_expire_days: int = 30
    agent_max_steps: int = 40
    agent_max_tool_calls: int = 40
    agent_max_retries: int = 5
    tool_timeout_seconds: float = 60.0
    # Task timeout (seconds). 0 = unlimited — tasks run to completion and are
    # only stopped by the user (stop button) or max_steps/max_tool_calls limits.
    # Previously 300.0 which killed long-running coding tasks mid-execution.
    task_timeout_seconds: float = 0.0

    # Phase J (§53-54): sandboxed coding workspace. Per-user space under
    # data/workspaces/<user_id>; commands are allowlisted (python, pytest,
    # read-only git) and run without a shell under a wall-clock/output cap.
    sandbox_enabled: bool = True
    sandbox_timeout_seconds: float = 60.0
    sandbox_max_output_chars: int = 20000
    sandbox_allow_pip: bool = False  # opt-in: allow `pip install` inside the sandbox
    workspaces_dir: Path = BASE_DIR / "data" / "workspaces"

    # Phase K (§27, §70-72): learning & preferences.
    learning_enabled: bool = True

    # Phase L (§58, §74/75): observability, Redis, and audit redaction.
    tracing_enabled: bool = False
    tracing_provider: str = "none"  # none | langsmith | otel
    langsmith_api_key: str | None = None
    otel_endpoint: str | None = None
    metrics_enabled: bool = True
    redis_url: str | None = None
    redact_audit_enabled: bool = True

    # Phase G (§49-51, §61-62): scheduling & reminders. `scheduler_enabled`
    # defaults on in dev; tests / ephemeral environments disable it so jobs do
    # not fire against transient databases. Trigger times are computed in UTC.
    scheduler_enabled: bool = True
    scheduler_timezone: str = "Africa/Nairobi"

    # Phase 0 (math/science): compute first, then explain. `math_explain`
    # enables the LLM explanation layer on top of the verified computation;
    # `math_level` selects the explanation depth (auto|primary|highschool|
    # university|stem|postgrad|professor); `math_timeout_seconds` bounds a CAS
    # solve; `plots_enabled` allows matplotlib plot artifacts; `math_show_working`
    # is the default for the "show working" toggle (False = answer only).
    math_explain: bool = True
    math_level: str = "auto"
    math_timeout_seconds: int = 15
    plots_enabled: bool = True
    math_show_working: bool = True

    # Conversation context (spec §26, §79): the newest `history_limit` messages
    # are loaded; when more than `summary_threshold` arrive, the older part is
    # summarised with one model call and only `summary_keep` stay verbatim.
    history_limit: int = 40
    summary_threshold: int = 32
    summary_keep: int = 12

    # Normalises empty-string env values to None for every secret/env-only field,
    # including the new HuggingFace token (huggingface_token) and Gmail OAuth
    # credentials added in the Creative Studio image-generation work.
    @field_validator("openai_api_key", "anthropic_api_key", "google_api_key",
                     "groq_api_key", "deepseek_api_key", "mistral_api_key",
                     "openrouter_api_key", "nvidia_api_key", "cohere_api_key",
                     "cerebras_api_key",
                     "perplexity_api_key", "poolside_api_key", "tavily_api_key",
                     "credentials_encryption_key", "gmail_client_id",
                     "gmail_client_secret", "gmail_redirect_uri",
                     "gmail_push_token", "huggingface_token",
                     "slack_client_id", "slack_client_secret",
                     "github_client_id", "github_client_secret",
                     "notion_client_id", "notion_client_secret",
                     "spotify_client_id", "spotify_client_secret",
                     "dropbox_client_id", "dropbox_client_secret",
                     "google_maps_api_key", "langsmith_api_key",
                     "redis_url", "deepgram_api_key", "voice_fallback_provider",
                     "twilio_account_sid", "twilio_auth_token",
                     "twilio_from_number", "twilio_whatsapp_from",
                     "telnyx_api_key", "telnyx_public_key", "telnyx_account_sid",
                     "telnyx_application_id", "telnyx_connection_id",
                     "telnyx_from_number", "telnyx_whatsapp_from",
                     "telnyx_messaging_profile_id",
                     "stripe_client_id", "stripe_client_secret",
                     "amadeus_client_id", "amadeus_client_secret",
                     mode="before")
    @classmethod
    def _empty_str_is_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def gmail_callback_url(self) -> str:
        """Effective OAuth redirect URI.

        An explicit ``JALORA_GMAIL_REDIRECT_URI`` override wins; otherwise it is
        derived from ``public_base_url``. Both the authorization request and the
        token exchange use this single value so they can never disagree.
        """
        if self.gmail_redirect_uri:
            return self.gmail_redirect_uri
        return f"{self.public_base_url.rstrip('/')}/api/integrations/gmail/callback"

    @property
    def oauth_callback_url(self) -> str:
        """Generic OAuth redirect URI for every non-Gmail provider.

        A single redirect target is reused across providers; register this exact
        URL on each provider's OAuth app.
        """
        return f"{self.public_base_url.rstrip('/')}/api/integrations/oauth/callback"

    @property
    def spa_base_url(self) -> str:
        """Return the browser app origin, keeping backend callback URLs separate."""
        if self.frontend_base_url:
            return self.frontend_base_url.rstrip("/")

        public_base = self.public_base_url.rstrip("/")
        if urlsplit(public_base).hostname in {"localhost", "127.0.0.1", "::1"}:
            return public_base
        return "https://jalora-ai.vercel.app"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"production", "prod"}

    # Provider registry: name -> (env key present?, model) -------------------
    def provider_status(self) -> dict[str, dict]:
        """Public, secret-free view of provider configuration."""
        checks: dict[str, tuple[bool, str]] = {
            "openai": (self.openai_api_key is not None, self.openai_model),
            "anthropic": (self.anthropic_api_key is not None, self.anthropic_model),
            "google": (self.google_api_key is not None, self.google_model),
            "groq": (self.groq_api_key is not None, self.groq_model),
            "deepseek": (self.deepseek_api_key is not None, self.deepseek_model),
            "mistral": (self.mistral_api_key is not None, self.mistral_model),
            "openrouter": (self.openrouter_api_key is not None, self.openrouter_model),
            "nvidia": (self.nvidia_api_key is not None, self.nvidia_model),
            "poolside": (self.poolside_api_key is not None, self.poolside_model),
            "lmstudio": (True, self.lmstudio_model),  # local, keyless
            "jan": (self.jan_base_url is not None, self.jan_model),  # user-configured URL
            "cohere": (self.cohere_api_key is not None, self.cohere_model),
            "cerebras": (self.cerebras_api_key is not None, self.cerebras_model),
            "perplexity": (self.perplexity_api_key is not None, self.perplexity_model),
            "ollama": (True, self.ollama_model),  # local, keyless (may be offline)
        }
        return {
            name: {"configured": configured, "model": model}
            for name, (configured, model) in checks.items()
        }

    def any_chat_provider_configured(self) -> bool:
        return any(
            v["configured"]
            for k, v in self.provider_status().items()
            if k not in ("ollama", "lmstudio", "jan")
        )


def validate_settings(settings: Settings) -> list[str]:
    """Return startup warnings; raise hard errors for unsafe production config."""
    warnings: list[str] = []

    if settings.is_production:
        if settings.jwt_secret == DEV_SECRET or len(settings.jwt_secret) < 32:
            raise RuntimeError(
                "JALORA_JWT_SECRET must be set to a strong random value in production."
            )
        encryption_key = settings.credentials_encryption_key
        if not encryption_key:
            raise RuntimeError(
                "JALORA_CREDENTIALS_ENCRYPTION_KEY must be set to a dedicated "
                "Fernet key in production."
            )
        if encryption_key == settings.jwt_secret:
            raise RuntimeError(
                "JALORA_CREDENTIALS_ENCRYPTION_KEY must be separate from "
                "JALORA_JWT_SECRET."
            )
        try:
            from cryptography.fernet import Fernet

            Fernet(encryption_key.encode("ascii"))
        except (UnicodeEncodeError, ValueError) as exc:
            raise RuntimeError(
                "JALORA_CREDENTIALS_ENCRYPTION_KEY must be a valid Fernet key."
            ) from exc
        if not settings.any_chat_provider_configured():
            warnings.append("No AI provider API key configured; chat will be unavailable.")
    else:
        if settings.jwt_secret == DEV_SECRET:
            warnings.append("Using development JWT secret; set JALORA_JWT_SECRET before deploying.")

    if not settings.any_chat_provider_configured():
        warnings.append(
            "No cloud AI provider configured. Configure at least one provider key, "
            "or ensure LM Studio or Ollama is running for local models."
        )

    if (settings.image_provider == "auto"
            and not settings.openai_api_key
            and not settings.google_api_key):
        warnings.append(
            "No image-generation provider configured. The local engine will fall back "
            "to procedural abstract art. Install torch+diffusers and set "
            "JALORA_IMAGE_LOCAL_MODEL for real AI image generation, or configure "
            "an OpenAI/Google API key."
        )

    if settings.gmail_client_id and settings.gmail_client_secret:
        callback = settings.gmail_callback_url
        logger.info("Gmail OAuth redirect URI: %s", callback)
        if settings.is_production and ("localhost" in callback or "127.0.0.1" in callback):
            warnings.append(
                "Gmail OAuth redirect URI points to localhost in production "
                f"({callback}); set JALORA_PUBLIC_BASE_URL (or "
                "JALORA_GMAIL_REDIRECT_URI) to the public URL and register it "
                "as an Authorized redirect URI."
            )

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)
    settings.workspaces_dir.mkdir(parents=True, exist_ok=True)

    for w in warnings:
        logger.warning("startup: %s", w)
    return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
