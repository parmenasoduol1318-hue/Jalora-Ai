# Jalora AI

Production-grade multimodal, memory-enabled, tool-using AI assistant.

**FastAPI + LangGraph + LangChain + React/TypeScript frontend.** The backend serves
the production build from `frontend-react/dist`; use Vite for local frontend work.

---

## Status (honest scope)

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Foundation: config, errors, database, auth, activity log | Implemented; validate with the current test suite |
| 2 | AI core: model router, agent graph, tools, chat/streaming, memory, documents, approvals | Implemented for the central chat path; shared lifecycle across voice, saved workflows, and legacy task execution remains incomplete |
| 3 | Migrations, observability, task history, approvals, and provider integrations | Partially implemented; provider behavior depends on configured services |
| 4 | Multimodal input and media tools | Partial; engines depend on local packages or configured providers |
| 5 | Communications, documents, browser, calendar, and device capabilities | Partial; individual tools and APIs exist, but capability coverage and shared execution vary |
| 6 | Native remote Windows/Android companions and broad app automation | Not implemented; browser pairing is not remote desktop access |

Do not rely on older test counts or pass claims; run the commands below to
establish current results.

---

## Quickstart (local, no Docker)

```powershell
cd jalora
python -m venv .venv
.\.venv\Scripts\pip install -r backend\requirements.txt

# configure (development) — config.py reads .env from the jalora\ folder
Copy-Item backend\.env.example .\.env   # then edit: set JALORA_JWT_SECRET

cd backend
$env:JALORA_JWT_SECRET='change-me'           # or rely on .env / environment
..\.venv\Scripts\python -m uvicorn app.main:app --reload
```

Build the React UI before serving it from FastAPI:

```powershell
Push-Location frontend-react
npm ci
npm run build
Pop-Location
```

Then open http://127.0.0.1:8000/. For frontend development, run `npm run dev`
from `frontend-react/`; Vite proxies `/api` requests to FastAPI on port 8000.

Without any provider key the app still boots and tells you so honestly; configure
**at least one** of:

- `JALORA_OPENAI_API_KEY`, `JALORA_ANTHROPIC_API_KEY`, `JALORA_GOOGLE_API_KEY`,
  `JALORA_GROQ_API_KEY`, `JALORA_DEEPSEEK_API_KEY`, `JALORA_MISTRAL_API_KEY`,
  `JALORA_OPENROUTER_API_KEY`
- or run local models: `ollama serve` (defaults: `qwen2.5:7b`)

## Live voice calls (Phase 1)

A real-time, two-way spoken conversation with the assistant, entirely in-app — no
phone number required. The browser streams microphone PCM16 (16 kHz) up a
WebSocket (`/api/voice/live`, JWT via `?token=`) and plays synthesised PCM16 back
as it arrives. Language understanding, tools and TTS all run server-side.

- **Local-first**: faster-whisper (STT) and Piper (TTS); the LLM route is
  Ollama-first with automatic cross-provider fallback when a turn exceeds
  `JALORA_VOICE_LATENCY_BUDGET_MS`.
- **Tools mid-call**: a small voice tool set (search, weather, calendar,
  messaging, contacts). Approval-gated actions (e.g. sending an SMS) ask for a
  spoken yes/no before running.
- **Graceful degradation**: every provider chain ends in a deterministic mock, so
  the pipeline runs and is testable offline even with no engines installed.
- **Uniform records**: in-app calls persist as `Call` rows with `kind="inapp"`
  (nullable `to_phone`), so transcripts and the owner summary appear alongside
  phone calls in the Tasks view.

Enable/disable with `JALORA_VOICE_ENABLED`; see `backend/.env.example` for the
provider chains, model path and sample rates. Install the optional extras with
`pip install -r backend/requirements.txt` (faster-whisper, Piper, Pipecat).


## Phone calls (Phase 2/3)

The same live voice engine also runs over the telephone via **Telnyx Media
Streams** (primary) or **Twilio Media Streams** (fallback). The provider streams
8 kHz μ-law audio to `/api/calls/{id}/media` (authorised by a short-lived signed
`?token=`; Telnyx adds `&provider=telnyx`); the bridge decodes it with Pipecat's
`TelnyxFrameSerializer`/`TwilioFrameSerializer`, feeds the `VoiceSession`, and
encodes the assistant's reply back to μ-law. The `Call.mode` column selects the
transport per call:

- **Outbound** — `POST /api/calls` with `"mode": "live"` places a call, then
  answers with TeXML/TwiML `<Start><Stream>` (Telnyx) or `<Connect><Stream>`
  (Twilio) so audio flows to the AI instead of the scripted `<Gather>` legs
  (`"mode": "script"`, the default).
- **Inbound** — point a number's voice webhook at `POST /api/calls/inbound`. The
  provider is detected from the signature header (`telnyx-signature-ed25519` vs
  `X-Twilio-Signature`). When `JALORA_VOICE_INBOUND_ENABLED=true` and
  `JALORA_VOICE_INBOUND_EMAIL` names a registered user, the assistant answers on
  that user's behalf, converses freely, takes notes and writes the usual summary;
  otherwise the call is declined. Signatures are verified whenever the matching
  key/token is configured.
- **Barge-in**: the assistant stops speaking (`clear`) when it detects the other
  party talking; DTMF `1`/`2` answer an approval prompt yes/no.

