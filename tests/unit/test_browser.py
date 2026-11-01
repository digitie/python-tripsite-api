"""Exercises BrowserSession against the real, pre-installed Chromium binary.

No network access to any real travel site is needed here - only `about:blank` and
Playwright's own cookie API, which is enough to validate storage_state persistence and
crash recovery (docs/DESIGN.md section 6).
"""

import json
from pathlib import Path

import pytest

from tripsite_api.browser import BrowserSession


async def test_browser_session_starts_and_closes(tmp_path: Path) -> None:
    session = BrowserSession("yanolja", headless=True, state_dir=tmp_path)
    async with session:
        assert await session.is_connected() is True
        page = await session.new_page()
        try:
            await page.goto("about:blank")
        finally:
            await page.close()
    assert await session.is_connected() is False


async def test_browser_session_persists_storage_state_across_restarts(tmp_path: Path) -> None:
    cookie = {"name": "tripsite_test", "value": "abc123", "domain": "example.test", "path": "/"}

    session = BrowserSession("yanolja", headless=True, state_dir=tmp_path)
    async with session:
        await session._context.add_cookies([cookie])  # type: ignore[union-attr]
        await session.save_storage_state()

    state_path = tmp_path / "storage_state_yanolja.json"
    assert state_path.exists()
    assert oct(state_path.stat().st_mode & 0o777) == oct(0o600)
    saved = json.loads(state_path.read_text())
    assert any(c["name"] == "tripsite_test" for c in saved.get("cookies", []))

    # A fresh BrowserSession for the same site/state_dir picks the cookie back up.
    session2 = BrowserSession("yanolja", headless=True, state_dir=tmp_path)
    async with session2:
        cookies = await session2._context.cookies()  # type: ignore[union-attr]
        assert any(c["name"] == "tripsite_test" and c["value"] == "abc123" for c in cookies)


async def test_browser_session_is_independent_per_site(tmp_path: Path) -> None:
    yanolja = BrowserSession("yanolja", headless=True, state_dir=tmp_path)
    naver = BrowserSession("naver", headless=True, state_dir=tmp_path)
    async with yanolja, naver:
        await yanolja._context.add_cookies(  # type: ignore[union-attr]
            [{"name": "site_marker", "value": "yanolja", "domain": "example.test", "path": "/"}]
        )
        naver_cookies = await naver._context.cookies()  # type: ignore[union-attr]
        assert naver_cookies == []  # the other site's context never saw that cookie


async def test_new_page_recovers_after_browser_crashes(tmp_path: Path) -> None:
    session = BrowserSession("yanolja", headless=True, state_dir=tmp_path)
    await session.start()
    try:
        await session._browser.close()  # type: ignore[union-attr]  # simulate a crash
        assert await session.is_connected() is False

        page = await session.new_page()  # must detect the crash and restart
        try:
            assert await session.is_connected() is True
            await page.goto("about:blank")
        finally:
            await page.close()
    finally:
        await session.close()


async def test_externally_written_storage_state_is_picked_up_on_next_page(tmp_path: Path) -> None:
    """Regression: the MCP server must see a session that `tripsite login` wrote while it
    was running, instead of keeping its stale in-memory cookies."""
    session = BrowserSession("naver", headless=True, state_dir=tmp_path)
    async with session:
        assert await session._context.cookies() == []  # type: ignore[union-attr]

        external = {
            "cookies": [
                {
                    "name": "NID_SES",
                    "value": "from-cli",
                    "domain": "example.test",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": False,
                    "sameSite": "Lax",
                }
            ],
            "origins": [],
        }
        (tmp_path / "storage_state_naver.json").write_text(json.dumps(external))

        page = await session.new_page()
        try:
            cookies = await session._context.cookies()  # type: ignore[union-attr]
        finally:
            await page.close()
        assert any(c["name"] == "NID_SES" and c["value"] == "from-cli" for c in cookies)


async def test_close_does_not_overwrite_storage_state_written_by_another_process(tmp_path: Path) -> None:
    """Regression: on shutdown the server must not replace the CLI's fresh session with
    its own stale in-memory state."""
    session = BrowserSession("naver", headless=True, state_dir=tmp_path)
    await session.start()
    await session._context.add_cookies(  # type: ignore[union-attr]
        [{"name": "stale", "value": "server", "domain": "example.test", "path": "/"}]
    )
    (tmp_path / "storage_state_naver.json").write_text(
        json.dumps({"cookies": [{"name": "fresh", "value": "cli", "domain": "example.test", "path": "/",
                                 "expires": -1, "httpOnly": False, "secure": False, "sameSite": "Lax"}],
                    "origins": []})
    )

    await session.close()

    saved = json.loads((tmp_path / "storage_state_naver.json").read_text())
    assert [c["name"] for c in saved["cookies"]] == ["fresh"]


async def test_corrupt_storage_state_is_treated_as_absent(tmp_path: Path) -> None:
    """A write cut off mid-way must not make every later call fail; start fresh instead."""
    (tmp_path / "storage_state_naver.json").write_text("{not json")

    session = BrowserSession("naver", headless=True, state_dir=tmp_path)
    async with session:
        page = await session.new_page()
        try:
            await page.goto("about:blank")
        finally:
            await page.close()


async def test_save_is_atomic_and_leaves_no_temp_file(tmp_path: Path) -> None:
    session = BrowserSession("naver", headless=True, state_dir=tmp_path)
    async with session:
        await session.save_storage_state()

    assert (tmp_path / "storage_state_naver.json").exists()
    assert not (tmp_path / "storage_state_naver.json.tmp").exists()


async def test_failed_start_stops_the_playwright_driver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a start() that fails after launching the driver must not leave it
    running, because nothing caches the half-started session to close it later."""
    session = BrowserSession("naver", headless=True, state_dir=tmp_path)

    async def boom(self) -> None:
        raise RuntimeError("context creation failed")

    monkeypatch.setattr(BrowserSession, "_new_context", boom)

    with pytest.raises(RuntimeError, match="context creation failed"):
        await session.start()

    assert session._playwright is None
    assert session._browser is None
    assert session._context is None
