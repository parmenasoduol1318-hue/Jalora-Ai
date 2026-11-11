"""Function/data plotting service (Phase 6, professionalised in Phase 9).

Renders a mathematical function, a numeric series, or a chart (scatter/bar/
histogram) to a **high-resolution PNG** (served as an authenticated artifact and
shown inline by the chat UI) plus **SVG** and **PDF** vector exports (offline,
publication quality, downloadable). Nothing is fabricated: an expression that
cannot be parsed/plotted returns a structured failure instead of a guess.

Guards:
  * disabled by ``settings.plots_enabled`` (default True);
  * needs matplotlib (forced onto the headless ``Agg`` backend) and numpy;
    function plots additionally need SymPy — otherwise ``is_available()`` is
    False and callers fall back gracefully;
  * the sample count and axis span are bounded so a request cannot wedge the
    process (e.g. ``plot sin(x) from -1e9 to 1e9``);
  * a module lock serialises rendering because matplotlib is not thread-safe.
"""
from __future__ import annotations

import io
import logging
import re
import threading
from dataclasses import dataclass, field

from app.config import get_settings

logger = logging.getLogger("jalora.plotting")

try:
    import matplotlib

    matplotlib.use("Agg")  # headless; must be set before any canvas import
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.backends.backend_pdf import FigureCanvasPdf
    from matplotlib.backends.backend_svg import FigureCanvasSVG
    from matplotlib.figure import Figure

    _HAS_MPL = True
except Exception:  # noqa: BLE001
    _HAS_MPL = False

try:
    import numpy as np

    _HAS_NUMPY = True
except Exception:  # noqa: BLE001
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False

try:
    import sympy as _sp
    from sympy.parsing.sympy_parser import (
        convert_xor,
        implicit_multiplication_application,
        parse_expr,
        standard_transformations,
    )

    _TRANSFORM = standard_transformations + (convert_xor, implicit_multiplication_application)
    _HAS_SYMPY = True
except Exception:  # noqa: BLE001
    _sp = None  # type: ignore[assignment]
    _TRANSFORM = ()
    _HAS_SYMPY = False


_RENDER_LOCK = threading.Lock()

MAX_POINTS = 4000
DEFAULT_POINTS = 400
MIN_SPAN = 1e-9
MAX_SPAN = 1_000_000.0

# Export formats produced for every successful plot (all downloadable).
FORMATS: tuple[str, ...] = ("png", "svg", "pdf")
FORMAT_MIME: dict[str, str] = {
    "png": "image/png",
    "svg": "image/svg+xml",
    "pdf": "application/pdf",
}

# Chart geometry + a clean, professional colour palette.
FIG_SIZE = (8.0, 4.6)
DPI = 150
_COLORS = ("#2563eb", "#dc2626", "#059669", "#d97706",
           "#7c3aed", "#0891b2", "#db2777", "#65a30d")

# A restrained "whitegrid" look, applied per-render under the lock so the
# process-wide matplotlib configuration is never permanently mutated.
_STYLE: dict = {
    "figure.dpi": DPI,
    "savefig.dpi": DPI,
    "figure.facecolor": "#ffffff",
    "figure.autolayout": False,
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "axes.facecolor": "#ffffff",
    "axes.titlesize": 14,
    "axes.titleweight": "bold",
    "axes.titlepad": 12,
    "axes.labelsize": 11.5,
    "axes.edgecolor": "#9ca3af",
    "axes.linewidth": 0.9,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": "#e5e7eb",
    "grid.linewidth": 0.7,
    "xtick.color": "#374151",
    "ytick.color": "#374151",
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.frameon": True,
    "legend.framealpha": 0.9,
    "legend.edgecolor": "#e5e7eb",
    "legend.fontsize": 10,
    "lines.linewidth": 2.2,
    "lines.antialiased": True,
}


@dataclass
class PlotResult:
    """Outcome of a plot request. ``ok=False`` means we did NOT render.

    ``png``/``svg``/``pdf`` are the rendered exports (SVG is text, the others
    bytes); ``formats()`` lists the ones that succeeded.
    """
    ok: bool
    png: bytes | None = None
    svg: str | None = None
    pdf: bytes | None = None
    error: str | None = None
    expression: str = ""
    title: str = ""
    x_min: float = 0.0
    x_max: float = 0.0
    n_points: int = 0
    extras: dict = field(default_factory=dict)

    def formats(self) -> dict[str, bytes | str]:
        """The successful exports keyed by extension (png/svg/pdf)."""
        out: dict[str, bytes | str] = {}
        if self.png:
            out["png"] = self.png
        if self.svg:
            out["svg"] = self.svg
        if self.pdf:
            out["pdf"] = self.pdf
        return out


