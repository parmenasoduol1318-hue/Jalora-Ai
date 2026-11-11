"""Visual-output post-processing for chat replies (multi-purpose output layer).

Two concerns live here because they both rewrite the model's reply text at
finalization time and are consumed by the chat outcome / SSE stream:

* :func:`number_research_citations` — for research turns, rewrite markdown-link
  citations ``[Title](URL)`` whose URL is a tracked source into numbered
  hyperlinks ``[N](URL)`` and produce a structured references list.
* :func:`extract_diagrams` — pull fenced ``svg``/``canvas`` blocks (and inline
  ``<svg>``) out of the reply into structured diagram descriptors so the UI can
  render vector graphics without showing raw markup.

Both functions are pure (no I/O) so they are unit-testable without a model.
"""
from __future__ import annotations

import re
from typing import Any

# --- regexes -----------------------------------------------------------------
# Markdown link whose href is an absolute http(s) URL: [text](https://...)
_CITE_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
# Explicit [citation:URL] marker an agent can emit directly.
_CITE_MARKER_RE = re.compile(r"\[citation:(https?://[^\s\]]+)\]")
# Fenced ```svg / ```canvas blocks.
_FENCE_RE = re.compile(r"```(svg|canvas)\s*\n?([\s\S]*?)```", re.IGNORECASE)
# Inline <svg>...</svg> blocks (case-insensitive, non-greedy).
_INLINE_SVG_RE = re.compile(r"<svg[^>]*>[\s\S]*?</svg>", re.IGNORECASE)

# --- svg hardening -----------------------------------------------------------
_SCRIPT_RE = re.compile(r"<script\b[^>]*>[\s\S]*?</script>", re.IGNORECASE)
# on* event-handler attributes: onclick=... , onload="..." , etc.
_EVENT_ATTR_RE = re.compile(
    r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE
)
# javascript: URLs in style/url(...)
_JS_URL_RE = re.compile(r"url\(\s*['\"]?\s*javascript:", re.IGNORECASE)
# javascript: URLs in href/src/xlink:href attributes (e.g. <a href="javascript:…">)
_JS_HREF_RE = re.compile(
    r'''(href|xlink:href|src)\s*=\s*(["'])\s*javascript:[^"']*\2''', re.IGNORECASE
)
_FOREIGN_RE = re.compile(
    r"<foreignObject[^>]*>[\s\S]*?</foreignObject>", re.IGNORECASE
)

MAX_CITATION_SNIPPET = 300


def _sanitize_svg(content: str) -> str:
    """Best-effort hardening of model-generated SVG.

    Strips ``<script>`` blocks, ``on*`` event-handler attributes, ``javascript:``
    URLs and ``<foreignObject>`` payloads. SVG may originate from the model
    (untrusted) so this is defense-in-depth even though the backend produced it.
    """
    out = _SCRIPT_RE.sub("", content)
    out = _EVENT_ATTR_RE.sub("", out)
    out = _JS_URL_RE.sub("url(#", out)
    out = _JS_HREF_RE.sub(r"\1=\2#\2", out)
    out = _FOREIGN_RE.sub("", out)
    return out.strip()


