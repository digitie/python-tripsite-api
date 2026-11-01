"""Unit-level checks for the Yanolja adapter's testable parts: marker-detection
predicates and login-form selectors, verified against HTML fixtures captured from
the real site (tests/fixtures/html/yanolja/). `login()`/`fetch_reservations()`
themselves navigate to nol.yanolja.com and belong in tests/e2e/test_yanolja.py
instead - see docs/DESIGN.md section 11.
"""

from pathlib import Path

from tripsite_api.registry import client_factory_for
from tripsite_api.sites import yanolja
from tripsite_api.sites.yanolja import YanoljaClient

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "html" / "yanolja"


def test_registers_itself_on_import() -> None:
    assert client_factory_for("yanolja") is YanoljaClient


async def test_logged_out_marker_matches_the_real_mypage_fixture(session) -> None:
    html = (_FIXTURES / "mypage_logged_out.html").read_text(encoding="utf-8")
    page = await session.new_page()
    try:
        await page.set_content(html)
        body_text = await page.inner_text("body")
        assert yanolja._page_shows_logged_out_state(body_text) is True
    finally:
        await page.close()


async def test_logged_out_marker_text_constant_is_present_verbatim_in_fixture() -> None:
    html = (_FIXTURES / "mypage_logged_out.html").read_text(encoding="utf-8")
    assert yanolja._NOT_LOGGED_IN_MARKER in html


async def test_two_factor_markers_do_not_false_trigger_on_the_plain_login_form(session) -> None:
    html = (_FIXTURES / "login_email_form.html").read_text(encoding="utf-8")
    page = await session.new_page()
    try:
        await page.set_content(html)
        body_text = await page.inner_text("body")
        assert yanolja._page_shows_two_factor_challenge(body_text) is False
    finally:
        await page.close()


async def test_login_email_form_fixture_has_expected_fields(session) -> None:
    html = (_FIXTURES / "login_email_form.html").read_text(encoding="utf-8")
    page = await session.new_page()
    try:
        await page.set_content(html)
        assert await page.locator("#email").count() == 1
        assert await page.locator("#password").count() == 1
        assert await page.get_by_text(yanolja._SUBMIT_BUTTON_TEXT, exact=False).count() >= 1
    finally:
        await page.close()
