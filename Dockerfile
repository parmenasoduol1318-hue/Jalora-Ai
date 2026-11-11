# ── Stage 1: Build the React frontend (Vite) ────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /src
COPY frontend-react/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend-react/ .
RUN npx vite build

# ── Stage 2: Build the Python backend ──────────────────────────────────────
FROM python:3.12-slim AS backend-base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install system deps needed for cryptography + psycopg2 (optional)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy backend
COPY backend/ /app/backend/
COPY scripts/sqlite_database.py scripts/rotate_credential_key.py /app/scripts/

# Copy built frontend into the expected location
COPY --from=frontend-builder /src/dist /app/frontend-react/dist

WORKDIR /app/backend

# Create a non-root user for security (Phase L hardening)
RUN groupadd -r -g 10001 jalora \
    && useradd  -r -u 10001 -g jalora -m -d /home/jalora jalora \
    && mkdir -p /app/data /app/logs \
    && chown -R jalora:jalora /app /home/jalora

USER jalora

EXPOSE 8000

# Healthcheck: hit the health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
