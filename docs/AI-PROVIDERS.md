# AI provider setup

Jalora supports provider keys configured server-wide in Render or separately by
each user in **Settings → AI providers**. User keys are encrypted before storage
and are never returned by the API.

| Provider | API endpoint | Default model |
|---|---|---|
| Google AI Studio | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-3.5-flash-lite` |
| Groq | `https://api.groq.com/openai/v1` | `openai/gpt-oss-20b` |
| Cerebras | `https://api.cerebras.ai/v1` | `qwen-3.8-27b` |
| OpenRouter | `https://openrouter.ai/api/v1` | `openrouter/free` |
| NVIDIA NIM | `https://integrate.api.nvidia.com/v1` | `nvidia/nemotron-3.5-lightning-30b-a3b` |
| Mistral | `https://api.mistral.ai/v1` | `mistral-small-latest` |

For server-wide configuration, set the corresponding `JALORA_*_API_KEY` and
optional `JALORA_*_MODEL` variables in Render. The names and model defaults are
listed in [`backend/.env.example`](../backend/.env.example). Keep secret values
in Render's environment settings; do not commit them. OpenRouter's `openrouter/free`
router selects from the free models currently available to the account.

## Complex-reasoning models

Requests classified as complex use these models for the matching provider.
Simple requests continue to use the configured default model, and an explicit
model choice takes precedence.

| Provider | API endpoint | Complex-reasoning model |
|---|---|---|
| Groq | `https://api.groq.com/openai/v1` | `openai/gpt-oss-120b` |
| Google AI Studio | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-3.8-flash` |
| OpenRouter | `https://openrouter.ai/api/v1` | `nvidia/nemotron-3-super-vl:free` |
| Mistral | `https://api.mistral.ai/v1` | `mistral-large-latest` |
| NVIDIA NIM | `https://integrate.api.nvidia.com/v1` | `mistralai/mistral-large-4` |
| Cerebras | `https://api.cerebras.ai/v1` | `gpt-oss-120b` |

Model availability and access requirements are controlled by each provider and
may change independently of Jalora.
