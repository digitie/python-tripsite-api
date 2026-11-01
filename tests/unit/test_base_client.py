"""Exercises BaseSiteClient's template-method contract (docs/DESIGN.md section 7):
single-retry re-login, query-result caching, and password-masking in debug dumps.
A fake adapter stands in for a real site so this test doesn't need network access.
"""

import asyncio
from pathlib import Path

import pytest

from tripsite_api.browser import BrowserSession
from tripsite_api.config import Credentials, SecretStr, Settings
from tripsite_api.errors import LoginRequiredError, ScrapeStructureError
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
        self._many_results: list | None = None
        self.fetch_delay_seconds = 0.0
        self._concurrent_fetches = 0
        self.max_concurrent_fetches_seen = 0

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

        self._concurrent_fetches += 1
        self.max_concurrent_fetches_seen = max(self.max_concurrent_fetches_seen, self._concurrent_fetches)
        try:
            if self.fetch_delay_seconds:
                await asyncio.sleep(self.fetch_delay_seconds)
            return list(self._many_results) if self._many_results is not None else [_FAKE_RESERVATION]
        finally:
            self._concurrent_fetches -= 1


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


_CREDENTIALS = Credentials(site="fake", identifier="id", password=SecretStr("pw"))


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


async def test_cache_is_not_shared_across_different_limits(tmp_path: Path, session: BrowserSession) -> None:
    """Regression: a limit=5 result must not be served to a later limit=None caller
    within the same query interval - that would silently return a truncated list."""
    client = _FakeClient(
        _CREDENTIALS,
        session,
        _settings(tmp_path, min_query_interval_sec=60),
        initially_logged_in=True,
    )

    await client.get_reservations(limit=5)
    await client.get_reservations(limit=None)

    assert client.fetch_calls == 2


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


async def test_dump_debug_artifacts_masks_password_synced_to_attribute_by_site_js(
    tmp_path: Path, session: BrowserSession
) -> None:
    """Realistic case flagged in review: a site's own JS mirrors the live input value
    onto the `value` attribute as the user types (some custom components do this -
    page.fill() alone normally does NOT get reflected into the attribute at all).
    """
    client = _FakeClient(_CREDENTIALS, session, _settings(tmp_path))
    page = await session.new_page()
    try:
        await page.set_content(
            '<html><body>'
            '<input id="pw" type="password" autocomplete="current-password">'
            "<script>"
            "document.getElementById('pw').addEventListener('input', (e) => {"
            "  e.target.setAttribute('value', e.target.value);"
            "});"
            "</script>"
            "</body></html>"
        )
        await page.fill("#pw", "supersecret123")

        error = await client._dump_debug_artifacts(page, label="login", mask_password=True)

        html = Path(error.html_path).read_text(encoding="utf-8")
        assert "supersecret123" not in html
    finally:
        await page.close()


async def test_dump_debug_artifacts_masks_password_field_identified_by_name_only(
    tmp_path: Path, session: BrowserSession
) -> None:
    """A show-password toggle can switch `type` to "text" before a dump is taken; the
    masking selector also matches on name/id/autocomplete so it isn't solely
    dependent on `type="password"` still being present.
    """
    client = _FakeClient(_CREDENTIALS, session, _settings(tmp_path))
    page = await session.new_page()
    try:
        await page.set_content(
            '<html><body>'
            '<input name="user_password" type="text" value="supersecret123">'
            "</body></html>"
        )
        error = await client._dump_debug_artifacts(page, label="login", mask_password=True)

        html = Path(error.html_path).read_text(encoding="utf-8")
        assert "supersecret123" not in html
    finally:
        await page.close()


async def test_raise_structure_error_raises_the_captured_error(
    tmp_path: Path, session: BrowserSession
) -> None:
    client = _FakeClient(_CREDENTIALS, session, _settings(tmp_path))
    page = await session.new_page()
    try:
        await page.set_content("<html><body>unexpected layout</body></html>")
        with pytest.raises(ScrapeStructureError) as exc_info:
            await client._raise_structure_error(page, label="listing", mask_password=False)
        assert exc_info.value.html_path is not None
    finally:
        await page.close()


async def test_get_reservations_truncates_unbounded_query_to_hard_cap(
    tmp_path: Path, session: BrowserSession
) -> None:
    client = _FakeClient(_CREDENTIALS, session, _settings(tmp_path), initially_logged_in=True)
    client.MAX_RESERVATIONS_UNBOUNDED = 2  # instance override, cheaper than 500 real rows
    client._many_results = [_FAKE_RESERVATION] * 5

    result = await client.get_reservations(limit=None)

    assert len(result) == 2


async def test_get_reservations_serializes_concurrent_callers_on_same_instance(
    tmp_path: Path, session: BrowserSession
) -> None:
    """Without the instance lock, two concurrent callers could both see a stale cache
    and both drive fetch_reservations()/login() at once (review finding)."""
    client = _FakeClient(
        _CREDENTIALS, session, _settings(tmp_path, min_query_interval_sec=0), initially_logged_in=True
    )
    client.fetch_delay_seconds = 0.05

    first, second = await asyncio.gather(client.get_reservations(), client.get_reservations())

    assert first == [_FAKE_RESERVATION]
    assert second == [_FAKE_RESERVATION]
    assert client.max_concurrent_fetches_seen == 1


async def test_explicit_limit_is_enforced_even_if_the_adapter_returns_more(
    tmp_path: Path, session: BrowserSession
) -> None:
    """Regression guard for docs/DESIGN.md section 7: the base class enforces the cap
    rather than trusting each adapter to honour `limit`."""
    client = _FakeClient(_CREDENTIALS, session, _settings(tmp_path), initially_logged_in=True)
    client._many_results = [_FAKE_RESERVATION] * 5

    result = await client.get_reservations(limit=2)

    assert len(result) == 2


async def test_successful_login_is_persisted_immediately(tmp_path: Path, session: BrowserSession) -> None:
    """A login done by the server must reach disk now, so a separate `tripsite check` sees
    it without waiting for the server to shut down."""
    client = _FakeClient(_CREDENTIALS, session, _settings(tmp_path), initially_logged_in=False)
    page = await session.new_page()
    try:
        await client.login_with_throttle(page)
    finally:
        await page.close()

    assert (tmp_path / "storage_state_fake.json").exists()
