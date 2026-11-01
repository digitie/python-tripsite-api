"""Yanolja (nol.yanolja.com) adapter.

The login flow and the logged-out state of the mypage were captured by driving a
real browser against nol.yanolja.com (see tests/fixtures/html/yanolja/ and
docs/DESIGN.md section 8.1) and are implemented below with confidence.

The AUTHENTICATED reservation-list markup has not been observed: no credentials
were available at implementation time (see docs/DESIGN.md section 8.1 / the PR this
shipped in). `fetch_reservations()` therefore stops at the reservation-list entry
point and raises ScrapeStructureError via `_raise_structure_error()` instead of
guessing at a parser for markup nobody has seen - that would silently return wrong
data instead of failing loudly. Finishing this requires a real YANOLJA_ID/YANOLJA_PW
to run `tripsite login yanolja` (headed) once, inspect the resulting page, and
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

LOGIN_URL = "https://nol.yanolja.com/login"
MYPAGE_URL = "https://nol.yanolja.com/mypage"

_NOT_LOGGED_IN_MARKER = "로그인 또는 회원가입하기"
_EMAIL_LOGIN_BUTTON_TEXT = "메일로 시작하기"
_SUBMIT_BUTTON_TEXT = "로그인하기"
_LODGING_RESERVATIONS_LINK_TEXT = "국내숙소/레저/교통"

# Conservative heuristic, not yet verified against a real challenge screen (see the
# module docstring) - a login() that can't tell a 2FA/CAPTCHA screen from a generic
# failure would otherwise mislabel it as bad credentials and burn a LoginThrottle
# attempt pointlessly.
_TWO_FACTOR_MARKERS = ("인증번호", "보안 확인", "자동입력 방지")


def _page_shows_logged_out_state(body_text: str) -> bool:
    """Pure predicate (unit-testable against the captured fixture without a live
    browser session) backing `YanoljaClient.is_logged_in()`."""
    return _NOT_LOGGED_IN_MARKER in body_text


def _page_shows_two_factor_challenge(body_text: str) -> bool:
    """Pure predicate backing the 2FA/CAPTCHA detection in `YanoljaClient.login()`."""
    return any(marker in body_text for marker in _TWO_FACTOR_MARKERS)


class YanoljaClient(BaseSiteClient):
    site_name = "yanolja"

    async def is_logged_in(self, page: Page) -> bool:
        await page.goto(MYPAGE_URL, wait_until="networkidle")
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

        email_input = page.locator("#email")
        password_input = page.locator("#password")
        if await email_input.count() == 0 or await password_input.count() == 0:
            await self._raise_structure_error(page, label="login_form_missing", mask_password=True)

        await email_input.fill(self._credentials.identifier)
        await password_input.fill(self._credentials.password.get_secret_value())

        submit_button = page.get_by_text(_SUBMIT_BUTTON_TEXT, exact=False)
        await submit_button.first.click()
        await page.wait_for_load_state("networkidle")

        body_text = await page.inner_text("body")
        if _page_shows_two_factor_challenge(body_text):
            raise TwoFactorRequiredError(
                "yanolja requested additional verification (CAPTCHA/device check) that "
                "only a human can complete - run `tripsite login yanolja` in headed mode."
            )

        if not await self.is_logged_in(page):
            raise LoginFailedError("yanolja login did not succeed - check YANOLJA_ID/YANOLJA_PW.")

    async def fetch_reservations(self, page: Page, *, limit: int | None) -> list[Reservation]:
        await page.goto(MYPAGE_URL, wait_until="networkidle")
        if _page_shows_logged_out_state(await page.inner_text("body")):
            raise LoginRequiredError("yanolja session expired (mypage shows the logged-out state)")

        reservations_link = page.get_by_text(_LODGING_RESERVATIONS_LINK_TEXT, exact=False)
        if await reservations_link.count() == 0:
            await self._raise_structure_error(
                page, label="reservations_link_missing", mask_password=False
            )
        await reservations_link.first.click()
        await page.wait_for_load_state("networkidle")

        if _page_shows_logged_out_state(await page.inner_text("body")):
            raise LoginRequiredError("yanolja session expired while opening the reservation list")

        # TODO(needs a logged-in session): replace this with the real parser once
        # YANOLJA_ID/YANOLJA_PW are available to observe the authenticated page.
        # Raising here (rather than returning []) makes the gap impossible to miss.
        await self._raise_structure_error(
            page,
            label="reservation_list_parser_not_implemented",
            mask_password=False,
        )


register_site_client("yanolja", YanoljaClient)
