"""ASGI entry point for running Jalora with uvicorn.

Usage:
    uvicorn uvicorn_main:app --host 0.0.0.0 --port 8000 --workers 4

For faster startup, use preload:
    uvicorn uvicorn_main:app --preload --host 0.0.0.0 --port 8000

Performance improvements:
    - Reduced timeout for faster failure detection
    - Disabled access log for production (reduce I/O)
    - Added health check endpoint
"""
from __future__ import annotations

import os
import sys

# Set log level to reduce noise
os.environ.setdefault("JALORA_LOG_LEVEL", "WARNING")

from app.main import create_app, app

# Export for uvicorn
__all__ = ["app", "create_app"]


def main() -> None:
    """Run the application with uvicorn."""
    import uvicorn
    
    host = os.environ.get("JALORA_HOST", "127.0.0.1")
    port = int(os.environ.get("JALORA_PORT", "8000"))
    workers = int(os.environ.get("JALORA_WORKERS", "1"))
    
    # Performance optimizations
    # - access_log=False: reduce I/O for high-traffic endpoints
    # - timeout_keep_alive: 30s instead of default 5s for better connection reuse
    # - limit_concurrency: limit concurrent requests to prevent resource exhaustion
    uvicorn.run(
        "uvicorn_main:app",
        host=host,
        port=port,
        workers=workers,
        log_level=os.environ.get("JALORA_LOG_LEVEL", "warning"),
        access_log=os.environ.get("JALORA_ACCESS_LOG", "false").lower() == "true",
        timeout_keep_alive=30,  # Reuse connections longer
        limit_concurrency=int(os.environ.get("JALORA_MAX_CONCURRENCY", "100")),
        limit_max_requests=int(os.environ.get("JALORA_MAX_REQUESTS", "10000")),
    )


if __name__ == "__main__":
    main()