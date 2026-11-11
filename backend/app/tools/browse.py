"""Browser automation tool: render JS pages via headless Chromium (§7–8)."""
from __future__ import annotations

from app.config import get_settings
from app.errors import ToolResult
from app.services import browser, url_safety
from app.services.page_text import MAX_PAGE_CHARS
from app.tools.base import PermissionLevel, registry

ALLOWED_CODES = url_safety.BLOCKED_CODES | {"BROWSER_UNAVAILABLE", "BROWSER_FAILED",
                                            "EMPTY_PAGE", "TIMEOUT"}


async def browse_page(url: str) -> ToolResult:
    """Render a webpage with a real headless browser (JavaScript included) and return its readable text. Use when open_url fails or the page needs JavaScript to show content."""
    settings = get_settings()
    blocked = url_safety.validate_external_url(
        url, allow_private=settings.browse_allow_private
    )
    if blocked:
        return ToolResult.fail(
            blocked, url_safety.blocked_message(blocked, url),
            message=url_safety.blocked_message(blocked, url),
        )
    if not browser.browser_available():
        return ToolResult.fail(
            "BROWSER_UNAVAILABLE",
            "playwright/chromium not installed",
            message="Browser rendering is not available on this server; "
                    "use open_url instead.",
        )
    try:
        title, text = await browser.render_url(
            url, timeout_ms=int(settings.browser_timeout_seconds * 1000)
        )
    except browser.BrowserUnavailable as exc:
        return ToolResult.fail("BROWSER_UNAVAILABLE", str(exc),
                               message="Browser rendering is not available.")
    except Exception as exc:  # noqa: BLE001
        code = "TIMEOUT" if "Timeout" in str(exc) else "BROWSER_FAILED"
        return ToolResult.fail(code, str(exc),
                               message=f"The page could not be rendered ({exc}).")
    if not text:
        return ToolResult.fail("EMPTY_PAGE", url,
                               message="The page contained no readable text.")
    return ToolResult.ok(
        f"Rendered '{title or url}' ({len(text)} characters).",
        {"url": url, "title": title or url, "text": text[:MAX_PAGE_CHARS],
         "total_chars": len(text), "untrusted": True},
    )


registry.register(browse_page, permission=PermissionLevel.AUTO, category="research",
                  requires_internet=True, tags=["research", "web", "browser"])