Messaging (SMS/WhatsApp) inbound webhooks are `POST /api/comms/telnyx/inbound`
(JSON `message.received`, Ed25519-signed) and `POST /api/comms/twilio/inbound`
(form-encoded, TwiML reply); both run one agent turn and reply on the same
channel.

Telephony needs the `JALORA_TELNYX_*` credentials (or `JALORA_TWILIO_ACCOUNT_SID`
/`_AUTH_TOKEN`) plus a public `JALORA_VOICE_PUBLIC_URL` (ngrok/tunnel in dev). All
of the above runs against the offline mock/loopback in tests — no provider
account required. See `backend/.env.example` for every `JALORA_VOICE_*`/
`JALORA_TELNYX_*` setting.


## Quickstart (Docker)

```bash
cd jalora
export JALORA_JWT_SECRET=$(openssl rand -hex 32)
docker compose up --build
```

Open http://localhost:8000/. State persists in named volumes. For Postgres set
`JALORA_DATABASE_URL=postgresql+psycopg://user:pass@db:5432/jalora` and
`pip install "psycopg[binary]"` (see `docker-compose.yml` comments).

## Tests

```powershell
cd jalora\backend
..\.venv\Scripts\python.exe -m pytest tests -q
```

Tests bootstrap their own temp database and a scripted fake model — no network,
no API keys needed.

---

## Architecture

```
Browser (frontend-react/) ── SSE / JSON ──► FastAPI (backend/app)
                                          │
                          ┌───────────────┼──────────────────────────┐
                          ▼               ▼                          ▼
                   Auth (JWT+Argon2)   LangGraph supervisor      REST API
                                          │  agent → tools → …     │
                          ┌───────────────┼──────────────┐          │
                          ▼               ▼              ▼          ▼
                    ModelRouter      ToolRegistry    Memory/Docs  SQLite/Postgres
                    (OpenAI-compat   (permission     (embeddings, (SQLAlchemy)
                     + Anthropic)     levels 1-4)     retrieval)
```

- **Model router** (`services/model_router.py`) — OpenAI-compatible HTTP adapter
  for 7+ providers plus a native Anthropic streaming adapter; complexity-based
  auto-routing, per-request overrides, honest healthchecks. Every provider emits
  the same `token`/`usage` events, so streaming is provider-agnostic.
- **Agent graph** (`agents/graph.py`) — LangGraph supervisor: bound tools loop,
  max-steps wrap-up, STOP handling, and **approval pause/resume** (Level 3/4
  tools persist graph state and wait for an explicit approve/reject, resumed with
  `entry="tools"`).
- **Tools** (`tools/`) — structured `ToolResult {success, message, data, error}`
  envelope for every tool; permission levels AUTO / NOTIFY / CONFIRM / STRICT;
  registry metadata discovers task-relevant capabilities and the model selects
  only within that discovered scope. Multi-step actions require a validated plan
  before any task capability can execute; tool-backed steps must match the plan
  and satisfy its dependencies. A prose-only final step uses `kind="response"`
  and is marked complete only after the final answer is produced.
- **Saved workflows** — execute registered tools with schema validation,
  per-user autonomy/tool-policy checks, actual structured results, and persisted
  step status. CONFIRM/STRICT steps stop before execution and must be run through
  the assistant's explicit approval flow; workflow execution never fabricates
  simulated tool results.
- **Memory** — sensitive-content filter, local-hash (or OpenAI/Ollama) embeddings,
  cosine search, injected into the system prompt as context.
- **Documents** — PDF/text extract → chunk → embed → query endpoint (Phase 2
  baseline RAG).
- **Frontend** — React/TypeScript/Vite application in `frontend-react/`, with
  streaming chat, tool status, approvals, and task/device interfaces. Build with
  `npm run build`; the production assets are served by FastAPI.
- **Security** — pydantic-settings env config, startup validation, dev-secret
  warnings / prod hard-fail, secret redaction in audit logs, structured errors
  only (no tracebacks to clients), CORS allowlist, auth rate limiting.

## API surface (representative routes)

`/api/auth/*` (register, login, me) · `/api/chat` (JSON or SSE stream) ·
`/api/conversations/*` · `/api/memory/*` · `/api/documents/*` ·
`/api/approvals/*` + `/api/runs` + `/api/agents/stop` · `/api/activity` ·
`/api/health`, `/api/providers*`, `/api/tools`

Interactive docs: http://127.0.0.1:8000/docs

## Project layout

```
jalora/
├── backend/
│   ├── app/
│   │   ├── api/          # routers (auth, chat, memory, documents, runs, …)
│   │   ├── agents/       # LangGraph supervisor + prompts
│   │   ├── auth/         # argon2 hashing, JWT
│   │   ├── database/     # engine/session
│   │   ├── models/       # SQLAlchemy ORM
│   │   ├── schemas/      # Pydantic contracts
│   │   ├── services/     # router, chat, memory, docs, audit, embeddings
│   │   ├── tools/        # registry + built-in tools
│   │   ├── config.py     # settings + startup validation
│   │   └── main.py       # app factory, static frontend
│   ├── tests/            # pytest suite (offline where supported)
│   ├── requirements.txt
│   └── .env.example
├── frontend-react/       # React, TypeScript, Vite, Playwright
├── plans/                # architecture and implementation plans
├── Dockerfile
├── docker-compose.yml
└── README.md
```
