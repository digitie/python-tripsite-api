from pathlib import Path

import pytest

from tripsite_api.config import (
    LoginThrottle,
    Settings,
    credentials_for_site,
    has_credentials,
)
from tripsite_api.errors import CredentialsNotConfiguredError, LoginFailedError


def test_credentials_for_site_reads_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YANOLJA_ID", "alice")
    monkeypatch.setenv("YANOLJA_PW", "s3cr3t")

    creds = credentials_for_site("yanolja")

    assert creds.site == "yanolja"
    assert creds.identifier == "alice"
    assert creds.password == "s3cr3t"


def test_credentials_for_site_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NAVER_ID", raising=False)
    monkeypatch.delenv("NAVER_PW", raising=False)

    with pytest.raises(CredentialsNotConfiguredError):
        credentials_for_site("naver")

    assert has_credentials("naver") is False


def test_credentials_for_site_rejects_unknown_site() -> None:
    with pytest.raises(ValueError):
        credentials_for_site("expedia")


def test_credentials_repr_never_contains_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YANOLJA_ID", "alice")
    monkeypatch.setenv("YANOLJA_PW", "s3cr3t-password")

    creds = credentials_for_site("yanolja")

    assert "s3cr3t-password" not in repr(creds)
    assert "s3cr3t-password" not in str(creds)
    assert "***" in repr(creds)


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    defaults = dict(
        state_dir=tmp_path,
        headless=True,
        nav_timeout_ms=15_000,
        min_login_interval_sec=60,
        max_login_attempts_per_day=5,
        min_query_interval_sec=30,
    )
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_login_throttle_allows_first_attempt(tmp_path: Path) -> None:
    throttle = LoginThrottle("yanolja", _settings(tmp_path))
    throttle.check_and_record(now=1_000.0)  # must not raise


def test_login_throttle_rejects_attempt_within_cooldown(tmp_path: Path) -> None:
    throttle = LoginThrottle("yanolja", _settings(tmp_path, min_login_interval_sec=60))
    throttle.check_and_record(now=1_000.0)

    with pytest.raises(LoginFailedError):
        throttle.check_and_record(now=1_030.0)  # only 30s later


def test_login_throttle_allows_attempt_after_cooldown(tmp_path: Path) -> None:
    throttle = LoginThrottle("yanolja", _settings(tmp_path, min_login_interval_sec=60))
    throttle.check_and_record(now=1_000.0)

    throttle.check_and_record(now=1_065.0)  # 65s later, must not raise


def test_login_throttle_enforces_daily_cap(tmp_path: Path) -> None:
    settings = _settings(tmp_path, min_login_interval_sec=0, max_login_attempts_per_day=2)
    throttle = LoginThrottle("yanolja", settings)

    throttle.check_and_record(now=1_000.0)
    throttle.check_and_record(now=1_001.0)

    with pytest.raises(LoginFailedError):
        throttle.check_and_record(now=1_002.0)


def test_login_throttle_resets_daily_cap_on_new_day(tmp_path: Path) -> None:
    settings = _settings(tmp_path, min_login_interval_sec=0, max_login_attempts_per_day=1)
    throttle = LoginThrottle("yanolja", settings)

    throttle.check_and_record(now=0.0)  # 1970-01-01

    # 1970-01-02: a new day resets the counter even though min_login_interval_sec is 0.
    throttle.check_and_record(now=86_400.0)


def test_login_throttle_persists_across_instances(tmp_path: Path) -> None:
    settings = _settings(tmp_path, min_login_interval_sec=0, max_login_attempts_per_day=1)
    LoginThrottle("yanolja", settings).check_and_record(now=1_000.0)

    with pytest.raises(LoginFailedError):
        LoginThrottle("yanolja", settings).check_and_record(now=1_001.0)


def test_login_throttle_is_independent_per_site(tmp_path: Path) -> None:
    settings = _settings(tmp_path, min_login_interval_sec=60)
    LoginThrottle("yanolja", settings).check_and_record(now=1_000.0)

    LoginThrottle("naver", settings).check_and_record(now=1_000.0)  # must not raise
