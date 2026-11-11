"""Persistent per-user browser session manager (spec §16, §17, §18, §19).

* One isolated session per user with a navigation history stack
  (back/forward) and an idle timeout safety net.
* Lifecycle is tied to a run/task: sessions are closed when the owning run
  finalizes, when ``browser_close`` is called, or after an idle period.
* Playwright instances must live in the event loop that created them — the
  manager detects loop migration and re-creates the session (tests run each
  ``asyncio.run`` in a fresh loop).

The driver (real Playwright or the offline fake) is injected by
:func:`open_session`, so the state machine itself is fully testable offline.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from app.config import get_settings
from app.services.browser_driver import BrowserDriver, create_driver

logger = logging.getLogger("jalora.browser")


class NoSession(RuntimeError):
    """No usable session/open history for this operation."""


@dataclass
class BrowserStatus:
    url: str = ""
    title: str = ""
    can_back: bool = False
    can_forward: bool = False
    session_open: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "title": self.title,
            "can_back": self.can_back,
            "can_forward": self.can_forward,
            "session_open": self.session_open,
        }


@dataclass
class BrowserSession:
    user_id: str
    driver: BrowserDriver
    history: list[str] = field(default_factory=list)
    index: int = -1
    loop: asyncio.AbstractEventLoop | None = None
    last_used: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock | None = None
    _open: bool = False

    @property
    def is_open(self) -> bool:
        return self._open

    def _touch(self) -> None:
        self.last_used = time.monotonic()

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used

    async def bind(self) -> None:
        """Attach to the current loop: recreate if it migrated or was closed."""
        loop = asyncio.get_running_loop()
        if self._open and self.loop is not loop:
            await self.close_resource()
        if not self._open:
            await self.driver.start()
            self._open = True
            self.loop = loop
            self.history = []
            self.index = -1
        if self.lock is None:
            self.lock = asyncio.Lock()
        self._touch()

    async def _goto(self, url: str) -> None:
        await self.driver.goto(
            url, timeout_ms=int(get_settings().browser_timeout_seconds * 1000)
        )

    async def navigate(self, url: str) -> BrowserStatus:
        """Push `url` onto the history stack (truncating any forward pages)."""
        await self.bind()
        async with self.lock:
            if self.index < len(self.history) - 1:
                self.history = self.history[: self.index + 1]
            await self._goto(url)
            self.history.append(url)
            self.index = len(self.history) - 1
            self._touch()
        return await self.snapshot()

    async def back(self) -> BrowserStatus:
        await self.bind()
        if self.index <= 0 or not self.history:
            raise NoSession("No previous page to go back to.")
        async with self.lock:
            self.index -= 1
            await self._goto(self.history[self.index])
            self._touch()
        return await self.snapshot()

    async def forward(self) -> BrowserStatus:
        await self.bind()
        if self.index >= len(self.history) - 1:
            raise NoSession("No page to go forward to.")
        async with self.lock:
            self.index += 1
            await self._goto(self.history[self.index])
            self._touch()
        return await self.snapshot()

    async def snapshot(self) -> BrowserStatus:
        url = self.history[self.index] if self.index >= 0 else ""
        title = ""
        try:
            title = await self.driver.title()
        except Exception:  # noqa: BLE001 - snapshot never raises
            pass
        return BrowserStatus(
            url=url, title=title or "",
            can_back=self.index > 0,
            can_forward=self.index < len(self.history) - 1,
            session_open=self._open,
        )

    async def close_resource(self) -> None:
        try:
            await self.driver.close()
        except Exception:  # noqa: BLE001 - best effort
            pass
        self._open = False
        self.history = []
        self.index = -1


_sessions: dict[str, BrowserSession] = {}


def get_session(user_id: str) -> BrowserSession | None:
    return _sessions.get(user_id)


def attach_session(session: BrowserSession) -> None:
    """Register an externally created session (tests inject a fake driver)."""
    _sessions[session.user_id] = session


def detach_session(user_id: str) -> None:
    _sessions.pop(user_id, None)


async def open_session(user_id: str) -> BrowserSession:
    """Return the user's session, creating it with the resolved driver.
    A stale session (loop mismatch) is closed and re-bound to the current
    loop; an attached-but-not-started session (tests inject a seeded fake
    driver) is started rather than discarded."""
    existing = _sessions.get(user_id)
    loop = asyncio.get_running_loop()
    if existing is not None and existing.is_open and existing.loop is loop:
        await existing.bind()
        return existing
    if existing is not None and existing.is_open and existing.loop is not loop:
        await existing.close_resource()
    if existing is not None and not existing.is_open:
        await existing.bind()
        _sessions[user_id] = existing
        return existing
    session = BrowserSession(user_id=user_id, driver=create_driver())
    await session.bind()
    _sessions[user_id] = session
    return session


async def close_user_session(user_id: str) -> None:
    session = _sessions.pop(user_id, None)
    if session is None:
        return
    await session.close_resource()


async def close_all_sessions() -> None:
    for user_id in list(_sessions.keys()):
        await close_user_session(user_id)


async def sweep_idle_sessions() -> int:
    """Close sessions idle past the timeout (safety net, spec §16 lifecycle)."""
    idle = get_settings().browser_session_idle_seconds
    now = time.monotonic()
    stale = [u for u, s in _sessions.items() if now - s.last_used > idle]
    for user_id in stale:
        await close_user_session(user_id)
    return len(stale)