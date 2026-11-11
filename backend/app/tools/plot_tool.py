"""Plotting tools (Phase 6; professional charts + downloads in Phase 9).

Render a mathematical function, a numeric series, or a chart (scatter/bar/
histogram) to a high-resolution PNG artifact under ``data/uploads/<user_id>/``
served by the authenticated artifacts endpoint, shown inline by the chat UI and
downloadable — plus downloadable SVG and PDF vector exports. Pure compute +
offline: matplotlib renders a headless ``Agg`` figure; nothing is fabricated.

The graph collects any tool result carrying a ``data["artifact"]`` descriptor
(and the ``data["artifacts"]`` list) and emits it to the client, so returning
those dicts is all the frontend needs.
"""
from __future__ import annotations

from app.context import get_tool_context
from app.errors import ToolResult
from app.services import plotting
from app.tools.base import PermissionLevel, registry

_UNAVAILABLE = "Plotting is disabled or matplotlib is missing."


def _finish_plot(prefix: str, title: str, result: plotting.PlotResult,
                 summary: str, extra: dict | None = None) -> ToolResult:
    user_id = get_tool_context().user_id
    primary, infos = plotting.save_exports(user_id, prefix, title, result)
    data = {"artifact": primary, "artifacts": infos, **(extra or {})}
    return ToolResult.ok(summary, data)


def plot_function(expression: str, x_min: float = -10.0, x_max: float = 10.0,
                  title: str = "", points: int = plotting.DEFAULT_POINTS) -> ToolResult:
    """Plot y = expression on the interval [x_min, x_max] and return a PNG chart.

    Use for graphing functions, curves and equations in x. Examples:
    `plot_function("sin(x)")`, `plot_function("x^2 - 3*x + 2", -5, 5)`,
    `plot_function("1/x", -10, 10)`, `plot_function("exp(-x)*cos(x)", 0, 10)`."""
    if not plotting.is_available():
        return ToolResult.fail("PLOT_UNAVAILABLE", _UNAVAILABLE,
                               message="Plotting isn't available in this environment.")
    result = plotting.plot_function(expression, x_min, x_max, points, title)
    if not result.ok:
        msg = result.error or "Could not render the plot."
        return ToolResult.fail("PLOT_ERROR", msg, message=msg)
    summary = (f"Plotted y = {result.expression} over "
               f"[{result.x_min:g}, {result.x_max:g}] ({result.n_points} samples). "
               f"Exports: PNG, SVG, PDF.")
    return _finish_plot("plot", result.title, result, summary, {
        "expression": result.expression,
        "x_min": result.x_min, "x_max": result.x_max, "points": result.n_points,
    })


def plot_data(x_values: list, y_values: list, title: str = "",
              x_label: str = "x", y_label: str = "y") -> ToolResult:
    """Plot an explicit series of (x, y) data points and return a PNG chart.

    `x_values` and `y_values` are equal-length lists of numbers."""
    if not plotting.is_available():
        return ToolResult.fail("PLOT_UNAVAILABLE", _UNAVAILABLE,
                               message="Plotting isn't available in this environment.")
    result = plotting.plot_series(x_values, y_values, title, x_label, y_label)
    if not result.ok:
        msg = result.error or "Could not render the plot."
        return ToolResult.fail("PLOT_ERROR", msg, message=msg)
    return _finish_plot("chart", result.title, result,
                        f"Plotted {result.n_points} data points. Exports: PNG, SVG, PDF.",
                        {"points": result.n_points})


def plot_scatter(x_values: list, y_values: list, title: str = "",
                 x_label: str = "x", y_label: str = "y") -> ToolResult:
    """Scatter plot of (x, y) points; `x_values`/`y_values` are equal-length lists."""
    if not plotting.is_available():
        return ToolResult.fail("PLOT_UNAVAILABLE", _UNAVAILABLE,
                               message="Plotting isn't available in this environment.")
    result = plotting.plot_scatter(x_values, y_values, title, x_label, y_label)
    if not result.ok:
        msg = result.error or "Could not render the plot."
        return ToolResult.fail("PLOT_ERROR", msg, message=msg)
    return _finish_plot("scatter", result.title, result,
                        f"Scatter plot of {result.n_points} points. Exports: PNG, SVG, PDF.",
                        {"points": result.n_points})


def plot_bar(labels: list, values: list, title: str = "",
             x_label: str = "", y_label: str = "value") -> ToolResult:
    """Bar chart of `values` against categorical `labels` (equal-length lists)."""
    if not plotting.is_available():
        return ToolResult.fail("PLOT_UNAVAILABLE", _UNAVAILABLE,
                               message="Plotting isn't available in this environment.")
    result = plotting.plot_bar(labels, values, title, x_label, y_label)
    if not result.ok:
        msg = result.error or "Could not render the plot."
        return ToolResult.fail("PLOT_ERROR", msg, message=msg)
    return _finish_plot("bar", result.title, result,
                        f"Bar chart with {result.n_points} bars. Exports: PNG, SVG, PDF.",
                        {"points": result.n_points})


def plot_histogram(values: list, bins: int = 20, title: str = "",
                   x_label: str = "value", y_label: str = "frequency") -> ToolResult:
    """Histogram of a numeric sample `values`; `bins` controls the bin count."""
    if not plotting.is_available():
        return ToolResult.fail("PLOT_UNAVAILABLE", _UNAVAILABLE,
                               message="Plotting isn't available in this environment.")
    result = plotting.plot_histogram(values, bins, title, x_label, y_label)
    if not result.ok:
        msg = result.error or "Could not render the plot."
        return ToolResult.fail("PLOT_ERROR", msg, message=msg)
    return _finish_plot("histogram", result.title, result,
                        f"Histogram of {result.n_points} values. Exports: PNG, SVG, PDF.",
                        {"points": result.n_points, "bins": result.extras.get("bins")})


registry.register(plot_function, permission=PermissionLevel.AUTO, category="math",
                  tags=["math", "plot", "chart", "curve", "relationship",
                        "visualization", "slide"])
registry.register(plot_data, permission=PermissionLevel.AUTO, category="math",
                  tags=["math", "plot", "chart", "curve", "relationship",
                        "visualization", "slide"])
registry.register(plot_scatter, permission=PermissionLevel.AUTO, category="math",
                  tags=["math", "plot", "chart", "curve", "relationship",
                        "visualization", "slide"])
registry.register(plot_bar, permission=PermissionLevel.AUTO, category="math",
                  tags=["math", "plot", "chart", "curve", "relationship",
                        "visualization", "slide"])
registry.register(plot_histogram, permission=PermissionLevel.AUTO, category="math",
                  tags=["math", "plot", "chart", "curve", "relationship",
                        "visualization", "slide"])
