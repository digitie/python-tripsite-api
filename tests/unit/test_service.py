"""ReservationService behaviour with fake clients and credentials - no real site is contacted.

The registry still starts a real headless Chromium for sessions it hands out, which is
cheap here and keeps the browser lifecycle honest.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from fakes import FakeClient, credentials_loader, make_settings
from tripsite_api.errors import (
    CredentialsNotConfiguredError,
    LoginRequiredError,
    ScrapeStructureError,
    TripsiteError,
    UnsupportedSiteError,
)
from tripsite_api.service import ReservationService, error_payload


@pytest.fixture
async def make_service(tmp_path: Path):
    services: list[ReservationService] = []

    def build(configured: set[str], behaviours: dict[str, dict[str, Any]] | None = None) -> ReservationService:
        behaviours = behaviours or {}
        clients: dict[str, FakeClient] = {}

        def factory(credentials, session, settings, *, lock=None):
            client = FakeClient(credentials.site, behaviours.get(credentials.site, {}))
            clients[credentials.site] = client
            return client

        service = ReservationService(
            make_settings(tmp_path), credentials_loader=credentials_loader(configured), client_factory=factory
        )
        service.clients_seen = clients  # type: ignore[attr-defined]
        services.append(service)
        return service

    yield build
    for service in services:
        await service.close()


def test_list_supported_sites_returns_all_three(make_service) -> None:
    service = make_service(set())
    assert service.list_supported_sites() == ["yanolja", "yeogiotae", "naver"]


async def test_get_reservations_rejects_unknown_site(make_service) -> None:
    service = make_service({"naver"})
    with pytest.raises(UnsupportedSiteError):
        await service.get_reservations("expedia")


@pytest.mark.parametrize("bad_limit", [0, -1, 501])
async def test_get_reservations_rejects_out_of_range_limit(make_service, bad_limit) -> None:
    service = make_service({"naver"})
    with pytest.raises(ValueError):
        await service.get_reservations("naver", limit=bad_limit)


async def test_get_reservations_without_credentials_fails_before_starting_a_browser(make_service) -> None:
    service = make_service(set())
    with pytest.raises(CredentialsNotConfiguredError):
        await service.get_reservations("naver")
    assert service._registry._sessions == {}


async def test_get_reservations_returns_serialized_reservations(make_service) -> None:
    service = make_service({"yanolja"})
    result = await service.get_reservations("yanolja", limit=3)
    assert result[0]["site"] == "yanolja"
    assert result[0]["category"] == "lodging"
    assert service.clients_seen["yanolja"].limits_seen == [3]


async def test_get_all_reservations_skips_unconfigured_sites_and_isolates_failures(make_service) -> None:
    service = make_service(
        {"yanolja", "naver"},
        behaviours={"naver": {"error": LoginRequiredError("session expired")}},
    )

    results = await service.get_all_reservations()

    assert results["yanolja"]["status"] == "ok"
    assert results["yanolja"]["reservations"][0]["site"] == "yanolja"
    assert results["naver"]["status"] == "error"
    assert results["naver"]["error_type"] == "LoginRequiredError"
    assert results["yeogiotae"] == {"status": "skipped", "skipped_reason": "credentials_not_configured"}


async def test_check_login_status_reports_the_probe_result(make_service) -> None:
    service = make_service({"naver"}, behaviours={"naver": {"logged_in": False}})
    assert await service.check_login_status("naver") is False


async def test_check_login_status_rejects_unknown_site(make_service) -> None:
    service = make_service({"naver"})
    with pytest.raises(UnsupportedSiteError):
        await service.check_login_status("expedia")


def test_error_payload_passes_domain_messages_through() -> None:
    payload = error_payload(LoginRequiredError("session expired"), site="naver")
    assert payload == {"error_type": "LoginRequiredError", "message": "session expired", "site": "naver"}


def test_error_payload_includes_debug_artifact_paths_for_structure_errors() -> None:
    exc = ScrapeStructureError("layout changed", screenshot_path="/tmp/s.png", html_path="/tmp/s.html")
    payload = error_payload(exc, site="yanolja")
    assert payload["screenshot_path"] == "/tmp/s.png"
    assert payload["html_path"] == "/tmp/s.html"


def test_error_payload_hides_message_of_unexpected_exceptions() -> None:
    """An arbitrary exception could carry anything, including a password, in its text."""
    payload = error_payload(RuntimeError("boom with password=hunter2"), site="naver")
    assert payload["error_type"] == "UnexpectedError"
    assert "hunter2" not in str(payload)
    assert "boom" not in str(payload)


def test_error_payload_passes_value_errors_through_for_argument_problems() -> None:
    payload = error_payload(ValueError("limit must be between 1 and 500"))
    assert payload["error_type"] == "ValueError"
    assert "limit" in payload["message"]


def test_tripsite_error_base_is_handled_as_domain_error() -> None:
    assert error_payload(TripsiteError("generic"))["error_type"] == "TripsiteError"


async def test_concurrent_first_calls_for_one_site_share_a_single_client(tmp_path: Path) -> None:
    """Regression: two first calls that both await the browser start must not each build a
    client - the second would discard the first's query cache."""
    built: list[str] = []

    def factory(credentials, session, settings, *, lock=None):
        built.append(credentials.site)
        return FakeClient(credentials.site, {})

    service = ReservationService(
        make_settings(tmp_path), credentials_loader=credentials_loader({"naver"}), client_factory=factory
    )
    try:
        await asyncio.gather(
            service.check_login_status("naver"),
            service.get_reservations("naver"),
        )
    finally:
        await service.close()

    assert built == ["naver"]