def is_available() -> bool:
    """True when plotting can run: matplotlib + numpy present and the feature
    flag is on. Callers must still handle SymPy absence for function plots."""
    try:
        enabled = bool(get_settings().plots_enabled)
    except Exception:  # noqa: BLE001
        enabled = False
    return bool(_HAS_MPL and _HAS_NUMPY and enabled)


def _default_title(expression: str) -> str:
    return f"y = {expression}" if expression else "Plot"


def _render(fig) -> tuple[bytes, str, bytes]:
    """Render a single figure to PNG bytes, an SVG string and PDF bytes."""
    png_buf = io.BytesIO()
    FigureCanvasAgg(fig).print_png(png_buf)
    svg_buf = io.StringIO()
    FigureCanvasSVG(fig).print_svg(svg_buf)
    pdf_buf = io.BytesIO()
    FigureCanvasPdf(fig).print_pdf(pdf_buf)
    return png_buf.getvalue(), svg_buf.getvalue(), pdf_buf.getvalue()


def _style_axes(ax) -> None:
    """A light, professional frame: no top/right spines, no tick marks."""
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(length=0)


def _draw_figure(*, title: str, xlabel: str, ylabel: str, draw, expression: str = "",
                 x_min: float = 0.0, x_max: float = 0.0, n_points: int = 0,
                 extras: dict | None = None) -> PlotResult:
    """Create a styled figure, call ``draw(ax)``, render all formats.

    Shared by every chart type; always runs under the render lock and a
    temporary rc-context so styling never leaks into the process defaults.
    """
    assert np is not None
    try:
        with _RENDER_LOCK, matplotlib.rc_context(_STYLE):
            fig = Figure(figsize=FIG_SIZE, dpi=DPI, layout="constrained")
            ax = fig.subplots()
            _style_axes(ax)
            if title:
                ax.set_title(title)
            ax.set_xlabel(xlabel)
            ax.set_ylabel(ylabel)
            draw(ax)
            png, svg, pdf = _render(fig)
            fig.clear()
    except Exception:  # noqa: BLE001 - rendering guard
        logger.exception("plot rendering failed")
        return PlotResult(False, error="Rendering the plot failed.", expression=expression,
                          title=title, x_min=x_min, x_max=x_max)
    return PlotResult(True, png=png, svg=svg, pdf=pdf, expression=expression, title=title,
                      x_min=x_min, x_max=x_max, n_points=n_points, extras=extras or {})


def _clean_expression(raw: str) -> str:
    """Strip a leading ``y =`` / ``f(x) =`` and stray trailing punctuation."""
    text = (raw or "").strip()
    text = re.sub(r"^\s*(?:y|f\s*\(\s*x\s*\))\s*=\s*", "", text, flags=re.IGNORECASE)
    text = text.strip().rstrip(".?,;")
    return text.strip()


def _as_float_list(values) -> "np.ndarray | None":
    assert np is not None
    try:
        arr = np.asarray(list(values), dtype=float)
    except Exception:  # noqa: BLE001 - user input; fail structurally
        return None
    return arr


