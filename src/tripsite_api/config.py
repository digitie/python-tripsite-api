"""Credentials, runtime settings, and login throttling. See docs/DESIGN.md sections 1/5."""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import CredentialsNotConfiguredError, LoginFailedError

logger = logging.getLogger(__name__)

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


class SecretStr:
    """Wraps a secret so it survives more than just `repr()`/`str()` overrides.

    `dataclasses.asdict()`/`vars()`/`__dict__` bypass a dataclass's own `__repr__`
    override and would print a plain `str` password field verbatim. Wrapping the
    value in a dedicated (non-dataclass) type defeats that: `asdict()` only
    decomposes dataclasses/lists/dicts/tuples, so a SecretStr field is left as this
    object, and *its* repr is what ends up printed.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretStr('***')"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SecretStr) and self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)


@dataclass(frozen=True)
class Credentials:
    site: str
    identifier: str
    password: SecretStr

    def __repr__(self) -> str:  # defense in depth on top of SecretStr itself
        return f"Credentials(site={self.site!r}, identifier={self.identifier!r}, password=SecretStr('***'))"

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
    return Credentials(site=site, identifier=identifier, password=SecretStr(password))


def has_credentials(site: str) -> bool:
    try:
        credentials_for_site(site)
    except CredentialsNotConfiguredError:
        return False
    return True


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():  # unset or blank -> default, same as _env_int
        return default
    return raw.strip().lower() not in ("0", "false", "no")


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
        resolved_state_dir = state_dir.resolve()  # safe even if state_dir doesn't exist yet
        cwd = Path.cwd().resolve()
        if resolved_state_dir == cwd or cwd in resolved_state_dir.parents:
            logger.warning(
                "TRIPSITE_STATE_DIR=%s is inside the current working directory (%s). "
                "This risks committing session cookies/credentials-adjacent files to "
                "a git repo - see docs/DESIGN.md section 12.",
                state_dir,
                cwd,
            )
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
        self._lock_path = settings.state_dir / f"login_attempts_{site}.lock"

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"day": "", "count": 0, "last_attempt_ts": 0.0}

    def _save(self, data: dict) -> None:
        self._path.write_text(json.dumps(data))
        try:
            self._path.chmod(0o600)
        except OSError:
            pass  # best effort; not fatal on filesystems without POSIX permissions

    @contextlib.contextmanager
    def _cross_process_lock(self):
        """Makes check_and_record's read-modify-write one atomic step across
        processes (docs/DESIGN.md section 9: MCP server and `cli.py` share
        TRIPSITE_STATE_DIR, so both may race to record a login attempt for the same
        site at once).
        """
        self._settings.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path, "w") as lock_file:
            try:
                os.fchmod(lock_file.fileno(), 0o600)
            except OSError:
                pass
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def check_and_record(self, *, now: float | None = None) -> None:
        """Raise LoginFailedError if a new attempt would violate the cooldown or the
        daily cap; otherwise record the attempt and return.
        """
        now = time.time() if now is None else now
        today = time.strftime("%Y-%m-%d", time.gmtime(now))

        with self._cross_process_lock():
            data = self._load()
            if data.get("day") != today:
                data = {"day": today, "count": 0, "last_attempt_ts": 0.0}

            elapsed = now - data.get("last_attempt_ts", 0.0)
            if elapsed < self._settings.min_login_interval_sec:
                wait = self._settings.min_login_interval_sec - elapsed
                raise LoginFailedError(
                    f"Re-login for site='{self._site}' attempted too soon; wait "
                    f"{wait:.0f}s more (TRIPSITE_MIN_LOGIN_INTERVAL_SEC="
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
