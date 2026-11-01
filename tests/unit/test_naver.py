"""Unit-level checks for the Naver adapter's testable parts: the host-based
is_logged_in predicate and login-form selectors, verified against an HTML fixture
captured from the real site (tests/fixtures/html/naver/). `login()`/
`fetch_reservations()` navigate to nid.naver.com/m.place.naver.com and belong in
tests/e2e/test_naver.py instead - see docs/DESIGN.md section 11.
"""

from pathlib import Path

from tripsite_api.registry import client_factory_for
from tripsite_api.sites import naver
from tripsite_api.sites.naver import NaverClient

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "html" / "naver"


def test_registers_itself_on_import() -> None:
    assert client_factory_for("naver") is NaverClient


def test_redirected_to_login_detects_the_nid_host() -> None:
    assert naver._redirected_to_login("https://nid.naver.com/nidlogin.login?url=...") is True


def test_redirected_to_login_is_false_for_the_authenticated_destination() -> None:
    assert naver._redirected_to_login("https://m.place.naver.com/my/timeline") is False


async def test_two_factor_markers_do_not_false_trigger_on_the_plain_login_form(session) -> None:
    html = (_FIXTURES / "login_form.html").read_text(encoding="utf-8")
    page = await session.new_page()
    try:
        await page.set_content(html)
        body_text = await page.inner_text("body")
        assert naver._page_shows_two_factor_challenge(body_text) is False
    finally:
        await page.close()


async def test_login_form_fixture_has_expected_fields(session) -> None:
    html = (_FIXTURES / "login_form.html").read_text(encoding="utf-8")
    page = await session.new_page()
    try:
        await page.set_content(html)
        assert await page.locator("#id").count() == 1
        assert await page.locator("#pw").count() == 1
        assert await page.get_by_text(naver._SUBMIT_BUTTON_TEXT, exact=True).count() >= 1
    finally:
        await page.close()