def plot_function(expression: str, x_min: float = -10.0, x_max: float = 10.0,
                  points: int = DEFAULT_POINTS, title: str = "") -> PlotResult:
    """Plot ``y = expression`` over ``[x_min, x_max]`` (variable x)."""
    expr_text = _clean_expression(expression)
    x_min, x_max = float(x_min), float(x_max)
    title = title or _default_title(expr_text)
    if not is_available():
        return PlotResult(False, error="Plotting is unavailable.", expression=expr_text,
                          title=title, x_min=x_min, x_max=x_max)
    if not _HAS_SYMPY:
        return PlotResult(False, error="SymPy is required to plot a function.",
                          expression=expr_text, title=title, x_min=x_min, x_max=x_max)
    assert _sp is not None
    assert np is not None
    if not expr_text:
        return PlotResult(False, error="No expression given.", title=title,
                          x_min=x_min, x_max=x_max)
    span = x_max - x_min
    if span <= 0:
        return PlotResult(False, error="x range must be increasing (x_min < x_max).",
                          expression=expr_text, title=title, x_min=x_min, x_max=x_max)
    if span < MIN_SPAN or span > MAX_SPAN:
        return PlotResult(False, error="x range is out of bounds.",
                          expression=expr_text, title=title, x_min=x_min, x_max=x_max)

    var = _sp.Symbol("x", real=True)
    try:
        parsed = parse_expr(expr_text, transformations=_TRANSFORM,
                            local_dict={"x": var}, evaluate=True)
    except Exception:  # noqa: BLE001 - user input; fail structurally
        return PlotResult(False, error=f"Could not parse '{expr_text}'.",
                          expression=expr_text, title=title, x_min=x_min, x_max=x_max)

    free = {s.name for s in parsed.free_symbols}
    if free - {"x"}:
        extra = ", ".join(sorted(free - {"x"}))
        return PlotResult(False, error=f"Only the variable x is supported (got: {extra}).",
                          expression=expr_text, title=title, x_min=x_min, x_max=x_max)

    n = max(2, min(int(points or DEFAULT_POINTS), MAX_POINTS))
    try:
        fn = _sp.lambdify(var, parsed, "numpy")
        xs = np.linspace(x_min, x_max, n, dtype=float)
        with np.errstate(all="ignore"):
            ys = fn(xs)
    except Exception:  # noqa: BLE001 - user input; fail structurally
        return PlotResult(False, error=f"Could not evaluate '{expr_text}'.",
                          expression=expr_text, title=title, x_min=x_min, x_max=x_max)

    ys = np.asarray(ys, dtype=float) if not np.iscomplexobj(ys) else None
    if ys is None:
        return PlotResult(False, error="The expression is complex-valued over this range.",
                          expression=expr_text, title=title, x_min=x_min, x_max=x_max)
    if ys.ndim == 0:
        ys = np.full_like(xs, float(ys))

    def draw(ax) -> None:
        ax.axhline(0.0, color="#9ca3af", linewidth=0.8, alpha=0.7, zorder=1)
        ax.axvline(0.0, color="#9ca3af", linewidth=0.8, alpha=0.7, zorder=1)
        ax.plot(xs, ys, color=_COLORS[0], linewidth=2.2, zorder=3,
                label=str(parsed))

    result = _draw_figure(title=title, xlabel="x", ylabel="y", draw=draw,
                          expression=expr_text, x_min=x_min, x_max=x_max, n_points=n)
    if result.ok:
        finite = int(np.isfinite(ys).sum())
        result.extras = {"finite_points": finite}
    return result


def _prepare_pair(x_values, y_values, title: str):
    """Validate two equal-length numeric series into ``(xs, ys)`` arrays.

    Returns ``((xs, ys), None)`` on success or ``(None, PlotResult-failure)``.
    Shared by the series and scatter charts.
    """
    xs = _as_float_list(x_values)
    ys = _as_float_list(y_values)
    if xs is None or ys is None:
        return None, PlotResult(False, error="x and y values must be numbers.", title=title)
    if xs.ndim != 1 or ys.ndim != 1 or xs.size != ys.size:
        return None, PlotResult(False, error="x and y must be equal-length 1-D lists.", title=title)
    if xs.size < 1:
        return None, PlotResult(False, error="No data points given.", title=title)
    if xs.size > MAX_POINTS:
        return None, PlotResult(False, error=f"Too many points (limit {MAX_POINTS}).", title=title)
    return (xs, ys), None


def plot_series(x_values, y_values, title: str = "",
                xlabel: str = "x", ylabel: str = "y") -> PlotResult:
    """Plot an explicit numeric series (two equal-length lists)."""
    title = title or "Plot"
    if not is_available():
        return PlotResult(False, error="Plotting is unavailable.", title=title)
    prepared, error = _prepare_pair(x_values, y_values, title)
    if error is not None:
        return error
    assert prepared is not None
    xs, ys = prepared
    single_point = xs.size == 1

    def draw(ax) -> None:
        if single_point:
            ax.plot(xs, ys, "o", color=_COLORS[0], markersize=7, label="data")
        else:
            ax.plot(xs, ys, "-o", color=_COLORS[0], linewidth=2.0, markersize=4.5,
                    markerfacecolor="white", markeredgewidth=1.4, label="data")

    return _draw_figure(title=title, xlabel=xlabel, ylabel=ylabel, draw=draw,
                        x_min=float(xs.min()), x_max=float(xs.max()), n_points=int(xs.size))