def extract_diagrams(
    reply: str, max_diagrams: int = 8
) -> tuple[str, list[dict[str, Any]]]:
    """Extract SVG/canvas diagrams from ``reply``.

    Recognizes:
      * fenced ```` ```svg ```` blocks
      * fenced ```` ```canvas ```` blocks (a JSON array of drawing commands OR a
        ``data:`` URL image)
      * inline ``<svg>…</svg>`` blocks

    Returns ``(reply_without_diagram_blocks, diagrams)`` where each diagram is::

        {"id": "diag-0", "kind": "svg"|"canvas"|"image", "title": str, "content": str}

    Diagram blocks are removed from the reply so the model's raw markup is not
    rendered as text by the chat's Markdown renderer.
    """
    diagrams: list[dict[str, Any]] = []

    def _add(kind: str, content: str) -> None:
        if len(diagrams) >= max_diagrams:
            return
        if kind == "svg":
            content = _sanitize_svg(content)
        diagrams.append(
            {
                "id": f"diag-{len(diagrams)}",
                "kind": kind,
                "title": "",
                "content": content,
            }
        )

    out = reply

    def _fence(match: re.Match) -> str:
        lang = match.group(1).lower()
        content = match.group(2)
        if lang == "svg":
            _add("svg", content)
            return ""
        payload = content.strip()
        if payload.lower().startswith("data:"):
            _add("image", payload)
        else:
            _add("canvas", payload)
        return ""

    out = _FENCE_RE.sub(_fence, out)

    def _inline(match: re.Match) -> str:
        _add("svg", match.group(0))
        return ""

    out = _INLINE_SVG_RE.sub(_inline, out)

    # When nothing was extracted, return the original reply byte-for-byte so the
    # common (diagram-free) case is never perturbed. Only normalize the leftover
    # blank lines when we actually removed blocks.
    if not diagrams:
        return reply, diagrams

    out = re.sub(r"\n{3,}", "\n\n", out).strip("\n")
    if out:
        out += "\n"
    return out, diagrams


def number_research_citations(
    reply: str, sources: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """Rewrite research citations to numbered hyperlinks.

    For each markdown link ``[Title](URL)`` or ``[citation:URL]`` marker whose
    URL matches a tracked source, replace it with ``[N](URL)`` (a numbered
    hyperlink). Numbers are assigned by first appearance in the reply. Returns
    ``(reply, citations)`` where each citation is
    ``{index, title, url, snippet}`` (snippet truncated to 300 chars).

    Links to URLs that are NOT tracked sources are left untouched, so the model
    can still link to non-research URLs safely.
    """
    url_to_index: dict[str, int] = {}
    citations: list[dict[str, Any]] = []

    def _index_for(url: str) -> int | None:
        if url in url_to_index:
            return url_to_index[url]
        src = next((s for s in sources if s.get("url") == url), None)
        if src is None:
            return None
        idx = len(citations) + 1
        url_to_index[url] = idx
        citations.append(
            {
                "index": idx,
                "title": str(src.get("title") or url),
                "url": url,
                "snippet": str(src.get("snippet") or "")[:MAX_CITATION_SNIPPET],
            }
        )
        return idx

    # First pass: assign numbers in order of first appearance in the reply.
    for match in _CITE_LINK_RE.finditer(reply):
        _index_for(match.group(2))
    for match in _CITE_MARKER_RE.finditer(reply):
        _index_for(match.group(1))

    def _repl_link(match: re.Match) -> str:
        url = match.group(2)
        idx = url_to_index.get(url)
        if idx is None:
            return match.group(0)
        return f"[{idx}]({url})"

    def _repl_marker(match: re.Match) -> str:
        url = match.group(1)
        idx = url_to_index.get(url)
        if idx is None:
            return match.group(0)
        return f"[{idx}]({url})"

    reply = _CITE_LINK_RE.sub(_repl_link, reply)
    reply = _CITE_MARKER_RE.sub(_repl_marker, reply)
    citations.sort(key=lambda c: c["index"])
    return reply, citations


def apply_visual_postprocessing(
    *, intent: str, reply: str, sources: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the full visual post-processing pipeline for a finalized reply.

    1. Extract diagrams (svg/canvas) out of the reply (always — any turn may
       contain vector graphics).
    2. Number research citations when the turn is research or any sources
       were surfaced (so resumed runs re-derive numbering).

    Returns ``(reply, citations, diagrams)``. ``sources`` is passed through
    unchanged.
    """
    reply, diagrams = extract_diagrams(reply)
    citations: list[dict[str, Any]] = []
    if intent == "research" or sources:
        reply, citations = number_research_citations(reply, sources)
    return reply, citations, diagrams
