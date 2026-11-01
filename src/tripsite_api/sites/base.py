"""Template for a single site's login + reservation-list adapter.

See docs/DESIGN.md section 7 for the full contract this class encodes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import ClassVar, NoReturn

from playwright.async_api import Page

from ..browser import BrowserSession
from ..config import Credentials, LoginThrottle, Settings
from ..errors import LoginRequiredError, ScrapeStructureError
from ..models import Reservation

logger = logging.getLogger(__name__)

# Defense in depth, not a complete guarantee: page.fill()'d values generally aren't
# reflected into the HTML `value` *attribute` at all (only the live DOM property),
# so outerHTML/page.content() already tend not to leak them. The real residual risk
# is a site that itself mirrors input values onto attributes (some custom JS
# components do), or toggles a password field's `type` to "text" (show-password),
# which is why this also matches on autocomplete/name/id, not just type="password".
_MASK_PASSWORD_JS = """() => {
    const clone = document.documentElement.cloneNode(true);
    const isPasswordish = (el) => {
        if ((el.getAttribute('type') || '').toLowerCase() === 'password') return true;
        const autocomplete = (el.getAttribute('autocomplete') || '').toLowerCase();
        if (autocomplete === 'current-password' || autocomplete === 'new-password') return true;
        const nameOrId = ((el.getAttribute('name') || '') + ' ' + (el.getAttribute('id') || '')).toLowerCase();
        return nameOrId.includes('password') || nameOrId.includes('passwd');
    };
    clone.querySelectorAll('input').forEach((el) => {
        if (isPasswordish(el)) {
            el.setAttribute('value', '***MASKED***');
            el.value = '***MASKED***';
        }
    });
    return clone.outerHTML;
}"""


class BaseSiteClient(ABC):
    site_name: ClassVar[str]
    MAX_RESERVATIONS_UNBOUNDED: ClassVar[int] = 500

    def __init__(
        self,
        credentials: Credentials,
        session: BrowserSession,
        settings: Settings,
        *,
        lock: asyncio.Lock | None = None,
    ) -> None:
        self._credentials = credentials
        self._session = session
        self._settings = settings
        self._throttle = LoginThrottle(self.site_name, settings)
        self._last_query_at: float | None = None
        self._last_result: list[Reservation] | None = None
        # Serializes get_reservations() for THIS client instance. Pass the site's
        # SiteRegistry.lock_for(site) when wiring this up (one client instance per
        # site, as the MCP server does) so it's the same lock the registry hands out
        # elsewhere; defaulting to a fresh Lock still protects a single instance
        # against concurrent callers even if the caller doesn't pass one in.
        self._lock = lock if lock is not None else asyncio.Lock()

    # ---- Abstract contract (one implementation per site) ----

    @abstractmethod
    async def login(self, page: Page) -> None:
        """Attempt a login with self._credentials, then return.

        Must never block waiting on a human: raise TwoFactorRequiredError immediately
        once OTP/CAPTCHA/device verification is detected (cli.py is the only caller
        that waits on a person; see docs/DESIGN.md section 10). Sites with a multi-step
        login form (e.g. Naver's id-then-password flow) may implement it by calling
        `_enter_identifier(page)` followed by `_enter_password(page)`.
        """

    @abstractmethod
    async def is_logged_in(self, page: Page) -> bool:
        """Cheaply check whether the current page reflects an authenticated session."""

    @abstractmethod
    async def fetch_reservations(self, page: Page, *, limit: int | None) -> list[Reservation]:
        """Assume an authenticated session; navigate to the reservation list and parse it.

        Must raise LoginRequiredError (never swallow it) if a mid-navigation redirect to
        a login page is detected, including mid-pagination. `limit=None` still must not
        exceed `MAX_RESERVATIONS_UNBOUNDED`.
        """

    # ---- Optional hooks for multi-step logins / paginated listings ----

    async def _enter_identifier(self, page: Page) -> None:
        raise NotImplementedError

    async def _enter_password(self, page: Page) -> None:
        raise NotImplementedError

    async def _has_next_page(self, page: Page) -> bool:
        return False

    async def _go_to_next_page(self, page: Page) -> None:
        raise NotImplementedError

    # ---- Public template method ----

    async def get_reservations(self, *, limit: int | None = None) -> list[Reservation]:
        """login-check -> login() if needed -> fetch_reservations(), with caching.

        This is the *only* place a re-login is retried (at most once) - see
        docs/DESIGN.md sections 6/7. Holds this client's lock for the whole call, so
        two concurrent callers on the same client instance can't both decide the
        cache is stale and both drive the browser/throttle/login at once.
        """
        async with self._lock:
            now = time.monotonic()
            if (
                self._last_result is not None
                and self._last_query_at is not None
                and now - self._last_query_at < self._settings.min_query_interval_sec
            ):
                logger.debug(
                    "Returning cached reservations for site=%s (within TRIPSITE_MIN_QUERY_INTERVAL_SEC)",
                    self.site_name,
                )
                return self._last_result

            page = await self._session.new_page()
            try:
                result = await self._get_reservations_with_retry(page, limit=limit)
            finally:
                await page.close()

            if limit is None and len(result) > self.MAX_RESERVATIONS_UNBOUNDED:
                logger.warning(
                    "site=%s returned %d reservations for an unbounded query; "
                    "truncating to MAX_RESERVATIONS_UNBOUNDED=%d (docs/DESIGN.md section 7)",
                    self.site_name,
                    len(result),
                    self.MAX_RESERVATIONS_UNBOUNDED,
                )
                result = result[: self.MAX_RESERVATIONS_UNBOUNDED]

            self._last_result = result
            self._last_query_at = time.monotonic()
            return result

    async def _get_reservations_with_retry(self, page: Page, *, limit: int | None) -> list[Reservation]:
        if await self.is_logged_in(page):
            try:
                return await self.fetch_reservations(page, limit=limit)
            except LoginRequiredError:
                pass  # session expired mid-call; fall through to the single retry below

        self._throttle.check_and_record()
        await self.login(page)
        # Exactly one retry, from scratch - a second LoginRequiredError propagates.
        return await self.fetch_reservations(page, limit=limit)

    # ---- Shared helpers for subclasses ----

    async def _raise_structure_error(
        self, page: Page, *, label: str, mask_password: bool
    ) -> NoReturn:
        """Capture debug artifacts and raise the resulting ScrapeStructureError.

        Prefer this over calling `_dump_debug_artifacts()` directly: that helper only
        *returns* the exception, so a call site that forgets to `raise` it would
        silently swallow a structural failure.
        """
        raise await self._dump_debug_artifacts(page, label=label, mask_password=mask_password)

    async def _dump_debug_artifacts(
        self, page: Page, *, label: str, mask_password: bool
    ) -> ScrapeStructureError:
        """Save a screenshot + HTML snapshot and return a ScrapeStructureError pointing
        at them. Pass `mask_password=True` from code paths reachable during login() so
        a plaintext password never lands on disk (docs/DESIGN.md section 7).
        """
        debug_dir = self._settings.state_dir / "debug" / self.site_name
        debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        screenshot_path = debug_dir / f"{stamp}_{label}.png"
        html_path = debug_dir / f"{stamp}_{label}.html"

        await page.screenshot(path=str(screenshot_path), full_page=True)
        html = await self._capture_html(page, mask_password=mask_password)
        html_path.write_text(html, encoding="utf-8")
        for path in (screenshot_path, html_path):
            try:
                path.chmod(0o600)
            except OSError:
                pass

        return ScrapeStructureError(
            f"Unexpected page structure for site={self.site_name} (label={label}). "
            f"See {html_path} and {screenshot_path}.",
            screenshot_path=str(screenshot_path),
            html_path=str(html_path),
        )

    async def _capture_html(self, page: Page, *, mask_password: bool) -> str:
        if not mask_password:
            return await page.content()
        try:
            return await page.evaluate(_MASK_PASSWORD_JS)
        except Exception:  # pragma: no cover - best effort, never let this crash the dump
            logger.warning(
                "Password-field masking failed for site=%s; dropping raw HTML dump",
                self.site_name,
            )
            return "<!-- debug HTML dropped: password masking failed -->"
