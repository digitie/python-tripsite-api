"""Fakes shared by the service and MCP-layer unit tests. No real site is contacted."""

from pathlib import Path
from typing import Any

from tripsite_api.config import Credentials, SecretStr, Settings
from tripsite_api.errors import CredentialsNotConfiguredError
from tripsite_api.models import Reservation, ReservationCategory, ReservationStatus


def make_reservation(site: str) -> Reservation:
    return Reservation(
        site=site,
        reservation_id=f"{site}-1",
        category=ReservationCategory.LODGING,
        property_name="테스트 숙소",
        room_name=None,
        check_in=None,
        check_out=None,
        visit_at=None,
        status=ReservationStatus.CONFIRMED,
        status_label_raw="예약완료",
        price_amount=None,
    )


class FakeClient:
    """Stands in for a BaseSiteClient. `behaviour` may set `error` (raised from
    get_reservations) or `logged_in` (returned by is_logged_in)."""

    def __init__(self, site: str, behaviour: dict[str, Any]) -> None:
        self.site = site
        self.behaviour = behaviour
        self.limits_seen: list[int | None] = []

    async def get_reservations(self, *, limit: int | None = None) -> list[Reservation]:
        self.limits_seen.append(limit)
        error = self.behaviour.get("error")
        if error is not None:
            raise error
        return [make_reservation(self.site)]

    async def is_logged_in(self, page: Any) -> bool:
        return self.behaviour.get("logged_in", True)


def make_settings(state_dir: Path) -> Settings:
    return Settings(
        state_dir=state_dir,
        headless=True,
        nav_timeout_ms=15_000,
        min_login_interval_sec=60,
        max_login_attempts_per_day=5,
        min_query_interval_sec=0,
    )


def credentials_loader(configured: set[str]):
    def load(site: str) -> Credentials:
        if site not in configured:
            raise CredentialsNotConfiguredError(f"no credentials for {site}")
        return Credentials(site=site, identifier="id", password=SecretStr("pw"))

    return load
