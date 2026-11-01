"""Shared exception hierarchy. See docs/DESIGN.md section 4."""

from __future__ import annotations


class TripsiteError(Exception):
    """Base class for all tripsite-api errors."""


class LoginRequiredError(TripsiteError):
    """A cached session is missing or no longer authenticated; a re-login is needed.

    Raised by a BaseSiteClient subclass's `is_logged_in`/`fetch_reservations`. Retrying
    the login is exclusively BaseSiteClient.get_reservations's responsibility (see
    docs/DESIGN.md section 6/7) - BrowserSession never raises this itself.
    """


class LoginFailedError(TripsiteError):
    """A login attempt was rejected (bad credentials) or throttled (cooldown / daily cap
    from LoginThrottle, see config.py)."""


class TwoFactorRequiredError(TripsiteError):
    """The site demanded verification only a human can complete (OTP, CAPTCHA, a device
    confirmation prompt, ...). Callers should surface this to a human rather than retry
    automatically; `cli.py` catches it and waits for the person to finish the challenge."""


class ScrapeStructureError(TripsiteError):
    """An expected page element was not found - most likely the site's markup changed.

    Carries paths to a screenshot/HTML snapshot captured at failure time so a human can
    diagnose and fix the adapter's selectors without reproducing the failure.
    """

    def __init__(
        self,
        message: str,
        *,
        screenshot_path: str | None = None,
        html_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.screenshot_path = screenshot_path
        self.html_path = html_path


class CredentialsNotConfiguredError(TripsiteError):
    """The `<SITE>_ID`/`<SITE>_PW` environment variables are not set for this site."""
