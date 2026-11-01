"""Process-wide ownership of per-site BrowserSession + asyncio.Lock, and the site-name
-> client-class mapping. See docs/DESIGN.md section 6 ("소유권") and section 9.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable

from .browser import BrowserSession
from .config import SUPPORTED_SITES, Settings

if TYPE_CHECKING:
    from .sites.base import BaseSiteClient


class SiteRegistry:
    """Owns one BrowserSession and one asyncio.Lock per site for the life of the
    process. Locks are independent per site: concurrent calls for *different* sites
    never block each other, only concurrent calls for the *same* site are serialized
    (so `get_all_reservations` can run sites in parallel while still protecting each
    site's single BrowserContext from concurrent use).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sessions: dict[str, BrowserSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def lock_for(self, site: str) -> asyncio.Lock:
        if site not in self._locks:
            self._locks[site] = asyncio.Lock()
        return self._locks[site]

    async def session_for(self, site: str) -> BrowserSession:
        session = self._sessions.get(site)
        if session is None:
            session = BrowserSession(
                site,
                headless=self._settings.headless,
                state_dir=self._settings.state_dir,
                nav_timeout_ms=self._settings.nav_timeout_ms,
            )
            await session.start()
            self._sessions[site] = session
        return session

    async def close_all(self) -> None:
        for session in self._sessions.values():
            await session.close()
        self._sessions.clear()
        self._locks.clear()


_SITE_CLIENT_FACTORIES: dict[str, Callable[..., "BaseSiteClient"]] = {}


def register_site_client(site: str, factory: Callable[..., "BaseSiteClient"]) -> None:
    """Called by each concrete adapter module at import time (sites/yanolja.py, etc.)."""
    if site not in SUPPORTED_SITES:
        raise ValueError(f"Unknown site: {site!r}. Supported sites: {SUPPORTED_SITES}")
    _SITE_CLIENT_FACTORIES[site] = factory


def client_factory_for(site: str) -> Callable[..., "BaseSiteClient"]:
    try:
        return _SITE_CLIENT_FACTORIES[site]
    except KeyError:
        raise ValueError(
            f"No client registered for site '{site}'. Supported sites: {SUPPORTED_SITES}. "
            "Make sure tripsite_api.sites has been imported so adapters register "
            "themselves."
        ) from None
