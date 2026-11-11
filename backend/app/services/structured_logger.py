"""Structured JSON logging for Jalora observability (Phase L)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable

# Default request ID for non-web operations
_global_request_id: str | None = None


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging."""
    
    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": _global_request_id,
        }
        
        # Add exception info if present
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        
        # Add extra fields
        for key, value in record.__dict__.items():
            if key not in ("name", "msg", "args", "levelname", "levelno", 
                          "pathname", "filename", "module", "lineno", 
                          "funcName", "created", "msecs", "relativeCreated",
                          "thread", "threadName", "processName", "process",
                          "getMessage", "exc_info", "exc_text", "stack_info",
                          "timestamp", "level", "message", "request_id"):
                log_obj[key] = value
        
        return json.dumps(log_obj, default=str, separators=(",", ":"))


def setup_structured_logging(level: str = "INFO") -> None:
    """Configure structured JSON logging for the application."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    
    # Set up root logger
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    # Configure uvicorn loggers to use JSON format
    for uvicorn_logger in ["uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"]:
        uvicorn_logger_obj = logging.getLogger(uvicorn_logger)
        uvicorn_logger_obj.handlers.clear()
        uvicorn_logger_obj.addHandler(handler)
        uvicorn_logger_obj.setLevel(logging.INFO)


def set_request_id(request_id: str | None = None) -> str:
    """Set the global request ID for logging context."""
    global _global_request_id
    _global_request_id = request_id or str(uuid.uuid4())
    return _global_request_id


def get_request_id() -> str | None:
    """Get the current request ID."""
    return _global_request_id


def clear_request_id() -> None:
    """Clear the request ID (for request cleanup)."""
    global _global_request_id
    _global_request_id = None


class RequestLogger:
    """Context manager for logging requests with timing and metrics."""
    
    def __init__(
        self,
        request_id: str | None = None,
        feature: str = "unknown",
        model: str | None = None,
        prompt_template: str | None = None,
    ):
        self.request_id = request_id or str(uuid.uuid4())
        self.feature = feature
        self.model = model
        self.prompt_template = prompt_template
        self.start_time: float | None = None
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.retrieval_results: list[dict] | None = None
        self.tool_calls: list[dict] | None = None
        self.latency: float = 0
        self.error: str | None = None
        self.gpu_vram_mb: float | None = None

    def __enter__(self):
        set_request_id(self.request_id)
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.latency = (time.perf_counter() - self.start_time) * 1000 if self.start_time else 0
        if exc_val:
            self.error = str(exc_val)
        clear_request_id()
        
        # Log the structured event
        log_data = {
            "request_id": self.request_id,
            "feature": self.feature,
            "model": self.model,
            "prompt_template": self.prompt_template,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency,
            "error": self.error,
            "gpu_vram_mb": self.gpu_vram_mb,
        }
        
        logger = logging.getLogger("jalora.request")
        logger.info(
            "Request completed",
            extra=log_data
        )


def get_gpu_memory_info() -> dict[str, Any]:
    """Get GPU memory info if CUDA is available."""
    try:
        import torch
        if torch.cuda.is_available():
            return {
                "cuda_available": True,
                "device_count": torch.cuda.device_count(),
                "device_name": torch.cuda.get_device_name(0),
                "vram_total_mb": torch.cuda.get_device_properties(0).total_memory / (1024 * 1024),
            }
    except Exception:
        pass
    
    return {"cuda_available": False}


def get_system_metrics() -> dict[str, Any]:
    """Get current system metrics (RAM, CPU, disk)."""
    try:
        import psutil
        mem = psutil.virtual_memory()
        disk_free = shutil.disk_usage("C:\\").free if os.name == "nt" else shutil.disk_usage("/").free
        return {
            "ram_total_gb": mem.total / (1024**3),
            "ram_available_gb": mem.available / (1024**3),
            "ram_percent": mem.percent,
            "cpu_count": psutil.cpu_count(),
            "disk_free_gb": disk_free / (1024**3),
        }
    except ImportError:
        return {}


def with_request_logging(
    feature: str,
    model_arg: str | None = None,
    prompt_template_arg: str | None = None,
):
    """Decorator for automatic request logging."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            request_id = kwargs.get("request_id") or str(uuid.uuid4())
            with RequestLogger(
                request_id=request_id,
                feature=feature,
                model=model_arg or (getattr(args[0], "model", None) if args else None),
                prompt_template=prompt_template_arg,
            ):
                return await func(*args, **kwargs)
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            request_id = kwargs.get("request_id") or str(uuid.uuid4())
            with RequestLogger(
                request_id=request_id,
                feature=feature,
                model=model_arg or (getattr(args[0], "model", None) if args else None),
                prompt_template=prompt_template_arg,
            ):
                return func(*args, **kwargs)
        
        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
    return decorator