def plot_scatter(x_values, y_values, title: str = "",
                 xlabel: str = "x", ylabel: str = "y") -> PlotResult:
    """Scatter plot of (x, y) points (two equal-length lists)."""
    title = title or "Scatter plot"
    if not is_available():
        return PlotResult(False, error="Plotting is unavailable.", title=title)
    prepared, error = _prepare_pair(x_values, y_values, title)
    if error is not None:
        return error
    assert prepared is not None
    xs, ys = prepared

    def draw(ax) -> None:
        ax.scatter(xs, ys, color=_COLORS[0], s=48, alpha=0.85,
                   edgecolors="white", linewidths=0.8, label="data")

    return _draw_figure(title=title, xlabel=xlabel, ylabel=ylabel, draw=draw,
                        x_min=float(xs.min()), x_max=float(xs.max()), n_points=int(xs.size))


def plot_bar(labels, values, title: str = "", xlabel: str = "", ylabel: str = "") -> PlotResult:
    """Bar chart of ``values`` against categorical ``labels``."""
    title = title or "Bar chart"
    if not is_available():
        return PlotResult(False, error="Plotting is unavailable.", title=title)
    assert np is not None
    try:
        cats = [str(label) for label in list(labels)]
    except Exception:  # noqa: BLE001 - user input; fail structurally
        return PlotResult(False, error="labels must be a list.", title=title)
    vals = _as_float_list(values)
    if vals is None:
        return PlotResult(False, error="values must be numbers.", title=title)
    if vals.ndim != 1 or len(cats) != vals.size:
        return PlotResult(False, error="labels and values must be equal-length lists.", title=title)
    if vals.size < 1:
        return PlotResult(False, error="No data points given.", title=title)
    if vals.size > MAX_POINTS:
        return PlotResult(False, error=f"Too many bars (limit {MAX_POINTS}).", title=title)

    positions = np.arange(vals.size)

    def draw(ax) -> None:
        ax.bar(positions, vals, color=_COLORS[0], width=0.62, zorder=3)
        ax.set_xticks(positions)
        ax.set_xticklabels(cats, rotation=(30 if len(cats) > 6 else 0),
                           ha=("right" if len(cats) > 6 else "center"))

    return _draw_figure(title=title, xlabel=xlabel, ylabel=ylabel, draw=draw,
                        x_min=float(positions.min()), x_max=float(positions.max()),
                        n_points=int(vals.size))


def plot_histogram(values, bins: int = 20, title: str = "",
                   xlabel: str = "value", ylabel: str = "frequency") -> PlotResult:
    """Histogram of a single numeric sample ``values``."""
    title = title or "Histogram"
    if not is_available():
        return PlotResult(False, error="Plotting is unavailable.", title=title)
    arr = _as_float_list(values)
    if arr is None:
        return PlotResult(False, error="values must be numbers.", title=title)
    if arr.ndim != 1 or arr.size < 1:
        return PlotResult(False, error="values must be a non-empty list.", title=title)
    if arr.size > MAX_POINTS:
        return PlotResult(False, error=f"Too many values (limit {MAX_POINTS}).", title=title)
    try:
        n_bins = max(1, min(int(bins or 20), 200))
    except (TypeError, ValueError):
        n_bins = 20

    def draw(ax) -> None:
        ax.hist(arr, bins=n_bins, color=_COLORS[0], edgecolor="white",
                linewidth=0.8, zorder=3)

    result = _draw_figure(title=title, xlabel=xlabel, ylabel=ylabel, draw=draw,
                          x_min=float(arr.min()), x_max=float(arr.max()), n_points=int(arr.size))
    if result.ok:
        result.extras = {"bins": n_bins}
    return result


# ---------------------------------------------------------------------------
# request parsing (offline fast-path for explicit "plot ..." messages)
# ---------------------------------------------------------------------------

