"""Headless page rendering with Playwright (spec §7–8).

Each render launches a fresh browser (no cross-event-loop state — tools are
invoked from independent asyncio loops in tests and in the agent). Missing
playwright/chromium degrades to a structured BrowserUnavailable error.
"""
from __future__ import annotations

import logging

from app.services.page_text import html_to_text

logger = logging.getLogger("jalora.browser")


class BrowserUnavailable(RuntimeError):
    """Playwright or its browser binary is not installed."""


def browser_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except Exception:  # pragma: no cover - import failure path
        return False


async def render_url(url: str, *, timeout_ms: int = 20000,
                     wait_until: str = "domcontentloaded") -> tuple[str, str]:
    """Render a URL (JS included) and return (title, readable_text).

    Launches an installed browser channel (Chrome/Edge) when the Playwright
    bundle is unavailable, so no large binary download is required.
    """
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover - env dependent
        raise BrowserUnavailable(
            "playwright is not installed (pip install playwright && "
            "playwright install chromium)"
        ) from exc

    last_error: Exception | None = None
    try:
        async with async_playwright() as pw:
            browser = None
            for channel in ("chrome", "msedge", None):
                try:
                    browser = await pw.chromium.launch(headless=True, channel=channel)
                    break
                except Exception as exc:  # noqa: BLE001 - try next channel
                    last_error = exc
            if browser is None:
                raise BrowserUnavailable(
                    "no Chromium-based browser available "
                    f"(install chrome/edge or run playwright install): {last_error}"
                )
            try:
                page = await browser.new_page(user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JaloraAI/0.1"
                ))
                await page.goto(url, timeout=timeout_ms, wait_until=wait_until)
                html = await page.content()
            finally:
                await browser.close()
    except BrowserUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - map to structured failure upstream
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc

    title, text = html_to_text(html)
    return title, text
