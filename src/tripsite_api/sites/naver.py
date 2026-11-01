"""Naver Booking (booking.naver.com, via the shared nid.naver.com login) adapter.

Both `https://booking.naver.com/my/bookings` and the Naver Place "my timeline" page
redirect an unauthenticated browser to `https://nid.naver.com/nidlogin.login?...
url=https://m.place.naver.com/my/timeline` - confirming `m.place.naver.com/my/timeline`
as the real destination for a logged-in user's booking/visit history (consistent with
the "네이버지도 예약 탭" integration; see docs/DESIGN.md section 8.3). The login form
itself was captured and is implemented below with confidence.

Naver's login form carries conspicuous anti-bot instrumentation (bvsd/dynamicKey/
wtoken/nclicks_nsc hidden fields - behavioral-signature fields, not something this
adapter tries to spoof or defeat). To avoid *accidentally* looking like a bot to a
form that is this instrumented, credentials are typed with `page.type()` (real
keyboard events, matching how BaseSiteClient's docstring already frames "don't be
needlessly robotic" as distinct from "defeat CAPTCHA/anti-bot", docs/DESIGN.md
section 1) rather than `page.fill()` (which sets the value directly, bypassing
input events some forms key validation off of).

As with the other two adapters, the AUTHENTICATED timeline page's actual markup
(what a booking/reservation card looks like) has not been observed - no credentials
were available at implementation time. `fetch_reservations()` stops once it confirms
a logged-in session reaches the timeline page and raises ScrapeStructureError
instead of guessing a parser. Finishing this requires real NAVER_ID/NAVER_PW to run
`tripsite login naver` (headed) once and replace the TODO block below.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from playwright.async_api import Page

from ..errors import LoginFailedError, LoginRequiredError, TwoFactorRequiredError
from ..models import Reservation
from ..registry import register_site_client
from .base import BaseSiteClient

logger = logging.getLogger(__name__)

LOGIN_URL = "https://nid.naver.com/nidlogin.login"
TIMELINE_URL = "https://m.place.naver.com/my/timeline"

_LOGIN_HOST = "nid.naver.com"
_SUBMIT_BUTTON_TEXT = "로그인"

# Not yet verified against a real challenge screen (see the module docstring's
# caveat about the unverified authenticated state in general). Naver is known for
# device/location-based re-verification and CAPTCHA on unfamiliar automation, so
# this list errs on the broad side.
_TWO_FACTOR_MARKERS = ("자동입력 방지", "2단계 인증", "기기 인증", "새로운 기기", "보안을 위해")


def _redirected_to_login(url: str) -> bool:
    """Pure predicate backing `NaverClient.is_logged_in()`: both
    booking.naver.com/my/bookings and m.place.naver.com/my/timeline redirect an
    unauthenticated browser to nid.naver.com (see the module docstring)."""
    return urlparse(url).hostname == _LOGIN_HOST


def _page_shows_two_factor_challenge(body_text: str) -> bool:
    return any(marker in body_text for marker in _TWO_FACTOR_MARKERS)


class NaverClient(BaseSiteClient):
    site_name = "naver"

    async def is_logged_in(self, page: Page) -> bool:
        await page.goto(TIMELINE_URL, wait_until="networkidle")
        return not _redirected_to_login(page.url)

    async def login(self, page: Page) -> None:
        await page.goto(LOGIN_URL, wait_until="networkidle")
        await self._enter_identifier(page)
        await self._enter_password(page)

        submit_button = page.get_by_text(_SUBMIT_BUTTON_TEXT, exact=True)
        await submit_button.first.click()
        await page.wait_for_load_state("networkidle")

        body_text = await page.inner_text("body")
        if _page_shows_two_factor_challenge(body_text):
            raise TwoFactorRequiredError(
                "naver requested additional verification (CAPTCHA/device check) that "
                "only a human can complete - run `tripsite login naver` in headed mode."
            )

        if not await self.is_logged_in(page):
            raise LoginFailedError("naver login did not succeed - check NAVER_ID/NAVER_PW.")

    async def _enter_identifier(self, page: Page) -> None:
        id_input = page.locator("#id")
        if await id_input.count() == 0:
            await self._raise_structure_error(page, label="login_form_missing", mask_password=True)
        await id_input.click()
        await id_input.type(self._credentials.identifier, delay=30)

    async def _enter_password(self, page: Page) -> None:
        pw_input = page.locator("#pw")
        if await pw_input.count() == 0:
            await self._raise_structure_error(page, label="login_form_missing", mask_password=True)
        await pw_input.click()
        await pw_input.type(self._credentials.password.get_secret_value(), delay=30)

    async def fetch_reservations(self, page: Page, *, limit: int | None) -> list[Reservation]:
        await page.goto(TIMELINE_URL, wait_until="networkidle")
        if _redirected_to_login(page.url):
            raise LoginRequiredError("naver session expired (timeline redirected to login)")

        # TODO(needs a logged-in session): replace this with the real parser once
        # NAVER_ID/NAVER_PW are available to observe the authenticated timeline.
        await self._raise_structure_error(
            page,
            label="reservation_list_parser_not_implemented",
            mask_password=False,
        )


register_site_client("naver", NaverClient)
