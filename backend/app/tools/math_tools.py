"""Mathematical calculation tools (§math): calculate, verify_math.

Backed by ``app.services.math_engine`` (SymPy). Pure compute — no Ollama/network.
A verified calculation is never overridden by model text: the fast-path in
``chat_service.execute_chat`` computes direct math before the model is invoked, and
the model can only reinforce a result produced by ``calculate``.
"""
from __future__ import annotations

from app.errors import ToolResult
from app.services import math_engine
from app.tools.base import PermissionLevel, registry


def calculate(expression: str) -> ToolResult:
    """Evaluate a mathematical expression or equation and return step-by-step
    working plus the verified answer. Supports arithmetic, fractions, algebra
    (solve/factor/simplify/expand), calculus (differentiate/integrate/limit),
    trigonometry, statistics, unit conversions and word problems.
    Examples: `x^2 - 5x + 6 = 0`, `differentiate sin(x)*x`, `integrate 3x^2`,
    `5 km to m`, `mean of 1 2 3 4 5`, `nCr(5,2)`."""
    try:
        result = math_engine.solve(expression)
    except Exception:  # noqa: BLE001 — tools must fail structurally
        return ToolResult.fail(
            "MATH_ERROR", "Could not compute the result.",
            message="I couldn't work that out — please check the expression.",
        )
    if not result.ok:
        msg = result.error or "Computation failed."
        return ToolResult.fail("MATH_ERROR", msg, message=msg)
    return ToolResult.ok(math_engine.format_reply(result), result.model_dump())


def verify_math(solution: str, equation: str) -> ToolResult:
    """Verify a solution by substituting it into an equation (spec #7).

    `solution` is the value/answer (e.g. "2"); `equation` is the expression that
    should equal zero or "f(x) = g(x)"."""
    try:
        result = math_engine.verify_math(solution, equation)
    except Exception:  # noqa: BLE001
        return ToolResult.fail(
            "MATH_ERROR", "Verification failed.",
            message="I couldn't verify that solution.",
        )
    if not result.ok:
        msg = result.error or "Verification failed."
        return ToolResult.fail("MATH_ERROR", msg, message=msg)
    return ToolResult.ok(math_engine.format_reply(result), result.model_dump())


registry.register(
    calculate, permission=PermissionLevel.AUTO, category="math", tags=["math"]
)
registry.register(
    verify_math, permission=PermissionLevel.AUTO, category="math", tags=["math", "verify"]
)
