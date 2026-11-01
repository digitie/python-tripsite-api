"""Yeogiotae/여기어때 (goodchoice.kr -> www.yeogi.com) adapter.

The login flow was captured by driving a real browser against www.yeogi.com /
platform.yeogi.com without credentials (see tests/fixtures/html/yeogiotae/ and
docs/DESIGN.md section 8.2) and is implemented below with confidence.

Unlike Yanolja, the member reservation-history ENTRY POINT itself could not be
located publicly: the menu item that would link to it only appears once logged in
(it replaces "로그인/회원가입" in the nav), and guessing at platform.yeogi.com URL
patterns by analogy with the guest-lookup flow (`guest/reservation/check`) only
produced errors. `fetch_reservations()` therefore can't even get as far as
Yanolja's "found the link, haven't seen the cards" checkpoint - it raises
ScrapeStructureError immediately after confirming a session is active. Finishing
this requires real YEOGIOTAE_ID/YEOGIOTAE_PW to run `tripsite login yeogiotae`
(headed), find the actual reservation-history link from a logged-in nav, and
replace the TODO block below.
"""

from __future__ import annotations

import logging

from playwright.async_api import Page

from ..errors import LoginFailedError, LoginRequiredError, TwoFactorRequiredError
from ..models import Reservation
from ..registry import register_site_client
from .base import BaseSiteClient

logger = logging.getLogger(__name__)

HOME_URL = "https://www.yeogi.com/"
LOGIN_URL = "https://platform.yeogi.com/login?redirectUri=https://www.yeogi.com/"

_NOT_LOGGED_IN_MARKER = "로그인/회원가입"
_EMAIL_LOGIN_BUTTON_TEXT = "이메일로 시작하기"
_SUBMIT_BUTTON_TEXT = "로그인"

# Not yet verified against a real challenge screen - see the module docstring's
# caveat about the unverified authenticated state in general.
_TWO_FACTOR_MARKERS = ("인증번호", "보안 확인", "자동입력 방지")


def _page_shows_logged_out_state(body_text: str) -> bool:
    """Pure predicate (unit-testable against the captured fixture without a live
    browser session) backing `YeogiotaeClient.is_logged_in()`."""
    return _NOT_LOGGED_IN_MARKER in body_text


def _page_shows_two_factor_challenge(body_text: str) -> bool:
    return any(marker in body_text for marker in _TWO_FACTOR_MARKERS)


class YeogiotaeClient(BaseSiteClient):
    site_name = "yeogiotae"

    async def is_logged_in(self, page: Page) -> bool:
        await page.goto(HOME_URL, wait_until="networkidle")
        body_text = await page.inner_text("body")
        return not _page_shows_logged_out_state(body_text)

    async def login(self, page: Page) -> None:
        await page.goto(LOGIN_URL, wait_until="networkidle")

        email_login_button = page.get_by_text(_EMAIL_LOGIN_BUTTON_TEXT, exact=False)
        if await email_login_button.count() == 0:
            await self._raise_structure_error(
                page, label="login_email_option_missing", mask_password=False
            )
        await email_login_button.first.click()
        await page.wait_for_load_state("networkidle")

        email_input = page.locator("#userEmail")
        password_input = page.locator("#userPassword")
        if await email_input.count() == 0 or await password_input.count() == 0:
            await self._raise_structure_error(page, label="login_form_missing", mask_password=True)

        await email_input.fill(self._credentials.identifier)
        await password_input.fill(self._credentials.password.get_secret_value())

        submit_button = page.get_by_text(_SUBMIT_BUTTON_TEXT, exact=True)
        await submit_button.first.click()
        await page.wait_for_load_state("networkidle")

        body_text = await page.inner_text("body")
        if _page_shows_two_factor_challenge(body_text):
            raise TwoFactorRequiredError(
                "yeogiotae requested additional verification (CAPTCHA/device check) "
                "that only a human can complete - run `tripsite login yeogiotae` in "
                "headed mode."
            )

        if not await self.is_logged_in(page):
            raise LoginFailedError(
                "yeogiotae login did not succeed - check YEOGIOTAE_ID/YEOGIOTAE_PW."
            )

    async def fetch_reservations(self, page: Page, *, limit: int | None) -> list[Reservation]:
        await page.goto(HOME_URL, wait_until="networkidle")
        if _page_shows_logged_out_state(await page.inner_text("body")):
            raise LoginRequiredError("yeogiotae session expired (home shows the logged-out state)")

        # TODO(needs a logged-in session): the reservation-history link only appears
        # once logged in (it replaces _NOT_LOGGED_IN_MARKER in the nav) and could not
        # be located from a logged-out session - see the module docstring. Raising
        # here (rather than returning []) makes the gap impossible to miss.
        await self._raise_structure_error(
            page,
            label="reservation_entry_point_not_located",
            mask_password=False,
        )


register_site_client("yeogiotae", YeogiotaeClient)
