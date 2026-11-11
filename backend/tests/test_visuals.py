"""Pure unit tests for the visual-output post-processing pipeline.

These exercise the stateless functions in ``app.services.visuals`` directly — no
model, no database, no network — so they are fast and deterministic.
"""
from __future__ import annotations

from app.services.visuals import (
    MAX_CITATION_SNIPPET,
    apply_visual_postprocessing,
    extract_diagrams,
    number_research_citations,
)


def _src(url: str, title: str | None = None, snippet: str | None = None) -> dict:
    return {"url": url, "title": title or url, "snippet": snippet or ""}


# --------------------------------------------------------------------------- #
# number_research_citations                                                     #
# --------------------------------------------------------------------------- #
def test_citations_renumber_tracked_link():
    reply = "See [Best Laptops](https://example.invalid/laptops) for specs."
    sources = [_src("https://example.invalid/laptops", title="Best Laptops")]
    out, cites = number_research_citations(reply, sources)
    assert out == "See [1](https://example.invalid/laptops) for specs."
    assert len(cites) == 1
    assert cites[0] == {
        "index": 1,
        "title": "Best Laptops",
        "url": "https://example.invalid/laptops",
        "snippet": "",
    }


def test_citations_number_by_first_appearance_even_when_repeat():
    reply = "[A](https://x.invalid) then [A again](https://x.invalid)"
    sources = [_src("https://x.invalid", title="A")]
    out, cites = number_research_citations(reply, sources)
    assert out == "[1](https://x.invalid) then [1](https://x.invalid)"
    assert [c["index"] for c in cites] == [1]


def test_citations_assign_indices_in_first_appearance_order():
    reply = "[second](https://b.invalid) [first](https://a.invalid)"
    sources = [_src("https://a.invalid"), _src("https://b.invalid")]
    out, cites = number_research_citations(reply, sources)
    assert "[1](https://b.invalid)" in out
    assert "[2](https://a.invalid)" in out
    assert {c["url"]: c["index"] for c in cites} == {
        "https://b.invalid": 1,
        "https://a.invalid": 2,
    }


def test_citations_citation_marker_form_is_numbered():
    reply = "see [citation:https://example.invalid/x]"
    sources = [_src("https://example.invalid/x", title="X")]
    out, cites = number_research_citations(reply, sources)
    assert "[1](https://example.invalid/x)" in out
    assert "[citation:" not in out
    assert cites[0]["index"] == 1
    assert cites[0]["url"] == "https://example.invalid/x"


def test_citations_leave_untracked_urls_untouched():
    reply = "[Not tracked](https://other.invalid) and [A](https://example.invalid/a)"
    sources = [_src("https://example.invalid/a", title="A")]
    out, cites = number_research_citations(reply, sources)
    assert "[Not tracked](https://other.invalid)" in out  # untouched
    assert "[1](https://example.invalid/a)" in out
    assert len(cites) == 1


def test_citations_snippet_truncated_to_max():
    long_snip = "x" * (MAX_CITATION_SNIPPET + 200)
    sources = [_src("https://example.invalid/a", title="A", snippet=long_snip)]
    _, cites = number_research_citations("[A](https://example.invalid/a)", sources)
    assert len(cites[0]["snippet"]) == MAX_CITATION_SNIPPET


def test_citations_no_sources_leaves_reply_unchanged():
    reply = "[A](https://example.invalid/a) plain text"
    out, cites = number_research_citations(reply, [])
    assert out == reply
    assert cites == []


def test_citations_title_falls_back_to_url_when_missing():
    reply = "[A](https://example.invalid/a)"
    sources = [{"url": "https://example.invalid/a"}]  # no title key
    _, cites = number_research_citations(reply, sources)
    assert cites[0]["title"] == "https://example.invalid/a"


# --------------------------------------------------------------------------- #
# extract_diagrams                                                              #
# --------------------------------------------------------------------------- #
def test_extract_svg_fence():
    reply = "Here: ```svg\n<svg><rect w=\"10\"/></svg>\n```\n"
    out, diags = extract_diagrams(reply)
    assert len(diags) == 1
    assert diags[0] == {
        "id": "diag-0", "kind": "svg", "title": "",
        "content": "<svg><rect w=\"10\"/></svg>",
    }
    assert "```svg" not in out
    assert "<svg" not in out  # removed from the reply text


def test_extract_canvas_json_diagram():
    reply = "```canvas\n[{\"type\":\"rect\",\"x\":1}]\n```"
    out, diags = extract_diagrams(reply)
    assert len(diags) == 1
    assert diags[0]["kind"] == "canvas"
    assert diags[0]["content"] == '[{\"type\":\"rect\",\"x\":1}]'
    assert "```canvas" not in out


