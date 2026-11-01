"""`tripsite` command line tool. See docs/DESIGN.md section 10.

`login` is the only command that needs a person: it opens a visible browser so the user
can complete OTP/CAPTCHA/device checks that the automated flow can't. The other commands
reuse the cached session and never open a window.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from . import sites  # noqa: F401  (import registers every adapter with the registry)
from .browser import BrowserSession
from .config import SUPPORTED_SITES, Settings, credentials_for_site
from .errors import TripsiteError, TwoFactorRequiredError
from .registry import client_factory_for
from .service import ReservationService, error_payload


async def _login(site: str) -> int:
    settings = Settings.from_env()
    credentials = credentials_for_site(site)
    session = BrowserSession(
        site,
        headless=False,
        state_dir=settings.state_dir,
        nav_timeout_ms=settings.nav_timeout_ms,
    )
    async with session:
        client = client_factory_for(site)(credentials, session, settings)
        page = await session.new_page()
        try:
            await client.login_with_throttle(page)
        except TwoFactorRequiredError as exc:
            print(f"{exc}", file=sys.stderr)
            while True:
                await asyncio.to_thread(input, "Finish the verification in the browser, then press Enter... ")
                if await client.is_logged_in(page):
                    break
                print("Still not logged in. Finish the verification in the browser and press Enter again.")
        finally:
            await page.close()
        await session.save_storage_state()
    print(f"Logged in to {site}. Session saved under {settings.state_dir}.")
    return 0


async def _check(site: str) -> int:
    service = ReservationService(Settings.from_env())
    try:
        logged_in = await service.check_login_status(site)
    finally:
        await service.close()
    print(json.dumps({"site": site, "logged_in": logged_in}, ensure_ascii=False))
    return 0 if logged_in else 1


async def _reservations(site: str, limit: int | None) -> int:
    service = ReservationService(Settings.from_env())
    try:
        reservations = await service.get_reservations(site, limit=limit)
    finally:
        await service.close()
    print(json.dumps({"site": site, "reservations": reservations}, ensure_ascii=False, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tripsite", description="Look up your own reservations.")
    commands = parser.add_subparsers(dest="command", required=True)

    login = commands.add_parser("login", help="log in once in a visible browser and cache the session")
    login.add_argument("site", choices=SUPPORTED_SITES)

    check = commands.add_parser("check", help="report whether the cached session is still logged in")
    check.add_argument("site", choices=SUPPORTED_SITES)

    reservations = commands.add_parser("reservations", help="print reservations for one site as JSON")
    reservations.add_argument("site", choices=SUPPORTED_SITES)
    reservations.add_argument("--limit", type=int, default=None)

    commands.add_parser("serve", help="run the MCP server over stdio")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "serve":
        from .mcp_server import run_server

        run_server()
        return 0

    try:
        if args.command == "login":
            return asyncio.run(_login(args.site))
        if args.command == "check":
            return asyncio.run(_check(args.site))
        return asyncio.run(_reservations(args.site, args.limit))
    except (TripsiteError, ValueError) as exc:
        print(json.dumps(error_payload(exc, site=getattr(args, "site", None)), ensure_ascii=False), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
