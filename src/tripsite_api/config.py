"""Credentials, runtime settings, and login throttling. See docs/DESIGN.md sections 1/5."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import CredentialsNotConfiguredError, LoginFailedError

SUPPORTED_SITES: tuple[str, ...] = ("yanolja", "yeogiotae", "naver")

_ENV_PREFIX: dict[str, str] = {
    "yanolja": "YANOLJA",
    "yeogiotae": "YEOGIOTAE",
    "naver": "NAVER",
}


def _require_known_site(site: str) -> str:
    try:
        return _ENV_PREFIX[site]
    except KeyError:
        raise ValueError(f"Unknown site: {site!r}. Supported sites: {SUPPORTED_SITES}") from None


@dataclass(frozen=True)
class Credentials:
    site: str
    identifier: str
    password: str

    def __repr__(self) -> str:  # never let a password reach a log line or traceback
        return f"Credentials(site={self.site!r}, identifier={self.identifier!r}, password='***')"

    __str__ = __repr__


def credentials_for_site(site: str) -> Credentials:
    """Read `<SITE>_ID`/`<SITE>_PW` from the environment.

    Raises CredentialsNotConfiguredError if either is missing/blank.
    """
    prefix = _require_known_site(site)
    identifier = os.environ.get(f"{prefix}_ID", "").strip()
    password = os.environ.get(f"{prefix}_PW", "").strip()
    if not identifier or not password:
        raise CredentialsNotConfiguredError(
            f"{prefix}_ID / {prefix}_PW is not set for site '{site}'."
        )
    return Credentials(site=site, identifier=identifier, password=password)


def has_credentials(site: str) -> bool:
    try:
        credentials_for_site(site)
    except CredentialsNotConfiguredError:
        return False
    return True


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    headless: bool
    nav_timeout_ms: int
    min_login_interval_sec: int
    max_login_attempts_per_day: int
    min_query_interval_sec: int

    @classmethod
    def from_env(cls) -> Settings:
        state_dir = Path(os.environ.get("TRIPSITE_STATE_DIR", "~/.tripsite-api/state")).expanduser()
        return cls(
            state_dir=state_dir,
            headless=_env_bool("TRIPSITE_HEADLESS", True),
            nav_timeout_ms=_env_int("TRIPSITE_NAV_TIMEOUT_MS", 15_000),
            min_login_interval_sec=_env_int("TRIPSITE_MIN_LOGIN_INTERVAL_SEC", 60),
            max_login_attempts_per_day=_env_int("TRIPSITE_MAX_LOGIN_ATTEMPTS_PER_DAY", 5),
            min_query_interval_sec=_env_int("TRIPSITE_MIN_QUERY_INTERVAL_SEC", 30),
        )


class LoginThrottle:
    """Per-site cooldown + daily cap on login attempts (docs/DESIGN.md sections 1/5).

    Persists attempt counters to `<state_dir>/login_attempts_<site>.json` so the limit
    survives process restarts, not just within a single MCP server run.
    """

    def __init__(self, site: str, settings: Settings) -> None:
        self._site = site
        self._settings = settings
        self._path = settings.state_dir / f"login_attempts_{site}.json"

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"day": "", "count": 0, "last_attempt_ts": 0.0}

    def _save(self, data: dict) -> None:
        self._settings.state_dir.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data))
        try:
            self._path.chmod(0o600)
        except OSError:
            pass  # best effort; not fatal on filesystems without POSIX permissions

    def check_and_record(self, *, now: float | None = None) -> None:
        """Raise LoginFailedError if a new attempt would violate the cooldown or the
        daily cap; otherwise record the attempt and return.
        """
        now = time.time() if now is None else now
        today = time.strftime("%Y-%m-%d", time.gmtime(now))
        data = self._load()
        if data.get("day") != today:
            data = {"day": today, "count": 0, "last_attempt_ts": 0.0}

        elapsed = now - data.get("last_attempt_ts", 0.0)
        if elapsed < self._settings.min_login_interval_sec:
            wait = self._settings.min_login_interval_sec - elapsed
            raise LoginFailedError(
                f"Re-login for site='{self._site}' attempted too soon; wait {wait:.0f}s "
                f"more (TRIPSITE_MIN_LOGIN_INTERVAL_SEC="
                f"{self._settings.min_login_interval_sec})."
            )
        if data["count"] >= self._settings.max_login_attempts_per_day:
            raise LoginFailedError(
                f"Daily login attempt limit ({self._settings.max_login_attempts_per_day}) "
                f"reached for site='{self._site}'. Try again tomorrow, or raise "
                "TRIPSITE_MAX_LOGIN_ATTEMPTS_PER_DAY if this is expected."
            )

        data["count"] += 1
        data["last_attempt_ts"] = now
        self._save(data)
