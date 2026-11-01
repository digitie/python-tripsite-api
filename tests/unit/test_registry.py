"""Verifies the ownership/concurrency contract from docs/DESIGN.md section 6: the Task
#2 subtask to check that (a) BrowserSession/Lock ownership works through SiteRegistry,
(b) different sites' locks are independent, and (c) running two sites' browsers
concurrently doesn't deadlock or contend unacceptably.
"""

import asyncio
from pathlib import Path

from tripsite_api.browser import BrowserSession
from tripsite_api.config import Settings
from tripsite_api.registry import SiteRegistry


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        state_dir=tmp_path,
        headless=True,
        nav_timeout_ms=15_000,
        min_login_interval_sec=60,
        max_login_attempts_per_day=5,
        min_query_interval_sec=30,
    )


async def test_session_for_reuses_the_same_session_per_site(tmp_path: Path) -> None:
    registry = SiteRegistry(_settings(tmp_path))
    try:
        first = await registry.session_for("yanolja")
        second = await registry.session_for("yanolja")
        assert first is second
    finally:
        await registry.close_all()


async def test_session_for_creates_independent_sessions_per_site(tmp_path: Path) -> None:
    registry = SiteRegistry(_settings(tmp_path))
    try:
        yanolja = await registry.session_for("yanolja")
        naver = await registry.session_for("naver")
        assert yanolja is not naver
        assert yanolja.site == "yanolja"
        assert naver.site == "naver"
    finally:
        await registry.close_all()


async def test_lock_for_returns_independent_locks_per_site(tmp_path: Path) -> None:
    registry = SiteRegistry(_settings(tmp_path))
    assert registry.lock_for("yanolja") is registry.lock_for("yanolja")
    assert registry.lock_for("yanolja") is not registry.lock_for("naver")


async def test_different_site_locks_do_not_block_each_other(tmp_path: Path) -> None:
    registry = SiteRegistry(_settings(tmp_path))
    order: list[str] = []

    async def hold_lock_and_record(site: str, hold_seconds: float) -> None:
        async with registry.lock_for(site):
            order.append(f"{site}:start")
            await asyncio.sleep(hold_seconds)
            order.append(f"{site}:end")

    await asyncio.gather(
        hold_lock_and_record("yanolja", 0.2),
        hold_lock_and_record("naver", 0.05),
    )

    # naver's much shorter hold finishes before yanolja's, proving the two locks ran
    # concurrently rather than being serialized behind a single shared lock.
    assert order.index("naver:end") < order.index("yanolja:end")


async def test_same_site_lock_serializes_concurrent_callers(tmp_path: Path) -> None:
    registry = SiteRegistry(_settings(tmp_path))
    order: list[str] = []

    async def hold_lock(call_id: str) -> None:
        async with registry.lock_for("yanolja"):
            order.append(f"{call_id}:start")
            await asyncio.sleep(0.05)
            order.append(f"{call_id}:end")

    await asyncio.gather(hold_lock("a"), hold_lock("b"))

    assert order in (
        ["a:start", "a:end", "b:start", "b:end"],
        ["b:start", "b:end", "a:start", "a:end"],
    )


async def test_concurrent_browsers_across_sites_do_not_deadlock(tmp_path: Path) -> None:
    registry = SiteRegistry(_settings(tmp_path))
    try:
        yanolja_session = await registry.session_for("yanolja")
        naver_session = await registry.session_for("naver")

        async def open_and_close(session: BrowserSession) -> None:
            page = await session.new_page()
            try:
                await page.goto("about:blank")
            finally:
                await page.close()

        await asyncio.wait_for(
            asyncio.gather(open_and_close(yanolja_session), open_and_close(naver_session)),
            timeout=15,
        )
    finally:
        await registry.close_all()
