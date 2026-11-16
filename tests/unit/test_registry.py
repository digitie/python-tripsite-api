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


async def test_session_for_handles_concurrent_first_calls_without_leaking_a_session(
    tmp_path: Path,
) -> None:
    """Regression test for a review finding: session_for()'s check-then-create had no
    lock around the `await session.start()` in between, so two concurrent first
    calls for the same site could both see no cached session and both launch a
    BrowserSession - leaking the loser's Chromium process.
    """
    registry = SiteRegistry(_settings(tmp_path))
    try:
        first, second = await asyncio.gather(
            registry.session_for("yanolja"), registry.session_for("yanolja")
        )
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
    """Deterministic (no sleep/timing): proves naver's lock can be acquired and
    released *while* yanolja's lock is still held, which would be impossible if both
    sites shared a single lock.
    """
    registry = SiteRegistry(_settings(tmp_path))
    yanolja_holding = asyncio.Event()
    naver_done = asyncio.Event()

    async def hold_yanolja_until_naver_is_done() -> None:
        async with registry.lock_for("yanolja"):
            yanolja_holding.set()
            await naver_done.wait()

    async def acquire_naver_while_yanolja_is_held() -> None:
        await yanolja_holding.wait()
        async with registry.lock_for("naver"):
            pass
        naver_done.set()

    await asyncio.wait_for(
        asyncio.gather(hold_yanolja_until_naver_is_done(), acquire_naver_while_yanolja_is_held()),
        timeout=5,
    )


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


async def test_close_all_closes_remaining_sessions_when_one_fails(tmp_path: Path) -> None:
    registry = SiteRegistry(_settings(tmp_path))
    closed: list[str] = []

    class _Session:
        def __init__(self, site: str, fail: bool) -> None:
            self.site = site
            self.fail = fail

        async def close(self) -> None:
            if self.fail:
                raise RuntimeError("driver already gone")
            closed.append(self.site)

    registry._sessions = {
        "yanolja": _Session("yanolja", fail=True),
        "naver": _Session("naver", fail=False),
    }

    await registry.close_all()

    assert closed == ["naver"]
    assert registry._sessions == {}
