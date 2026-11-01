"""Exercises BaseSiteClient's template-method contract (docs/DESIGN.md section 7):
single-retry re-login, query-result caching, and password-masking in debug dumps.
A fake adapter stands in for a real site so this test doesn't need network access.
"""

from pathlib import Path

import pytest

from tripsite_api.browser import BrowserSession
from tripsite_api.config import Credentials, Settings
from tripsite_api.errors import LoginRequiredError
from tripsite_api.models import Reservation, ReservationCategory, ReservationStatus
from tripsite_api.sites.base import BaseSiteClient

_FAKE_RESERVATION = Reservation(
    site="fake",
    reservation_id="R-1",
    category=ReservationCategory.LODGING,
    property_name="테스트 호텔",
    room_name=None,
    check_in=None,
    check_out=None,
    visit_at=None,
    status=ReservationStatus.CONFIRMED,
    status_label_raw="예약완료",
    price_amount=None,
)


class _FakeClient(BaseSiteClient):
    site_name = "fake"

    def __init__(self, *args, initially_logged_in: bool = True, fail_fetch_times: int = 0, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.login_calls = 0
        self.fetch_calls = 0
        self.logged_in = initially_logged_in
        self.remaining_failures = fail_fetch_times

    async def login(self, page) -> None:
        self.login_calls += 1
        self.logged_in = True

    async def is_logged_in(self, page) -> bool:
        return self.logged_in

    async def fetch_reservations(self, page, *, limit):
        self.fetch_calls += 1
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            raise LoginRequiredError("fake session expired")
        return [_FAKE_RESERVATION]


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    defaults = dict(
        state_dir=tmp_path,
        headless=True,
        nav_timeout_ms=15_000,
        min_login_interval_sec=0,
        max_login_attempts_per_day=5,
        min_query_interval_sec=30,
    )
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


_CREDENTIALS = Credentials(site="fake", identifier="id", password="pw")


@pytest.fixture
async def session(tmp_path: Path):
    s = BrowserSession("fake", headless=True, state_dir=tmp_path)
    async with s:
        yield s


async def test_logs_in_when_no_cached_session(tmp_path: Path, session: BrowserSession) -> None:
    client = _FakeClient(
        _CREDENTIALS, session, _settings(tmp_path), initially_logged_in=False
    )

    result = await client.get_reservations()

    assert result == [_FAKE_RESERVATION]
    assert client.login_calls == 1
    assert client.fetch_calls == 1


async def test_retries_once_after_mid_call_session_expiry(tmp_path: Path, session: BrowserSession) -> None:
    client = _FakeClient(
        _CREDENTIALS, session, _settings(tmp_path), initially_logged_in=True, fail_fetch_times=1
    )

    result = await client.get_reservations()

    assert result == [_FAKE_RESERVATION]
    assert client.login_calls == 1
    assert client.fetch_calls == 2


async def test_propagates_error_when_retry_also_fails(tmp_path: Path, session: BrowserSession) -> None:
    client = _FakeClient(
        _CREDENTIALS, session, _settings(tmp_path), initially_logged_in=True, fail_fetch_times=99
    )

    with pytest.raises(LoginRequiredError):
        await client.get_reservations()

    # Exactly one retry: the initial attempt plus one re-login attempt, never more.
    assert client.login_calls == 1
    assert client.fetch_calls == 2


async def test_caches_result_within_min_query_interval(tmp_path: Path, session: BrowserSession) -> None:
    client = _FakeClient(
        _CREDENTIALS,
        session,
        _settings(tmp_path, min_query_interval_sec=60),
        initially_logged_in=True,
    )

    first = await client.get_reservations()
    second = await client.get_reservations()

    assert first == second
    assert client.fetch_calls == 1  # second call served from cache, not re-fetched


async def test_does_not_cache_once_interval_elapses(tmp_path: Path, session: BrowserSession) -> None:
    client = _FakeClient(
        _CREDENTIALS,
        session,
        _settings(tmp_path, min_query_interval_sec=0),
        initially_logged_in=True,
    )

    await client.get_reservations()
    await client.get_reservations()

    assert client.fetch_calls == 2


async def test_default_pagination_hooks(tmp_path: Path, session: BrowserSession) -> None:
    client = _FakeClient(_CREDENTIALS, session, _settings(tmp_path))
    page = await session.new_page()
    try:
        assert await client._has_next_page(page) is False
        with pytest.raises(NotImplementedError):
            await client._go_to_next_page(page)
    finally:
        await page.close()


async def test_dump_debug_artifacts_masks_password_field(tmp_path: Path, session: BrowserSession) -> None:
    client = _FakeClient(_CREDENTIALS, session, _settings(tmp_path))
    page = await session.new_page()
    try:
        await page.set_content(
            '<html><body><input type="password" value="supersecret123"></body></html>'
        )
        error = await client._dump_debug_artifacts(page, label="login", mask_password=True)

        assert error.html_path is not None
        assert error.screenshot_path is not None
        html = Path(error.html_path).read_text(encoding="utf-8")
        assert "supersecret123" not in html
        assert Path(error.screenshot_path).exists()
    finally:
        await page.close()


async def test_dump_debug_artifacts_without_masking_includes_password(
    tmp_path: Path, session: BrowserSession
) -> None:
    client = _FakeClient(_CREDENTIALS, session, _settings(tmp_path))
    page = await session.new_page()
    try:
        await page.set_content(
            '<html><body><input type="password" value="supersecret123"></body></html>'
        )
        error = await client._dump_debug_artifacts(page, label="listing", mask_password=False)

        html = Path(error.html_path).read_text(encoding="utf-8")
        assert "supersecret123" in html  # demonstrates masking is doing real work above
    finally:
        await page.close()
