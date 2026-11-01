"""Per-site Playwright browser/context lifecycle. See docs/DESIGN.md section 6.

BrowserSession deliberately does NOT decide whether a re-login is needed - that
decision, and the retry loop around it, lives entirely in sites/base.py. This module
owns the Chromium process, the BrowserContext, and the on-disk storage_state cache.

The storage_state file is shared between the long-running MCP server and the one-shot
`tripsite login` CLI, which writes it after a human completes verification. Each
session therefore tracks the file's fingerprint from its last load or save: it reloads
when the file changed under it, and never overwrites a file someone else wrote.
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

# All three target sites are Korean consumer web apps behind bot-detection that 403s
# Playwright's default UA/locale outright (confirmed against nol.yanolja.com while
# building sites/yanolja.py - see docs/DESIGN.md section 8.1). A plain desktop Chrome
# UA + ko-KR locale was enough to pass; this isn't meant to evade detection (out of
# scope per docs/DESIGN.md section 1), just to look like an ordinary browser.
_DESKTOP_CHROME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def _launch_kwargs(headless: bool) -> dict:
    kwargs: dict = {"headless": headless}
    if os.path.exists(_PREINSTALLED_CHROMIUM):
        kwargs["executable_path"] = _PREINSTALLED_CHROMIUM
    return kwargs


def chromium_launch_error(*, headless: bool = True) -> str | None:
    """Launch the browser the server will use and close it again.

    Returns None on success, or the launch error text. Probing the real launch (rather
    than checking that a binary file exists) also catches a headless-shell binary that
    is missing while the full Chromium is present. Uses the sync API, so call it before
    the event loop starts (see mcp_server.run_server).
    """
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**_launch_kwargs(headless))
            browser.close()
    except Exception as exc:
        message = str(exc).strip()
        return message.splitlines()[0] if message else type(exc).__name__
    return None


def _file_fingerprint(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


class BrowserSession:
    """Owns one Chromium browser + one BrowserContext for a single site.

    storage_state is persisted to `<state_dir>/storage_state_<site>.json` so repeated
    calls - and separate processes - reuse the same login session instead of
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
        self._storage_fingerprint: tuple[int, int] | None = None

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
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(**_launch_kwargs(self._headless))
            self._context = await self._new_context()
        except BaseException:
            # Don't leave a half-started driver or browser behind: nothing else holds a
            # reference to this session yet, so nothing else would ever close it.
            await self.close()
            raise

    async def _new_context(self) -> BrowserContext:
        storage_state = self._load_storage_state_path()
        assert self._browser is not None
        context = await self._browser.new_context(
            storage_state=storage_state,
            user_agent=_DESKTOP_CHROME_USER_AGENT,
            locale="ko-KR",
        )
        context.set_default_navigation_timeout(self._nav_timeout_ms)
        context.set_default_timeout(self._nav_timeout_ms)
        self._storage_fingerprint = _file_fingerprint(self._storage_state_path)
        return context

    def _load_storage_state_path(self) -> str | None:
        """Path to pass to Playwright, or None if there is no usable saved state.

        A corrupt file (for example a write that was cut off) is treated as absent so
        the next login can overwrite it, instead of failing every call forever.
        """
        path = self._storage_state_path
        if not path.exists():
            return None
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("Ignoring unreadable storage_state for site=%s; starting fresh", self.site)
            return None
        return str(path)

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
                logger.debug("context.close() failed for site=%s (already dead?)", self.site, exc_info=True)
            self._context = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                logger.debug("browser.close() failed for site=%s (already dead?)", self.site, exc_info=True)
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                logger.debug("playwright.stop() failed for site=%s", self.site, exc_info=True)
            self._playwright = None

    def storage_changed_on_disk(self) -> bool:
        return _file_fingerprint(self._storage_state_path) != self._storage_fingerprint

    async def save_storage_state(self) -> None:
        if self._context is None:
            return
        if self.storage_changed_on_disk():
            logger.warning(
                "storage_state for site=%s was changed by another process; not overwriting it",
                self.site,
            )
            return
        state = await self._context.storage_state()
        self._state_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self._storage_state_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(state), encoding="utf-8")
        try:
            tmp_path.chmod(0o600)
        except OSError:
            pass  # best effort; not fatal on filesystems without POSIX permissions
        os.replace(tmp_path, self._storage_state_path)
        self._storage_fingerprint = _file_fingerprint(self._storage_state_path)

    async def is_connected(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    async def restart(self) -> None:
        """Recover from a crashed/disconnected browser (docs/DESIGN.md section 6)."""
        logger.warning("Restarting browser session for site=%s (disconnected)", self.site)
        await self.close()
        await self.start()

    async def _reload_context(self) -> None:
        logger.info("storage_state for site=%s changed on disk; reloading it", self.site)
        old_context = self._context
        self._context = await self._new_context()
        if old_context is not None:
            try:
                await old_context.close()
            except Exception:
                logger.debug("closing replaced context failed for site=%s", self.site, exc_info=True)

    async def new_page(self) -> Page:
        """Open a new page on this session's context.

        The caller owns the returned page and must close it (ideally via try/finally) -
        BrowserSession does not track pages it didn't open itself. Callers must hold the
        site's lock: a reload replaces the context, which would break pages in flight.
        """
        if not await self.is_connected():
            await self.restart()
        elif self.storage_changed_on_disk():
            await self._reload_context()
        assert self._context is not None
        return await self._context.new_page()
