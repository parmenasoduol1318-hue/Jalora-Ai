"""Compute-first math/science explanation layer (Phase 0).

Design rules (spec items 6, 8, 9):
  * The :class:`MathResult` computed by :mod:`app.services.math_engine` is
    authoritative. The user's active model only writes the narrative; the
    verified answer block is always appended afterwards.
  * Any answer restatement in the model text is stripped, so a drifting model
    can never change a computed number.
  * If no model is configured (or the call fails/times out), we fall back to the
    deterministic computed steps — math keeps working offline.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import get_settings
from app.services import math_engine, pedagogy
from app.services.model_router import ModelNotConfiguredError, ModelRouter

logger = logging.getLogger("jalora.explain")


_EXPLAIN_SYSTEM = (
    "You are Jalora's maths and science tutor. A trusted symbolic/numeric engine "
    "has already computed the result. Your ONLY job is to explain, step by step, "
    "how to reach that result. Rules:\n"
    "1. The computed result is AUTHORITATIVE — never contradict, recompute or "
    "'correct' it.\n"
    "2. Do NOT state a final answer yourself and do NOT write an 'Answer:' line; "
    "the verified answer is appended after your explanation.\n"
    "3. Be concise. Use plain markdown; inline math in \\( ... \\) is fine.\n"
    "4. If unsure about the method, describe the computed steps verbatim."
)

_ANSWER_LINE = re.compile(
    r"^\s*(?:>+\s*)?(?:\*\*)?\s*(?:final\s+)?answer\s*(?:\*\*)?\s*[:\-].*$",
    re.IGNORECASE | re.MULTILINE,
)


def _strip_answer_lines(text: str) -> str:
    cleaned = _ANSWER_LINE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _computed_context(result: math_engine.MathResult) -> str:
    lines = [f"{i}. {step}" for i, step in enumerate(result.steps, 1)]
    if result.exact:
        lines.append(f"Computed value: {result.exact}")
    if result.numeric and result.numeric != result.exact:
        lines.append(f"Computed decimal: {result.numeric}")
    return "\n".join(lines)


async def explain_math(
    *,
    router: ModelRouter,
    question: str,
    result: math_engine.MathResult,
    provider_override: str | None = None,
    model_override: str | None = None,
    disabled: Any = None,
    level: str | None = None,
    show_working: bool | None = None,
) -> str:
    """Return a model explanation followed by the verified answer block.

    Always returns the authoritative :func:`math_engine.format_reply` block; the
    model text is a best-effort prefix that can never override it. When
    ``show_working`` is False only the final answer line is returned (no model
    call, no steps). ``show_working=None`` falls back to the config default.
    """
    settings = get_settings()
    if show_working is None:
        show_working = settings.math_show_working
    # Resolve the pedagogical level (explicit config/arg wins, else inferred from
    # the question) and build the deterministic, level-aware fallback once.
    effective = pedagogy.resolve_level(level or settings.math_level, question)
    verified_block = pedagogy.render(result, effective, show_working=show_working)
    if not show_working:
        return verified_block
    if not result.ok or not settings.math_explain:
        return verified_block

    try:
        route = router.route(
            message=question,
            provider_override=provider_override,
            model_override=model_override,
            disabled=disabled,
        )
        model = router.get_model(route)
    except ModelNotConfiguredError:
        return verified_block
    except Exception:  # noqa: BLE001
        logger.exception("math explain: routing failed")
        return verified_block

    prompt = (
        f"Question: {question}\n\n"
        f"Verified computed steps (authoritative):\n{_computed_context(result)}\n\n"
        f"{pedagogy.model_directive(effective)}"
    )
    try:
        resp = await asyncio.wait_for(
            model.ainvoke([
                SystemMessage(content=_EXPLAIN_SYSTEM),
                HumanMessage(content=prompt),
            ]),
            timeout=max(5, settings.math_timeout_seconds),
        )
        text = getattr(resp, "content", "") or ""
        if isinstance(text, list):
            text = " ".join(str(part) for part in text)
        text = _strip_answer_lines(str(text)).strip()
        if len(text) < 20:
            return verified_block
        return f"{text}\n\n{verified_block}"
    except Exception:  # noqa: BLE001
        logger.exception("math explain: model call failed")
        return verified_block
