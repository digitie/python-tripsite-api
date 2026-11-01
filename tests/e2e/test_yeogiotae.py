"""Live e2e against the real www.yeogi.com / platform.yeogi.com (docs/DESIGN.md
section 11). Skipped entirely unless YEOGIOTAE_ID/YEOGIOTAE_PW are set.
"""

import os

import pytest

from tripsite_api.browser import BrowserSession
from tripsite_api.config import Settings, credentials_for_site
from tripsite_api.errors import ScrapeStructureError
from tripsite_api.sites.yeogiotae import YeogiotaeClient

pytestmark = pytest.mark.skipif(
    not (os.environ.get("YEOGIOTAE_ID") and os.environ.get("YEOGIOTAE_PW")),
    reason="requires real YEOGIOTAE_ID/YEOGIOTAE_PW credentials",
)


@pytest.fixture
async def yeogiotae_client(tmp_path):
    settings = Settings(
        state_dir=tmp_path,
        headless=True,
        nav_timeout_ms=20_000,
        min_login_interval_sec=0,
        max_login_attempts_per_day=5,
        min_query_interval_sec=0,
    )
    session = BrowserSession(
        "yeogiotae", headless=True, state_dir=settings.state_dir, nav_timeout_ms=settings.nav_timeout_ms
    )
    async with session:
        credentials = credentials_for_site("yeogiotae")
        yield YeogiotaeClient(credentials, session, settings)


async def test_login_succeeds_with_real_credentials(yeogiotae_client) -> None:
    page = await yeogiotae_client._session.new_page()
    try:
        await yeogiotae_client.login(page)
        assert await yeogiotae_client.is_logged_in(page) is True
    finally:
        await page.close()


async def test_get_reservations_is_a_known_unfinished_gap(yeogiotae_client) -> None:
    """TODO: once sites/yeogiotae.py's fetch_reservations() can locate the
    reservation-history entry point from a logged-in session and has a real parser,
    replace this with an assertion on the returned Reservation list and delete this
    test.
    """
    with pytest.raises(ScrapeStructureError):
        await yeogiotae_client.get_reservations(limit=5)
