"""Unit-level checks for the Yeogiotae adapter's testable parts, verified against
HTML fixtures captured from the real site (tests/fixtures/html/yeogiotae/).
`login()`/`fetch_reservations()` navigate to www.yeogi.com/platform.yeogi.com and
belong in tests/e2e/test_yeogiotae.py instead - see docs/DESIGN.md section 11.
"""

from pathlib import Path

from tripsite_api.registry import client_factory_for
from tripsite_api.sites import yeogiotae
from tripsite_api.sites.yeogiotae import YeogiotaeClient

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "html" / "yeogiotae"


def test_registers_itself_on_import() -> None:
    assert client_factory_for("yeogiotae") is YeogiotaeClient


async def test_logged_out_marker_matches_the_real_home_fixture(session) -> None:
    html = (_FIXTURES / "home_logged_out.html").read_text(encoding="utf-8")
    page = await session.new_page()
    try:
        await page.set_content(html)
        body_text = await page.inner_text("body")
        assert yeogiotae._page_shows_logged_out_state(body_text) is True
    finally:
        await page.close()


def test_logged_out_marker_text_constant_is_present_verbatim_in_fixture() -> None:
    html = (_FIXTURES / "home_logged_out.html").read_text(encoding="utf-8")
    assert yeogiotae._NOT_LOGGED_IN_MARKER in html


async def test_two_factor_markers_do_not_false_trigger_on_the_plain_login_form(session) -> None:
    html = (_FIXTURES / "login_email_form.html").read_text(encoding="utf-8")
    page = await session.new_page()
    try:
        await page.set_content(html)
        body_text = await page.inner_text("body")
        assert yeogiotae._page_shows_two_factor_challenge(body_text) is False
    finally:
        await page.close()


def test_login_email_form_fixture_has_expected_fields() -> None:
    """A raw string check, not page.set_content() + a locator: this page's Nuxt
    bundle is injected via parser-blocking document.write()'d <script> tags, which
    halts HTML parsing entirely once their (external, network-dependent) src fails
    to load - so offline, the DOM ends up with zero elements even though the
    captured source text has the real server-rendered markup. See the real page's
    console warnings about "invoked via document.write" for confirmation.
    """
    html = (_FIXTURES / "login_email_form.html").read_text(encoding="utf-8")
    assert 'id="userEmail"' in html
    assert 'name="userEmail"' in html
    assert 'id="userPassword"' in html
    assert 'type="password"' in html
