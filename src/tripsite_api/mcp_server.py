"""MCP server exposing reservation lookups to AI agents over stdio. See docs/DESIGN.md section 9.

Every tool returns a CallToolResult directly so failures can carry `isError=True` and a
machine-readable `structuredContent` payload (`error_type`, `message`, `site`). Returning a
plain value or raising from a tool would make FastMCP prefix the message with
"Error executing tool ...", forcing agents to parse text instead of fields.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from .browser import chromium_launch_error
from .config import Settings
from .service import ReservationService, error_payload

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "Read-only lookups of the user's own reservations on Yanolja, Yeogiotae and Naver Booking. "
    "Sessions are cached; a first call for a site may take several seconds while a browser logs in. "
    "If a result has error_type TwoFactorRequiredError, the user must complete verification once "
    "by running `tripsite login <site>` on this machine; do not retry automatically."
)


def _result(payload: dict[str, Any], *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structuredContent=payload,
        isError=is_error,
    )


def build_server(service: ReservationService) -> FastMCP:
    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await service.close()

    mcp = FastMCP("tripsite", instructions=INSTRUCTIONS, lifespan=lifespan)

    @mcp.tool()
    def list_supported_sites() -> CallToolResult:
        """List the sites this server can query: yanolja, yeogiotae, naver."""
        return _result({"sites": service.list_supported_sites()})

    @mcp.tool()
    async def get_reservations(site: str, limit: int | None = None) -> CallToolResult:
        """Get the logged-in user's reservations for one site.

        Args:
            site: One of yanolja, yeogiotae, naver.
            limit: Optional maximum number of reservations to return (1-500). Omit for all
                reservations, which is capped at 500.
        """
        try:
            reservations = await service.get_reservations(site, limit=limit)
        except Exception as exc:
            return _result(error_payload(exc, site=site), is_error=True)
        return _result({"site": site, "reservations": reservations})

    @mcp.tool()
    async def get_all_reservations(limit: int | None = None) -> CallToolResult:
        """Get reservations from every site that has credentials configured.

        Sites without credentials are reported as skipped; one site failing does not stop
        the others. Each entry under `results` has a `status` of ok, skipped, or error.

        Args:
            limit: Optional per-site maximum (1-500).
        """
        try:
            results = await service.get_all_reservations(limit=limit)
        except Exception as exc:
            return _result(error_payload(exc), is_error=True)
        return _result({"results": results})

    @mcp.tool()
    async def check_login_status(site: str) -> CallToolResult:
        """Check whether the cached session for one site is still logged in.

        Does not perform a login, so it never consumes a login attempt.

        Args:
            site: One of yanolja, yeogiotae, naver.
        """
        try:
            logged_in = await service.check_login_status(site)
        except Exception as exc:
            return _result(error_payload(exc, site=site), is_error=True)
        return _result({"site": site, "logged_in": logged_in})

    return mcp


def run_server() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    launch_error = chromium_launch_error(headless=True)
    if launch_error is not None:
        sys.stderr.write(
            "Chromium cannot be launched for Playwright "
            f"({launch_error}). Run `playwright install chromium` once, then start the server again.\n"
        )
        raise SystemExit(1)

    service = ReservationService(Settings.from_env())
    build_server(service).run(transport="stdio")
