"""Per-site Playwright browser/context lifecycle. See docs/DESIGN.md section 6.

BrowserSession deliberately does NOT decide whether a re-login is needed - that
decision, and the retry loop around it, lives entirely in sites/base.py. This module
only owns the Chromium process, the BrowserContext, and the on-disk storage_state cache.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

logger = logging.getLogger(__name__)

# Reuse the Chromium binary this environment already ships (see repo's session docs);
# falls back to Playwright's own managed browser when unset, e.g. on a developer's
# machine that ran `playwright install chromium`.
_PREINSTALLED_CHROMIUM = "/opt/pw-browsers/chromium"


class BrowserSession:
    """Owns one Chromium browser + one BrowserContext for a single site.

    storage_state is persisted to `<state_dir>/storage_state_<site>.json` across
    start()/close() cycles so repeated calls reuse the same login session instead of
    re-authenticating every time (docs/DESIGN.md section 1: minimize login frequency).
    """

    def __init__(
        self,
        site: str,
        *,
        headless: bool,
        state_dir: Path,
        nav_timeout_ms: int = 15_000,
    ) -> None:
        self.site = site
        self._headless = headless
        self._state_dir = state_dir
        self._nav_timeout_ms = nav_timeout_ms
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    @property
    def _storage_state_path(self) -> Path:
        return self._state_dir / f"storage_state_{self.site}.json"

    async def __aenter__(self) -> BrowserSession:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def start(self) -> None:
        if self._context is not None:
            return
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        launch_kwargs: dict = {"headless": self._headless}
        if os.path.exists(_PREINSTALLED_CHROMIUM):
            launch_kwargs["executable_path"] = _PREINSTALLED_CHROMIUM
        self._browser = await self._playwright.chromium.launch(**launch_kwargs)

        storage_state = str(self._storage_state_path) if self._storage_state_path.exists() else None
        self._context = await self._browser.new_context(storage_state=storage_state)
        self._context.set_default_navigation_timeout(self._nav_timeout_ms)
        self._context.set_default_timeout(self._nav_timeout_ms)

    async def close(self) -> None:
        # Best-effort at every step: close() is also how restart() recovers from a
        # crashed browser, where the context/browser handles are already dead and
        # would raise if we touched them normally.
        if self._context is not None:
            try:
                await self.save_storage_state()
            except Exception:
                logger.warning(
                    "Failed to save storage_state for site=%s during close()", self.site, exc_info=True
                )
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def save_storage_state(self) -> None:
        if self._context is None:
            return
        state = await self._context.storage_state()
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._storage_state_path.write_text(json.dumps(state))
        try:
            self._storage_state_path.chmod(0o600)
        except OSError:
            pass  # best effort; not fatal on filesystems without POSIX permissions

    async def is_connected(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    async def restart(self) -> None:
        """Recover from a crashed/disconnected browser (docs/DESIGN.md section 6)."""
        logger.warning("Restarting browser session for site=%s (disconnected)", self.site)
        await self.close()
        await self.start()

    async def new_page(self) -> Page:
        """Open a new page on this session's context.

        The caller owns the returned page and must close it (ideally via
        try/finally) - BrowserSession does not track pages it didn't open itself.
        """
        if not await self.is_connected():
            await self.restart()
        assert self._context is not None
        return await self._context.new_page()