_PLOT_VERB = re.compile(
    r"^\s*(?:please\s+)?(?:can you\s+|could you\s+)?"
    r"(?:plot|graph|draw|sketch|chart|visuali[sz]e)\b[:,]?\s*",
    re.IGNORECASE,
)
_BOUNDS = re.compile(
    r"\b(?:from|between|over|on|in the range|for)\s+"
    r"(?P<a>[^\s,]+)\s*(?:to|and|through|\.\.)\s*(?P<b>[^\s,?]+)",
    re.IGNORECASE,
)
_LEAD_NOISE = re.compile(
    r"^\s*(?:a\s+|the\s+)?(?:graph|plot|chart|curve|function)\s+of\s+",
    re.IGNORECASE,
)
_TRAIL_NOISE = re.compile(
    r"\s+(?:for|on|over|in)\s+[a-z]\s*$|^\s*(?:the\s+)?function\s+",
    re.IGNORECASE,
)


def _bounds_value(token: str) -> float | None:
    text = token.strip().lower().replace("π", "pi")
    try:
        return float(text)
    except ValueError:
        pass
    if _HAS_SYMPY:
        assert _sp is not None
        try:
            return float(parse_expr(text, transformations=_TRANSFORM,
                                    local_dict={"pi": _sp.pi, "e": _sp.E}))
        except Exception:  # noqa: BLE001 - unparseable bound
            return None
    return None


def parse_request(text: str) -> tuple[str, float, float] | None:
    """Detect an explicit plot request and return ``(expression, x_min, x_max)``.

    Returns ``None`` when the message is not an explicit plot request. Defaults
    the range to ``[-10, 10]`` when no bounds are given.
    """
    if not text:
        return None
    verb = _PLOT_VERB.match(text)
    if verb is None:
        return None
    rest = text[verb.end():].strip()
    rest = _LEAD_NOISE.sub("", rest)
    x_min, x_max = -10.0, 10.0
    bounds = _BOUNDS.search(rest)
    if bounds is not None:
        low, high = _bounds_value(bounds.group("a")), _bounds_value(bounds.group("b"))
        if low is not None and high is not None:
            x_min, x_max = low, high
            rest = rest[:bounds.start()]
    expr = _TRAIL_NOISE.sub("", rest).strip()
    expr = _clean_expression(expr)
    if not expr:
        return None
    return expr, x_min, x_max


def render_request(text: str, *, points: int = DEFAULT_POINTS) -> PlotResult | None:
    """Parse + render an explicit plot request in one call (or ``None``).

    Convenience for the chat fast-path: returns ``None`` when ``text`` is not a
    plot request; otherwise the (possibly failed) :class:`PlotResult`.
    """
    parsed = parse_request(text)
    if parsed is None:
        return None
    expression, x_min, x_max = parsed
    return plot_function(expression, x_min, x_max, points)


# Re-export for callers that build a diagram from a plot result.
def svg_diagram(result: PlotResult, *, diagram_id: str = "diag-plot") -> dict | None:
    """Build an inline-SVG diagram descriptor from a rendered plot (or None)."""
    if not result.ok or not result.svg:
        return None
    return {
        "id": diagram_id,
        "kind": "svg",
        "title": result.title,
        "content": result.svg,
    }


def save_exports(user_id: str, prefix: str, title: str,
                 result: PlotResult) -> tuple[dict, list[dict]]:
    """Persist every rendered format and return ``(primary, all_infos)``.

    All exports share one base name so the PNG, SVG and PDF of a single plot are
    recognisably the same figure. The PNG is the primary (inline) artifact; the
    vector exports are downloadable. ``artifacts`` is imported lazily to avoid an
    import-time coupling.
    """
    from app.services import artifacts

    base = artifacts.new_artifact_path(user_id, prefix, "png", subdir="plots")
    infos: list[dict] = []
    for ext, data in result.formats().items():
        path = base.with_suffix(f".{ext}")
        payload = data.encode("utf-8") if isinstance(data, str) else data
        path.write_bytes(payload)
        mime = FORMAT_MIME.get(ext, "application/octet-stream")
        if ext == "png":
            kind, label = "plot", title
        else:
            kind, label = "plot-vector", f"{title} ({ext.upper()})"
        infos.append(artifacts.artifact_info(user_id, path, kind=kind, mime=mime,
                                             title=label))
    primary = next((i for i in infos if i["mime"] == "image/png"), infos[0])
    return primary, infos


__all__ = [
    "PlotResult", "FORMATS", "FORMAT_MIME", "MAX_POINTS", "DEFAULT_POINTS",
    "is_available", "plot_function", "plot_series", "plot_scatter", "plot_bar",
    "plot_histogram", "parse_request", "render_request", "svg_diagram",
    "save_exports",
]
