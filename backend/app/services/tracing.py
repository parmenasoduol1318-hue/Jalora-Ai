"""Optional observability tracing setup (Phase L, sec 58).

LangSmith and OpenTelemetry are optional.  When ``JALORA_TRACING_ENABLED``
is ``False`` (the default, and the test-mode setting) this module is a no-op
so that tests never touch real provider credentials or network endpoints.

Tracing providers:
  - ``langsmith``  → init a langsmith ``Client`` (auto-instruments OpenAI/Anthropic)
  - ``otel``       → install an OpenTelemetry ``TracerProvider`` with an optional
                     OTLP gRPC span exporter when ``JALORA_OTEL_ENDPOINT`` is set
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger("jalora.tracing")

_TRACING_INITIALISED = False


def init_tracing(settings: "Settings") -> None:
    """Initialise tracing exactly once.

    No-op when ``settings.tracing_enabled`` is False.
    Safe to call multiple times.
    """
    global _TRACING_INITIALISED
    if _TRACING_INITIALISED:
        return
    if not settings.tracing_enabled:
        logger.debug("tracing disabled — skipping init")
        return

    provider = (settings.tracing_provider or "none").strip().lower()
    if provider == "langsmith":
        _init_langsmith(settings)
    elif provider == "otel":
        _init_otel(settings)
    else:
        logger.warning(
            "tracing_enabled is True but tracing_provider=%r is unrecognised "
            "(expected 'langsmith' or 'otel'); tracing stays disabled",
            provider,
        )
        return

    _TRACING_INITIALISED = True


def _init_langsmith(settings: "Settings") -> None:
    """Configure LangSmith tracing via the environment."""
    api_key = settings.langsmith_api_key
    if api_key:
        os.environ.setdefault("LANGSMITH_API_KEY", api_key)
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    try:
        from langsmith import Client  # type: ignore[import]

        if api_key:
            Client(api_key=api_key)
        logger.info("LangSmith tracing initialised")
    except Exception as exc:  # pragma: no cover — best-effort
        logger.warning("LangSmith init failed (tracing disabled): %s", exc)


def _init_otel(settings: "Settings") -> None:
    """Configure OpenTelemetry tracing with an optional OTLP exporter."""
    try:
        from opentelemetry import trace  # type: ignore[import]
        from opentelemetry.sdk.resources import Resource  # type: ignore[import]
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import]
        from opentelemetry.sdk.trace.export import (  # type: ignore[import]
            BatchSpanProcessor,
        )
    except ImportError:
        logger.warning("opentelemetry-sdk not installed — tracing disabled")
        return

    resource = Resource.create({"service.name": "jalora"})
    provider = TracerProvider(resource=resource)

    if settings.otel_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore[import]
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(endpoint=settings.otel_endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except Exception as exc:  # pragma: no cover
            logger.warning("OTLP exporter init failed: %s", exc)

    trace.set_tracer_provider(provider)
    logger.info("OpenTelemetry tracing initialised")


def get_tracer(name: str):
    """Return an OpenTelemetry tracer, or None if tracing is unavailable."""
    try:
        from opentelemetry import trace  # type: ignore[import]

        return trace.get_tracer(name)
    except Exception:
        return None
