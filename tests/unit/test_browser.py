"""Exercises BrowserSession against the real, pre-installed Chromium binary.

No network access to any real travel site is needed here - only `about:blank` and
Playwright's own cookie API, which is enough to validate storage_state persistence and
crash recovery (docs/DESIGN.md section 6).
"""

import json
from pathlib import Path

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
