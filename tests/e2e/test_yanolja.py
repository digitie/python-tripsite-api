"""Live e2e against the real nol.yanolja.com (docs/DESIGN.md section 11).

Skipped entirely unless YANOLJA_ID/YANOLJA_PW are set - this hits the real site with
a real account, so it is NOT run in CI, only as a manual PR gate before merging
sites/yanolja.py changes.
"""

import os

import pytest

from tripsite_api.browser import BrowserSession
from tripsite_api.config import Settings, credentials_for_site
from tripsite_api.errors import ScrapeStructureError
from tripsite_api.sites.yanolja import YanoljaClient

pytestmark = pytest.mark.skipif(
    not (os.environ.get("YANOLJA_ID") and os.environ.get("YANOLJA_PW")),
    reason="requires real YANOLJA_ID/YANOLJA_PW credentials",
)


@pytest.fixture
async def yanolja_client(tmp_path):
    settings = Settings(
        state_dir=tmp_path,
        headless=True,
        nav_timeout_ms=20_000,
        min_login_interval_sec=0,
        max_login_attempts_per_day=5,
        min_query_interval_sec=0,
    )
    session = BrowserSession(
        "yanolja", headless=True, state_dir=settings.state_dir, nav_timeout_ms=settings.nav_timeout_ms
    )
    async with session:
        credentials = credentials_for_site("yanolja")
        yield YanoljaClient(credentials, session, settings)


async def test_login_succeeds_with_real_credentials(yanolja_client) -> None:
    page = await yanolja_client._session.new_page()
    try:
        await yanolja_client.login(page)
        assert await yanolja_client.is_logged_in(page) is True
    finally:
        await page.close()


async def test_get_reservations_is_a_known_unfinished_gap(yanolja_client) -> None:
    """TODO: once sites/yanolja.py's fetch_reservations() has a real parser (needs a
    logged-in session to observe the markup), replace this with an assertion on the
    returned Reservation list and delete this test.
    """
    with pytest.raises(ScrapeStructureError):
        await yanolja_client.get_reservations(limit=5)