def test_extract_data_url_image_diagram():
    reply = "```canvas\ndata:image/svg+xml;base64,abc123\n```"
    out, diags = extract_diagrams(reply)
    assert len(diags) == 1
    assert diags[0]["kind"] == "image"
    assert diags[0]["content"].startswith("data:image/svg+xml;base64,")


def test_extract_inline_svg():
    reply = "<svg><circle/></svg> text after"
    out, diags = extract_diagrams(reply)
    assert len(diags) == 1
    assert diags[0]["kind"] == "svg"
    assert "<svg" in diags[0]["content"]
    assert "<svg" not in out
    assert "text after" in out


def test_extract_multiple_diagrams_numbered_and_ordered():
    reply = "```svg\n<r/>\n```\n```canvas\n[]\n```"
    out, diags = extract_diagrams(reply)
    assert [d["id"] for d in diags] == ["diag-0", "diag-1"]
    assert [d["kind"] for d in diags] == ["svg", "canvas"]


def test_extract_no_diagrams_returns_reply_byte_for_byte():
    reply = "Hello! How can I help?"
    out, diags = extract_diagrams(reply)
    assert out is reply or out == reply  # untouched (identity on no-op path)
    assert diags == []
    assert out == reply


def test_extract_does_not_touch_non_diagram_fences():
    reply = "```python\nprint('hi')\n```\nplain"
    out, diags = extract_diagrams(reply)
    assert diags == []
    assert out == reply  # code fences are not diagram blocks


def test_extract_svg_sanitizer_strips_script_block():
    payload = "<svg><script>alert(1)</script><rect/></svg>"
    _, diags = extract_diagrams(f"```svg\n{payload}\n```")
    content = diags[0]["content"]
    assert "<script" not in content
    assert "alert" not in content
    assert "<rect" in content


def test_extract_svg_sanitizer_strips_event_handlers_foreignobject_js_url():
    payload = (
        '<svg><rect onload="steal()"/><a href="javascript:bad()">x</a>'
        '<foreignObject><script>1</script></foreignObject></svg>'
    )
    content = extract_diagrams(f"```svg\n{payload}\n```")[1][0]["content"]
    assert "onload" not in content
    assert "<foreignObject" not in content
    assert "<script" not in content
    assert "javascript:bad" not in content
    assert "<rect" in content


def test_extract_respects_max_diagrams_cap():
    blocks = "\n\n".join(f"```svg\n<r{i}/>\n```" for i in range(20))
    _, diags = extract_diagrams(blocks, max_diagrams=8)
    assert len(diags) == 8
    assert [d["id"] for d in diags] == [f"diag-{i}" for i in range(8)]


# --------------------------------------------------------------------------- #
# apply_visual_postprocessing (gating)                                          #
# --------------------------------------------------------------------------- #
def test_postprocess_research_numbers_citations_and_extracts_diagrams():
    reply = "[A](https://example.invalid/a) ```svg\n<r/>\n```"
    sources = [_src("https://example.invalid/a", title="A")]
    out, cites, diags = apply_visual_postprocessing(
        intent="research", reply=reply, sources=sources
    )
    assert "[1](https://example.invalid/a)" in out
    assert len(cites) == 1 and cites[0]["index"] == 1
    assert len(diags) == 1 and diags[0]["kind"] == "svg"


def test_postprocess_with_sources_numbers_citations_even_non_research():
    reply = "[A](https://example.invalid/a)"
    sources = [_src("https://example.invalid/a", title="A")]
    out, cites, _ = apply_visual_postprocessing(
        intent="chat", reply=reply, sources=sources
    )
    assert "[1](https://example.invalid/a)" in out
    assert len(cites) == 1


def test_postprocess_non_research_without_sources_skips_citations_but_keeps_diagrams():
    reply = "See [A](https://example.invalid/a) and ```svg\n<r/>\n```"
    out, cites, diags = apply_visual_postprocessing(
        intent="chat", reply=reply, sources=[]
    )
    assert "[A](https://example.invalid/a)" in out  # left untouched
    assert cites == []
    assert len(diags) == 1 and diags[0]["kind"] == "svg"


def test_postprocess_passthrough_when_nothing_present():
    reply = "No citations or diagrams here."
    out, cites, diags = apply_visual_postprocessing(
        intent="chat", reply=reply, sources=[]
    )
    assert out == reply
    assert cites == [] and diags == []
