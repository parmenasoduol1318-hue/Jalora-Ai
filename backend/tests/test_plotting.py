"""Phase 6: offline plotting service + tools.

Rendering is exercised headlessly (matplotlib ``Agg``); the artifact the tool
produces must be a real PNG under the user's upload folder and be described by
a descriptor the chat UI can render inline.
"""
import json

from app.config import get_settings
from app.context import ToolContext, set_tool_context
from app.services import plotting
from app.tools import registry

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _parse(raw: str) -> dict:
    return json.loads(raw)


def _tool(name: str):
    spec = registry.get(name)
    assert spec is not None
    return spec.tool


def test_plotting_is_available_in_test_env():
    assert plotting.is_available() is True


def test_plot_function_renders_png_and_svg():
    result = plotting.plot_function("sin(x)", 0.0, 6.283185307179586)
    assert result.ok is True
    assert result.png is not None and result.png.startswith(PNG_MAGIC)
    assert result.svg is not None and "<svg" in result.svg
    assert result.x_min == 0.0 and result.n_points == plotting.DEFAULT_POINTS


def test_plot_function_handles_domain_gaps_without_crashing():
    result = plotting.plot_function("log(x)", -10.0, 10.0)
    assert result.ok is True
    assert result.extras["finite_points"] < result.n_points


def test_plot_function_rejects_unparseable_expression():
    result = plotting.plot_function("sin(", 0.0, 1.0)
    assert result.ok is False
    assert result.error


def test_plot_function_rejects_inverted_or_empty_range():
    assert plotting.plot_function("x", 5.0, 5.0).ok is False
    assert plotting.plot_function("x", 10.0, -10.0).ok is False
    assert plotting.plot_function("x", -1e9, 1e9).ok is False


def test_plot_function_rejects_extra_free_variables():
    result = plotting.plot_function("x + a", 0.0, 1.0)
    assert result.ok is False
    assert "a" in (result.error or "")


def test_plot_series_validates_shape():
    assert plotting.plot_series([1, 2, 3], [1, 2]).ok is False
    assert plotting.plot_series([], []).ok is False
    assert plotting.plot_series([1, 2, 3], [1, 4, 9]).ok is True


def test_parse_request_recognises_plot_phrasings():
    assert plotting.parse_request("plot sin(x)") == ("sin(x)", -10.0, 10.0)
    assert plotting.parse_request("graph y = x^2 from -5 to 5") == ("x^2", -5.0, 5.0)
    parsed = plotting.parse_request("draw cos(x) between 0 and 2pi")
    assert parsed is not None
    expr, lo, hi = parsed
    assert expr == "cos(x)" and lo == 0.0 and round(hi, 5) == 6.28319
    assert plotting.parse_request("plot the function x^3 for x from -2 to 2") == ("x^3", -2.0, 2.0)


def test_parse_request_ignores_non_plot_messages():
    assert plotting.parse_request("what is 2+2") is None
    assert plotting.parse_request("define a graph") is None


def test_plot_tool_writes_artifact_descriptor():
    set_tool_context(ToolContext(user_id="u1"))
    try:
        result = _parse(_tool("plot_function").invoke(
            {"expression": "cos(x)", "x_min": -3.14, "x_max": 3.14}))
    finally:
        set_tool_context(None)
    assert result["success"] is True
    info = result["data"]["artifact"]
    assert info["kind"] == "plot"
    assert info["mime"] == "image/png"
    assert info["url"].startswith("/api/artifacts/u1/plots/")
    assert "\\" not in info["url"]
    assert info["size_bytes"] > 0
    disk = (get_settings().upload_dir / "u1" / "plots")
    assert any(p.name.endswith(".png") for p in disk.iterdir())


def test_plot_data_tool_writes_artifact():
    set_tool_context(ToolContext(user_id="u2"))
    try:
        result = _parse(_tool("plot_data").invoke(
            {"x_values": [1, 2, 3], "y_values": [2, 4, 6], "title": "growth"}))
    finally:
        set_tool_context(None)
    assert result["success"] is True
    assert result["data"]["artifact"]["kind"] == "plot"


def test_plot_tool_reports_bad_expression_as_structured_failure():
    set_tool_context(ToolContext(user_id="u3"))
    try:
        result = _parse(_tool("plot_function").invoke({"expression": "sin("}))
    finally:
        set_tool_context(None)
    assert result["success"] is False
    assert result["error"]["code"] == "PLOT_ERROR"
    assert "Traceback" not in json.dumps(result)


def test_plot_tool_without_context_fails_structurally():
    set_tool_context(None)
    result = _parse(_tool("plot_function").invoke({"expression": "x"}))
    assert result["success"] is False
    assert result["error"]["code"] == "TOOL_EXCEPTION"


