@echo off
setlocal
cd /d "%~dp0"

set "JALORA_ENV=test"
for /f %%S in ('python -c "import secrets; print(secrets.token_urlsafe(32))"') do set "JALORA_JWT_SECRET=%%S"
set "JALORA_DATABASE_URL=sqlite:///./dev.db"
set "JALORA_DEFAULT_PROVIDER=ollama"
set "JALORA_LOCAL_PROVIDER_PREFERENCE=ollama"
set "JALORA_OLLAMA_MODEL=qwen2.5:0.5b"
set "JALORA_OLLAMA_MID_MODEL=qwen2.5:0.5b"
set "JALORA_OLLAMA_LIGHT_MODEL=qwen2.5:0.5b"
set "JALORA_RATE_LIMIT_AUTH_PER_MIN=1000"
set "JALORA_RATE_LIMIT_GLOBAL_PER_MIN=0"
set "JALORA_EMBEDDING_PROVIDER=local"
set "JALORA_MEMORY_VECTORSTORE=sql"
set "JALORA_BROWSER_DRIVER=auto"
set "JALORA_TRACING_ENABLED=false"
set "PYTHONPATH=%CD%"

python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8011
