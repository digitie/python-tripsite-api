import asyncio
import json
from pathlib import Path

import pytest

from tripsite_api.cli import _build_parser, main


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRIPSITE_STATE_DIR", str(tmp_path))
    for var in ("YANOLJA_ID", "YANOLJA_PW", "YEOGIOTAE_ID", "YEOGIOTAE_PW", "NAVER_ID", "NAVER_PW"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_parser_rejects_unknown_site() -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["check", "expedia"])


def test_parser_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args([])


def test_parser_accepts_all_documented_commands() -> None:
    parser = _build_parser()
    assert parser.parse_args(["login", "naver"]).command == "login"
    assert parser.parse_args(["check", "naver"]).command == "check"
    assert parser.parse_args(["reservations", "naver", "--limit", "3"]).limit == 3
    assert parser.parse_args(["serve"]).command == "serve"


def test_reservations_without_credentials_exits_1_with_structured_error(
    isolated_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["reservations", "naver"])

    assert exit_code == 1
    error = json.loads(capsys.readouterr().err)
    assert error["error_type"] == "CredentialsNotConfiguredError"
    assert error["site"] == "naver"


def test_check_without_credentials_exits_1(isolated_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "yanolja"]) == 1
    assert json.loads(capsys.readouterr().err)["error_type"] == "CredentialsNotConfiguredError"


def test_login_without_credentials_fails_before_opening_a_browser(
    isolated_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["login", "yeogiotae"]) == 1
    assert json.loads(capsys.readouterr().err)["error_type"] == "CredentialsNotConfiguredError"


def test_wait_for_enter_returns_once_a_line_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    from tripsite_api.cli import _wait_for_enter

    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    asyncio.run(_wait_for_enter("ok? "))


def test_wait_for_enter_surfaces_end_of_input(monkeypatch: pytest.MonkeyPatch) -> None:
    from tripsite_api.cli import _wait_for_enter

    def closed(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", closed)

    with pytest.raises(EOFError):
        asyncio.run(_wait_for_enter("ok? "))
