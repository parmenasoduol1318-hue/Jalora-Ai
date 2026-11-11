"""Shared readable-text extraction for fetched/rendered pages (spec §7)."""
from __future__ import annotations

from bs4 import BeautifulSoup, NavigableString, Tag

MAX_PAGE_CHARS = 12000
USER_AGENT = "JaloraAI/0.1 (+research assistant)"
_STRIP_TAGS = ["script", "style", "noscript", "nav", "footer", "header", "aside",
               "iframe", "svg", "form"]


def html_to_text(html: str) -> tuple[str, str]:
    """Return (title, readable_text) preserving page blocks and table rows."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    blocks: list[str] = []
    block_tags = {
        "address", "blockquote", "dd", "dt", "h1", "h2", "h3", "h4", "h5",
        "h6", "li", "p", "pre",
    }

    def collect(node: Tag) -> None:
        for child in node.children:
            if not isinstance(child, Tag):
                if isinstance(child, NavigableString):
                    text = " ".join(str(child).split())
                    if text:
                        blocks.append(text)
                continue
            if child.name == "table":
                for row in child.find_all("tr"):
                    cells = [
                        " ".join(cell.stripped_strings)
                        for cell in row.find_all(["th", "td"], recursive=False)
                    ]
                    if any(cells):
                        blocks.append(" | ".join(cells))
                continue
            if child.name in block_tags:
                text = " ".join(child.stripped_strings)
                if text:
                    blocks.append(text)
                continue
            collect(child)

    root = soup.body or soup
    collect(root)
    return title, "\n".join(blocks)
