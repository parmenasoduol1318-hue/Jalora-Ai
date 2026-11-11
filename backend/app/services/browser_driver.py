"""Browser drivers for persistent sessions (spec §16, §17, §18, §19).

`RealPlaywrightDriver` drives a real headless Chromium (channel fallback so no
bundle download is needed, mirroring the P6 approach). `FakeDriver` is a
deterministic offline driver used when ``JALORA_BROWSER_DRIVER=fake`` and by
tests — it keeps the full tool/approval/credential machinery testable without
a browser. Every driver raises :class:`DriverError` with structured codes.
"""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

from app.services.page_text import html_to_text
from app.config import get_settings

logger = logging.getLogger("jalora.browser")

# A tiny valid 1x1 transparent PNG so offline screenshots still render.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


_TIMEOUT_MS = 20000


class DriverError(RuntimeError):
    """Structured browser driver failure (code + detail, never raw stack)."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class BrowserDriver(Protocol):
    async def start(self) -> None: ...
    async def goto(self, url: str, timeout_ms: int = _TIMEOUT_MS) -> None: ...
    async def wait(self, milliseconds: int) -> None: ...
    async def back(self, timeout_ms: int = _TIMEOUT_MS) -> None: ...
    async def forward(self, timeout_ms: int = _TIMEOUT_MS) -> None: ...
    async def close(self) -> None: ...
    async def content(self) -> str: ...
    async def articles(self) -> list[str]: ...
    async def title(self) -> str: ...
    async def url(self) -> str: ...
    async def click(self, selector: str) -> None: ...
    async def type_text(self, selector: str, text: str) -> None: ...
    async def fill(self, selector: str, text: str) -> None: ...
    async def select_option(self, selector: str, value: str) -> None: ...
    async def scroll(self, direction: str, amount: int = 600) -> None: ...
    async def set_input_files(self, selector: str, path: str) -> None: ...
    async def download(self, url: str) -> tuple[str, bytes]: ...
    async def screenshot(self, path: str, full_page: bool = False) -> None: ...


# --------------------------------------------------------------------------- #
# real driver                                                                 #
# --------------------------------------------------------------------------- #
class RealPlaywrightDriver:
    """One persistent Playwright browser/context/page per session."""

    def __init__(self, timeout_ms: int = _TIMEOUT_MS):
        self.timeout_ms = timeout_ms
        self._py = None
        self._browser = None
        self._context = None
        self._page = None

    def _ensure_page(self):
        if self._page is None:
            raise DriverError("BROWSER_NOT_OPEN", "No browser session is open.")
        return self._page

    async def start(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:  # pragma: no cover - env dependent
            raise DriverError(
                "BROWSER_UNAVAILABLE",
                "playwright is not installed (pip install playwright && "
                "playwright install chromium)",
            ) from exc
        last_error: Exception | None = None
        try:
            self._py = await async_playwright().start()
            for channel in ("chrome", "msedge", None):
                try:
                    self._browser = await self._py.chromium.launch(
                        headless=True, channel=channel
                    )
                    break
                except Exception as exc:  # noqa: BLE001 - try next channel
                    last_error = exc
            if self._browser is None:
                raise DriverError(
                    "BROWSER_UNAVAILABLE",
                    "no Chromium-based browser available (install chrome/edge "
                    f"or run playwright install): {last_error}",
                )
            self._context = await self._browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) JaloraAI/0.1",
                viewport={"width": 1280, "height": 900},
            )
            self._page = await self._context.new_page()
        except DriverError:
            await self.close()
            raise
        except Exception as exc:  # noqa: BLE001
            await self.close()
            raise DriverError("BROWSER_FAILED", f"{type(exc).__name__}: {exc}") from exc

    async def goto(self, url: str, timeout_ms: int = _TIMEOUT_MS) -> None:
        page = self._ensure_page()
        try:
            await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            raise _map_error(url, exc)

    async def wait(self, milliseconds: int) -> None:
        try:
            await self._ensure_page().wait_for_timeout(milliseconds)
        except Exception as exc:  # noqa: BLE001
            raise _map_error("wait", exc)

    async def back(self, timeout_ms: int = _TIMEOUT_MS) -> None:
        page = self._ensure_page()
        try:
            await page.go_back(timeout=timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            raise _map_error("back", exc)

    async def forward(self, timeout_ms: int = _TIMEOUT_MS) -> None:
        page = self._ensure_page()
        try:
            await page.go_forward(timeout=timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            raise _map_error("forward", exc)

    async def close(self) -> None:
        try:
            if self._context is not None:
                await self._context.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._py is not None:
                await self._py.stop()
        except Exception:  # noqa: BLE001
            pass
        self._page = self._context = self._browser = self._py = None

    async def content(self) -> str:
        return await self._ensure_page().content()

    async def articles(self) -> list[str]:
        page = self._ensure_page()
        try:
            return await page.locator("article").evaluate_all(
                """elements => elements
                    .filter(element => element.getClientRects().length > 0)
                    .map(element => (element.innerText || "").trim())
                    .filter(Boolean)
                    .slice(0, 40)"""
            )
        except Exception as exc:  # noqa: BLE001
            raise _map_error("extract article cards", exc)

    async def links(self) -> list[tuple[str, str]]:
        page = self._ensure_page()
        try:
            return await page.locator("a[href]").evaluate_all(
                """elements => elements.map(element => [
                    (element.innerText || element.getAttribute("aria-label") || "").trim(),
                    element.href
                ])"""
            )
        except Exception as exc:  # noqa: BLE001
            raise _map_error("extract links", exc)

    async def inputs(self) -> list[dict[str, object]]:
        page = self._ensure_page()
        try:
            return await page.locator("input, select, textarea").evaluate_all(
                """elements => elements
                    .filter(element =>
                        element.getClientRects().length > 0 &&
                        element.type !== "hidden" &&
                        element.type !== "submit" &&
                        element.type !== "button"
                    )
                    .map(element => {
                        const tag = element.tagName.toLowerCase();
                        const allOfType = Array.from(
                            document.querySelectorAll(tag)
                        );
                        const selector = element.id
                            ? `#${CSS.escape(element.id)}`
                            : `${tag} >> nth=${allOfType.indexOf(element)}`;
                        const labels = element.labels
                            ? Array.from(element.labels)
                                .map(label => label.innerText.trim())
                                .filter(Boolean)
                            : [];
                        const labelledBy = element.getAttribute("aria-labelledby");
                        if (labelledBy) {
                            labels.push(...labelledBy.split(/\\s+/).map(id => {
                                const label = document.getElementById(id);
                                return label ? label.innerText.trim() : "";
                            }).filter(Boolean));
                        }
                        const ariaLabel = element.getAttribute("aria-label");
                        if (ariaLabel) labels.push(ariaLabel);
                        const field = {
                            selector,
                            tag,
                            name: element.name || "",
                            type: element.type || tag,
                            label: [...new Set(labels)].join(" ").slice(0, 200),
                            placeholder: element.placeholder || "",
                            required: Boolean(element.required),
                            disabled: Boolean(element.disabled)
                        };
                        if (tag === "select") {
                            field.options = Array.from(element.options)
                                .slice(0, 100)
                                .map(option => ({
                                    label: option.label.trim().slice(0, 120),
                                    value: option.value,
                                    disabled: Boolean(option.disabled)
                                }));
                        }
                        return field;
                    })"""
            )
        except Exception as exc:  # noqa: BLE001
            raise _map_error("extract form fields", exc)

    async def title(self) -> str:
        try:
            return await self._ensure_page().title()
        except Exception as exc:  # noqa: BLE001
            raise _map_error("title", exc)

    async def url(self) -> str:
        return self._ensure_page().url

    async def click(self, selector: str) -> None:
        try:
            await self._ensure_page().click(selector, timeout=self.timeout_ms)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(selector, exc)

    async def type_text(self, selector: str, text: str) -> None:
        try:
            await self._ensure_page().type(selector, text, delay=0,
                                           timeout=self.timeout_ms)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(selector, exc)

    async def fill(self, selector: str, text: str) -> None:
        try:
            await self._ensure_page().fill(selector, text, timeout=self.timeout_ms)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(selector, exc)

    async def select_option(self, selector: str, value: str) -> None:
        try:
            await self._ensure_page().select_option(selector, value, timeout=self.timeout_ms)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(selector, exc)

    async def scroll(self, direction: str, amount: int = 600) -> None:
        page = self._ensure_page()
        try:
            if direction in ("down", "up"):
                sign = 1 if direction == "down" else -1
                await page.mouse.wheel(0, sign * amount)
            elif direction == "top":
                await page.evaluate("window.scrollTo(0, 0)")
            elif direction == "bottom":
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            else:
                raise DriverError("INVALID_SCROLL",
                                  f"direction must be up|down|top|bottom, got '{direction}'.")
        except DriverError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _map_error("scroll", exc)

    async def set_input_files(self, selector: str, path: str) -> None:
        try:
            await self._ensure_page().set_input_files(selector, path)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(selector, exc)

    async def download(self, url: str) -> tuple[str, bytes]:
        try:
            resp = await self._ensure_page().request.get(url, timeout=self.timeout_ms)
            if resp.status >= 400:
                raise DriverError(
                    "HTTP_ERROR", f"{url} returned HTTP {resp.status}"
                )
            return url.rsplit("/", 1)[-1] or "download.bin", await resp.body()
        except DriverError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _map_error(url, exc)

    async def screenshot(self, path: str, full_page: bool = False) -> None:
        try:
            await self._ensure_page().screenshot(path=path, full_page=full_page)
        except Exception as exc:  # noqa: BLE001
            raise _map_error("screenshot", exc)


def _map_error(context: str, exc: Exception) -> DriverError:
    name = type(exc).__name__
    text = str(exc)
    if "Timeout" in text or name == "TimeoutError":
        return DriverError("TIMEOUT", f"Browser action timed out (for {context}).")
    if "No element" in text or "locator" in text.lower() or "Selector" in text:
        return DriverError("NO_ELEMENT", f"Could not find element: {context}")
    return DriverError("BROWSER_FAILED", f"{name}: {text}")


# --------------------------------------------------------------------------- #
# fake driver (offline, deterministic)                                          #
# --------------------------------------------------------------------------- #
_LINK_RE = re.compile(r'<a\s+[^>]*href="([^"]+)"[^>]*>([^<]*)</a>', re.I)
_INPUT_RE = re.compile(r"<input\b([^>]*)>", re.I)
_ATTR_RE = re.compile(r'([\w-]+)\s*=\s*"([^"]*)"')


def _parse_attrs(tag: str) -> dict[str, str]:
    return {k.lower(): v for k, v in _ATTR_RE.findall(tag)}


@dataclass
class FakeDriver:
    """Deterministic offline driver. `pages` maps URL -> HTML; interactions
    are recorded so tests can assert on them."""
    pages: dict[str, str] = field(default_factory=dict)
    current_url: str = ""
    html: str = ""
    visited: list[str] = field(default_factory=list)
    clicked: list[str] = field(default_factory=list)
    typed: list[tuple[str, str]] = field(default_factory=list)
    filled: list[tuple[str, str]] = field(default_factory=list)
    selected: list[tuple[str, str]] = field(default_factory=list)
    scrolls: list[str] = field(default_factory=list)
    uploaded: list[tuple[str, str]] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    waits: list[int] = field(default_factory=list)
    _open: bool = False

    async def start(self) -> None:
        self._open = True

    def _resolve(self, href: str) -> str:
        if href.startswith("http://") or href.startswith("https://"):
            return href
        from urllib.parse import urljoin

        return urljoin(self.current_url or href, href)

    async def goto(self, url: str, timeout_ms: int = _TIMEOUT_MS) -> None:
        if not self._open:
            raise DriverError("BROWSER_NOT_OPEN", "No browser session is open.")
        self.current_url = url
        self.visited.append(url)
        self.html = self.pages.get(url) or (
            f"<html><head><title>{url}</title></head>"
            f"<body><p>Content for {url}</p></body></html>"
        )

    async def wait(self, milliseconds: int) -> None:
        if not self._open:
            raise DriverError("BROWSER_NOT_OPEN", "No browser session is open.")
        self.waits.append(milliseconds)

    async def back(self, timeout_ms: int = _TIMEOUT_MS) -> None:
        pass  # history is managed by the session; driver just allows nav

    async def forward(self, timeout_ms: int = _TIMEOUT_MS) -> None:
        pass

    async def close(self) -> None:
        self._open = False

    async def content(self) -> str:
        return self.html

    async def articles(self) -> list[str]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(self.html, "lxml")
        return [
            "\n".join(
                line.strip()
                for line in article.get_text(separator="\n").splitlines()
                if line.strip()
            )
            for article in soup.find_all("article")[:40]
            if article.get_text(strip=True)
        ]

    async def title(self) -> str:
        title, _ = html_to_text(self.html)
        return title

    async def url(self) -> str:
        return self.current_url

    async def click(self, selector: str) -> None:
        self.clicked.append(selector)
        for href, text in _LINK_RE.findall(self.html):
            if text.strip() == selector.strip():
                await self.goto(self._resolve(href))
                return

    async def type_text(self, selector: str, text: str) -> None:
        self.typed.append((selector, text))

    async def fill(self, selector: str, text: str) -> None:
        self.filled.append((selector, text))

    async def select_option(self, selector: str, value: str) -> None:
        self.selected.append((selector, value))

    async def scroll(self, direction: str, amount: int = 600) -> None:
        self.scrolls.append(direction)

    async def set_input_files(self, selector: str, path: str) -> None:
        self.uploaded.append((selector, path))

    async def download(self, url: str) -> tuple[str, bytes]:
        self.downloaded.append(url)
        return url.rsplit("/", 1)[-1] or "download.bin", b"fake-download-content"

    async def screenshot(self, path: str, full_page: bool = False) -> None:
        self.screenshots.append(path)
        with open(path, "wb") as fh:
            fh.write(_TINY_PNG)

    # --- helpers used by browser_extract ------------------------------------
    def links(self) -> list[tuple[str, str]]:
        return [(text.strip(), self._resolve(href))
                for href, text in _LINK_RE.findall(self.html)]

    def inputs(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for tag in _INPUT_RE.findall(self.html):
            attrs = _parse_attrs(tag)
            if "type" in attrs and attrs["type"] in ("hidden", "submit", "button"):
                continue
            out.append({
                "selector": ("input#" + attrs["id"]) if attrs.get("id")
                            else ("input[name=" + attrs["name"] + "]") if attrs.get("name")
                            else "input",
                "name": attrs.get("name", ""),
                "type": attrs.get("type", "text"),
                "placeholder": attrs.get("placeholder", ""),
            })
        return out


# --------------------------------------------------------------------------- #
# factory                                                                      #
# --------------------------------------------------------------------------- #
def browser_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except Exception:  # pragma: no cover - import failure path
        return False


def resolve_driver_mode() -> str:
    """auto -> real when playwright importable, else fake (logged degrade)."""
    settings = get_settings()
    mode = settings.browser_driver
    if mode in ("real", "fake"):
        return mode
    if browser_available():
        return "real"
    logger.warning("browser driver 'auto': playwright unavailable; "
                   "falling back to the offline fake driver.")
    return "fake"


def create_driver(mode: str | None = None, *, pages: dict[str, str] | None = None) -> BrowserDriver:
    """Build the driver for the resolved mode. `pages` only applies to fake."""
    mode = mode or resolve_driver_mode()
    if mode == "real":
        return RealPlaywrightDriver()
    if mode == "fake":
        return FakeDriver(pages=pages or {})
    raise DriverError("BROWSER_UNAVAILABLE", f"Unknown browser driver mode: {mode}")