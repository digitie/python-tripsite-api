"""Live e2e against the real nid.naver.com / m.place.naver.com (docs/DESIGN.md
section 11). Skipped entirely unless NAVER_ID/NAVER_PW are set.

Naver is known to challenge unfamiliar automation with device/location
re-verification or CAPTCHA - if that happens, login() raises TwoFactorRequiredError
rather than this test failing outright; see sites/naver.py's module docstring.
"""

import os

import pytest

from tripsite_api.browser import BrowserSession
from tripsite_api.config import Settings, credentials_for_site
from tripsite_api.errors import ScrapeStructureError
from tripsite_api.sites.naver import NaverClient

pytestmark = pytest.mark.skipif(
    not (os.environ.get("NAVER_ID") and os.environ.get("NAVER_PW")),
    reason="requires real NAVER_ID/NAVER_PW credentials",
)


@pytest.fixture
async def naver_client(tmp_path):
    settings = Settings(
        state_dir=tmp_path,
        headless=True,
        nav_timeout_ms=20_000,
        min_login_interval_sec=0,
        max_login_attempts_per_day=5,
        min_query_interval_sec=0,
    )
    session = BrowserSession(
        "naver", headless=True, state_dir=settings.state_dir, nav_timeout_ms=settings.nav_timeout_ms
    )
    async with session:
        credentials = credentials_for_site("naver")
        yield NaverClient(credentials, session, settings)


async def test_login_succeeds_with_real_credentials(naver_client) -> None:
    page = await naver_client._session.new_page()
    try:
        await naver_client.login(page)
        assert await naver_client.is_logged_in(page) is True
    finally:
        await page.close()


async def test_get_reservations_is_a_known_unfinished_gap(naver_client) -> None:
    """TODO: once sites/naver.py's fetch_reservations() has a real parser (needs a
    logged-in session to observe the m.place.naver.com/my/timeline markup), replace
    this with an assertion on the returned Reservation list and delete this test.
    """
    with pytest.raises(ScrapeStructureError):
        await naver_client.get_reservations(limit=5)
