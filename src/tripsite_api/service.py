"""Transport-agnostic reservation service shared by the MCP server and the CLI.

The MCP tools in mcp_server.py are thin wrappers over this class, so the behaviour
(credential gating, per-site error isolation, the login-status probe) is testable
without standing up an MCP transport. See docs/DESIGN.md sections 6 and 9.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from . import sites  # noqa: F401  (import registers every adapter with the registry)
from .config import SUPPORTED_SITES, Credentials, Settings, credentials_for_site
from .errors import CredentialsNotConfiguredError, ScrapeStructureError, TripsiteError, UnsupportedSiteError
from .registry import SiteRegistry, client_factory_for
from .sites.base import BaseSiteClient

logger = logging.getLogger(__name__)

CredentialsLoader = Callable[[str], Credentials]
ClientFactory = Callable[..., BaseSiteClient]

_UNEXPECTED_ERROR_MESSAGE = "unexpected internal error; see the server log for details"


def error_payload(exc: BaseException, site: str | None = None) -> dict[str, Any]:
    """Structured description of a failure, safe to return to an agent.

    Only domain errors and argument-validation ValueErrors pass their message through.
    Anything else is replaced with a generic message so an unexpected exception can't
    leak arbitrary text (and, in the worst case, something sensitive) into a tool result.
    """
    if isinstance(exc, (TripsiteError, ValueError)):
        payload: dict[str, Any] = {
            "error_type": type(exc).__name__,
            "message": str(exc),
            "site": site,
        }
        if isinstance(exc, ScrapeStructureError):
            payload["screenshot_path"] = exc.screenshot_path
            payload["html_path"] = exc.html_path
        return payload
    logger.error("unexpected error while handling site=%s", site, exc_info=exc)
    return {"error_type": "UnexpectedError", "message": _UNEXPECTED_ERROR_MESSAGE, "site": site}


class ReservationService:
    def __init__(
        self,
        settings: Settings,
        *,
        credentials_loader: CredentialsLoader = credentials_for_site,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._settings = settings
        self._registry = SiteRegistry(settings)
        self._credentials_loader = credentials_loader
        self._client_factory = client_factory
        self._clients: dict[str, BaseSiteClient] = {}

    @staticmethod
    def _validate_site(site: str) -> None:
        if site not in SUPPORTED_SITES:
            raise UnsupportedSiteError(f"Unknown site '{site}'. Supported sites: {', '.join(SUPPORTED_SITES)}.")

    @staticmethod
    def _validate_limit(limit: int | None) -> None:
        if limit is None:
            return
        if not 1 <= limit <= BaseSiteClient.MAX_RESERVATIONS_UNBOUNDED:
            raise ValueError(
                f"limit must be between 1 and {BaseSiteClient.MAX_RESERVATIONS_UNBOUNDED}, got {limit}."
            )

    def _has_credentials(self, site: str) -> bool:
        try:
            self._credentials_loader(site)
        except CredentialsNotConfiguredError:
            return False
        return True

    async def _client_for(self, site: str) -> BaseSiteClient:
        client = self._clients.get(site)
        if client is not None:
            return client
        credentials = self._credentials_loader(site)
        session = await self._registry.session_for(site)
        factory = self._client_factory or client_factory_for(site)
        client = factory(credentials, session, self._settings, lock=self._registry.lock_for(site))
        self._clients[site] = client
        return client

    def list_supported_sites(self) -> list[str]:
        return list(SUPPORTED_SITES)

    async def get_reservations(self, site: str, limit: int | None = None) -> list[dict[str, Any]]:
        self._validate_site(site)
        self._validate_limit(limit)
        client = await self._client_for(site)
        reservations = await client.get_reservations(limit=limit)
        return [reservation.to_dict() for reservation in reservations]

    async def get_all_reservations(self, limit: int | None = None) -> dict[str, dict[str, Any]]:
        self._validate_limit(limit)
        outcomes = await asyncio.gather(*(self._one_site_outcome(site, limit) for site in SUPPORTED_SITES))
        return dict(zip(SUPPORTED_SITES, outcomes, strict=True))

    async def _one_site_outcome(self, site: str, limit: int | None) -> dict[str, Any]:
        if not self._has_credentials(site):
            return {"status": "skipped", "skipped_reason": "credentials_not_configured"}
        try:
            return {"status": "ok", "reservations": await self.get_reservations(site, limit=limit)}
        except Exception as exc:
            return {"status": "error", **error_payload(exc, site=site)}

    async def check_login_status(self, site: str) -> bool:
        """Probe whether the cached session is still authenticated, without logging in.

        Holds the site's lock so it can't interleave with a concurrent
        get_reservations() that may be mid-login on the same browser context.
        """
        self._validate_site(site)
        client = await self._client_for(site)
        session = await self._registry.session_for(site)
        async with self._registry.lock_for(site):
            page = await session.new_page()
            try:
                return await client.is_logged_in(page)
            finally:
                await page.close()

    async def close(self) -> None:
        await self._registry.close_all()
        self._clients.clear()
