# Jalora Deployment Reference

## What is on GitHub

The public repository's `main` branch points to
[`f232eb1`](https://github.com/parmenasoduol1318-hue/Jalora-Ai/commit/f232eb118715e18f7e08cd29d4c9ce63ab432a08).
I compared the local non-ignored Git snapshot with `origin/main`; they matched
at the time this guide was prepared. The local checkout is on the separate
`jalora-optimization` branch, so Git may show many files as changed or
untracked relative to that branch even though their contents are in the
published snapshot. Ignored local files and runtime data are not part of this
comparison.

The root `.env` is intentionally private and is not part of the published
snapshot. Put production secrets in your hosting provider's secret manager.

This guide was added after the initial publish; the deployment follow-up
includes it with the Vercel configuration and frontend lockfile.

## Project root and deployable files

```text
jalora/
├── backend/
│   ├── app/                 FastAPI application, APIs, agents, services and tools
│   ├── alembic/             Database schema migrations
│   ├── tests/               Backend tests
│   ├── requirements.txt     Python dependencies
│   └── .env.example         Configuration names and development defaults
├── frontend-react/          React/Vite application; Docker builds its static UI
├── scripts/                 Database and credential maintenance scripts
├── docs/                    Deployment, operations and project documentation
├── data/                    Local runtime data; production data must be persisted
├── Dockerfile               Builds the frontend and backend container
├── docker-compose.yml       Local single-host app + Redis deployment
├── .dockerignore            Excludes local and secret files from image builds
└── .gitignore               Excludes local secrets and generated files from Git
```

The checkout also contains `app/`, `backups/`, `config/`, `frontend/`, `jalora/`,
`lora_adapters/`, `plans/`, `reports/`, `screenshots/`, `trainer/`,
`training results/`, and `training_data/`. These are repository/workspace
directories; the current `Dockerfile` builds from `backend/`,
`frontend-react/`, and two maintenance scripts under `scripts/`. Do not assume
the other root directories are present inside the container.

The Dockerfile and Compose file describe an alternative single-host Docker
deployment. For the Render + Vercel setup below, Render runs the backend and
Vercel builds/hosts the frontend separately; use persistent backend storage for
the database and generated files.

## Render: FastAPI backend

Create a **Web Service** in Render connected to this GitHub repository, then set:

| Render setting | Value |
|---|---|
| Runtime | Python 3 |
| Root Directory | `backend` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health Check Path | `/api/health` |

Render supplies `PORT`; do not hard-code it. After the first deploy, note the
backend URL, for example `https://jalora-api.onrender.com`. Replace the example
URLs below with your actual Render and Vercel project domains.

Add these in **Render → Environment** (never commit them):

```powershell
# Generate locally; put each output into its matching Render secret variable.
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

| Render environment variable | Value |
|---|---|
| `JALORA_ENV` | `production` |
| `JALORA_JWT_SECRET` | First command's output; at least 32 characters |
| `JALORA_CREDENTIALS_ENCRYPTION_KEY` | Second command's output; valid Fernet key, different from JWT secret |
| `JALORA_CORS_ORIGINS` | Your exact Vercel origin, e.g. `https://jalora.vercel.app` (no path or trailing slash) |
| `JALORA_PUBLIC_BASE_URL` | Your public **Render backend** URL, e.g. `https://jalora-api.onrender.com` |
| One model-provider key | For example `JALORA_OPENAI_API_KEY` or `JALORA_ANTHROPIC_API_KEY` |

The Vercel URL belongs in CORS because it is the browser origin. The Render URL
belongs in `JALORA_PUBLIC_BASE_URL` because OAuth callbacks and server webhooks
must reach the backend. Register the callback URLs derived from the Render URL
with OAuth providers. If you add a custom domain, use its exact HTTPS origin(s)
instead.

### Render persistent data

Render's local filesystem is not persistent across deploys unless you attach a
persistent disk. Add a disk mounted at `/var/data`, then set:

```text
JALORA_DATA_DIR=/var/data
JALORA_UPLOAD_DIR=/var/data/uploads
JALORA_OUTPUTS_DIR=/var/data/outputs
JALORA_CREATIVE_DIR=/var/data/uploads
JALORA_DATABASE_URL=sqlite:////var/data/jalora.db
```

Keep a single backend instance when using SQLite and provision a disk plan that
supports persistent storage. Without the disk and these paths, database records
and generated/uploaded files can be lost when Render replaces the instance.
Arrange backups separately.

Also configure **at least one** model provider on Render:

| Provider | Render variable |
|---|---|
| OpenAI | `JALORA_OPENAI_API_KEY` |
| Anthropic | `JALORA_ANTHROPIC_API_KEY` |
| Google | `JALORA_GOOGLE_API_KEY` |
| Groq | `JALORA_GROQ_API_KEY` |
| DeepSeek | `JALORA_DEEPSEEK_API_KEY` |
| Mistral | `JALORA_MISTRAL_API_KEY` |
| OpenRouter | `JALORA_OPENROUTER_API_KEY` |
| Ollama | `JALORA_OLLAMA_BASE_URL`, `JALORA_OLLAMA_MODEL` (Ollama must be publicly/reliably reachable from Render) |

Add only keys for providers you actually use. A local Ollama URL such as
`http://localhost:11434` will not reach Ollama on your personal computer from
Render.

## Vercel: React frontend

Create a Vercel project from the same GitHub repository and set:

| Vercel setting | Value |
|---|---|
| Framework Preset | Vite |
| Root Directory | `frontend-react` |
| Install Command | `npm ci` |
| Build Command | `npm run build` |
| Output Directory | `dist` |
| Development Command | Leave default/blank; not used for production builds |

The production manifest and matching npm lockfile are both in
`frontend-react/`. Use `frontend-react/package-lock.json` with
`frontend-react/package.json`; do not use the legacy `frontend/package-lock.json`
file (that folder has no production `package.json`). The
[`frontend-react/vercel.json`](../frontend-react/vercel.json) repeats the npm
install/build/output settings for Vercel. Keep the Vercel Root Directory set to
`frontend-react`.

The current frontend has **no required `VITE_*` environment variables**. Its API
client calls relative paths such as `/api/auth/login` and its live voice socket
uses the current frontend hostname. Do not add a made-up `VITE_API_URL`; the
current code does not read one.

Therefore, there are no Jalora environment variables to add in **Vercel →
Settings → Environment Variables** for the current frontend. Backend secrets
and provider keys belong only in Render.

The `npm warn Unknown env config "cache-dir"` message is unrelated to the
missing-lockfile failure. There is no project `.npmrc` setting for `cache-dir`
in this repository. If that warning persists in Vercel, check its project
environment variables for an obsolete `npm_config_cache_dir` setting and
remove it; do not replace the required `npm ci` command.

Because the browser is on Vercel and the API is on Render, configure Vercel to
proxy `/api/*` to the Render service. Put a `vercel.json` in the Vercel project
root (`frontend-react/vercel.json`) with your actual Render hostname:

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "rewrites": [
    {
      "source": "/api/:path*",
      "destination": "https://jalora-api.onrender.com/api/:path*"
    },
    {
      "source": "/(.*)",
      "destination": "/index.html"
    }
  ]
}
```

The second rewrite supports client-side routes. Replace `jalora-api` with the
actual Render service name. Commit this file and redeploy Vercel. The Vercel
frontend domain should be added to Render's `JALORA_CORS_ORIGINS`.

**Live voice needs separate verification:** the current voice WebSocket URL is
constructed from the Vercel hostname, while the backend runs on Render. Do not
assume an HTTP rewrite also proxies WebSocket upgrades. If live voice is needed,
use a WebSocket-capable proxy/custom domain or update the frontend to use a
configurable backend WebSocket origin, then test a real call after deployment.

After both services deploy, check the backend directly and through the Vercel
proxy:

```powershell
curl.exe --fail https://jalora-api.onrender.com/api/health
curl.exe --fail https://jalora.vercel.app/api/health
```

Replace both hostnames with your actual service domains. Then test browser
registration/login, one real model request, file upload/download, and any
enabled OAuth callback. These checks are required because a successful static
frontend build alone does not confirm the backend proxy or environment values.

## Optional Render environment variables

- **Calls/webhooks:** set `JALORA_CALLS_BASE_URL` and
  `JALORA_VOICE_PUBLIC_URL` to the public Render backend URL when enabling
  Telnyx/Twilio; add the relevant `JALORA_TELNYX_*` or `JALORA_TWILIO_*`
  credentials.
- **Gmail/OAuth:** set `JALORA_GMAIL_CLIENT_ID`, `JALORA_GMAIL_CLIENT_SECRET`,
  `JALORA_GMAIL_PUSH_TOKEN`, and other relevant integration credentials in
  Render. Register callbacks at the Render backend URL.
- **Other provider integrations:** set only the needed
  `JALORA_SLACK_*`, `JALORA_GITHUB_*`, `JALORA_NOTION_*`, `JALORA_SPOTIFY_*`,
  `JALORA_DROPBOX_*`, `JALORA_STRIPE_*`, or `JALORA_AMADEUS_*` variables.
- **Redis/tracing/features:** add `JALORA_REDIS_URL`,
  `JALORA_TRACING_*`, voice, image, maps, scheduler, and rate-limit settings
  only when required. The exact supported names/defaults are listed in
  [`backend/.env.example`](../backend/.env.example); put backend variables in
  Render, not Vercel.

For the full set of optional provider and feature variables and their defaults,
see [`backend/.env.example`](../backend/.env.example). Add only the variables
for services you enable, and put all backend settings in Render.

## Before exposing the app publicly

1. Deploy the Render Web Service with the build/start commands above.
2. Add the Render persistent disk and required environment variables.
3. Configure Vercel's `frontend-react` root, build/output settings, and API rewrite.
4. Configure production-only JWT/Fernet secrets, HTTPS URLs, CORS, and one model provider.
5. Test the API health check and user flows through Vercel; confirm backup/restore.
6. Review [`OPERATIONS-RUNBOOK.md`](./OPERATIONS-RUNBOOK.md) and
   [`PRODUCTION_READINESS_ASSESSMENT.md`](./PRODUCTION_READINESS_ASSESSMENT.md).

This reference describes deployment configuration; it is not a production
certification. Provider acceptance, database operations, backups, security
review, and production-load testing remain the operator's responsibility.