def test_plot_tool_honours_disabled_flag(monkeypatch):
    monkeypatch.setattr(get_settings(), "plots_enabled", False)
    assert plotting.is_available() is False
    set_tool_context(ToolContext(user_id="u4"))
    try:
        result = _parse(_tool("plot_function").invoke({"expression": "x"}))
    finally:
        set_tool_context(None)
    assert result["success"] is False
    assert result["error"]["code"] == "PLOT_UNAVAILABLE"


# --------------------------------------------------------------------------- #
# (Phase 9) professional multi-format exports: PNG (inline) + SVG/PDF (vector)
# --------------------------------------------------------------------------- #
PDF_MAGIC = b"%PDF"


def test_plot_function_exports_png_svg_pdf():
    result = plotting.plot_function("sin(x)", 0.0, 6.283185307179586)
    assert result.ok is True
    formats = result.formats()
    assert set(formats) == {"png", "svg", "pdf"}
    assert formats["png"].startswith(PNG_MAGIC)
    assert "<svg" in formats["svg"]
    assert formats["pdf"].startswith(PDF_MAGIC)


def test_chart_kinds_render_all_formats():
    cases = {
        "scatter": plotting.plot_scatter([1, 2, 3, 4], [2, 3, 5, 8]),
        "bar": plotting.plot_bar(["A", "B", "C"], [5, 3, 8]),
        "histogram": plotting.plot_histogram([1, 1, 2, 3, 3, 3, 5], bins=4),
    }
    for name, result in cases.items():
        assert result.ok is True, name
        formats = result.formats()
        assert formats["png"].startswith(PNG_MAGIC), name
        assert "<svg" in formats["svg"], name
        assert formats["pdf"].startswith(PDF_MAGIC), name


def test_format_mime_map_is_complete():
    assert plotting.FORMAT_MIME["png"] == "image/png"
    assert plotting.FORMAT_MIME["svg"] == "image/svg+xml"
    assert plotting.FORMAT_MIME["pdf"] == "application/pdf"


def test_save_exports_shares_basename_with_download_metadata():
    result = plotting.plot_function("x^2", -2.0, 2.0, title="parabola")
    primary, infos = plotting.save_exports("u_exports", "plot", result.title, result)
    assert len(infos) == 3
    assert primary is infos[0]
    assert primary["kind"] == "plot" and primary["mime"] == "image/png"
    stems = {info["file_name"].rsplit(".", 1)[0] for info in infos}
    assert len(stems) == 1  # PNG/SVG/PDF recognise as one figure
    for info in infos:
        assert info["download_url"] == info["url"] + "?download=1"
        assert info["file_name"] in info["url"]
        assert info["size_bytes"] > 0
    assert {info["mime"] for info in infos} == set(plotting.FORMAT_MIME.values())
    prefixes = {info["kind"] for info in infos}
    assert prefixes == {"plot", "plot-vector"}


def test_render_request_and_svg_diagram():
    result = plotting.render_request("plot sin(x)")
    assert result is not None and result.ok is True
    diagram = plotting.svg_diagram(result)
    assert diagram is not None
    assert diagram["kind"] == "svg" and diagram["id"] == "diag-plot"
    assert "<svg" in diagram["content"]
    assert plotting.render_request("what is 2+2") is None


def test_plot_tool_returns_primary_and_all_formats():
    set_tool_context(ToolContext(user_id="u5"))
    try:
        result = _parse(_tool("plot_function").invoke({"expression": "sin(x)"}))
    finally:
        set_tool_context(None)
    assert result["success"] is True
    data = result["data"]
    assert data["artifact"]["url"] == data["artifacts"][0]["url"]
    mimes = {info["mime"] for info in data["artifacts"]}
    assert mimes == set(plotting.FORMAT_MIME.values())


def test_chart_tools_are_registered():
    for name in ("plot_scatter", "plot_bar", "plot_histogram"):
        assert _tool(name) is not None
    set_tool_context(ToolContext(user_id="u6"))
    try:
        scatter = _parse(_tool("plot_scatter").invoke(
            {"x_values": [1, 2, 3], "y_values": [3, 2, 1]}))
        bar = _parse(_tool("plot_bar").invoke(
            {"labels": ["a", "b"], "values": [1, 2]}))
        hist = _parse(_tool("plot_histogram").invoke(
            {"values": [1, 2, 2, 3, 3, 3], "bins": 3}))
    finally:
        set_tool_context(None)
    assert scatter["success"] and bar["success"] and hist["success"]
    assert scatter["data"]["artifact"]["kind"] == "plot"
