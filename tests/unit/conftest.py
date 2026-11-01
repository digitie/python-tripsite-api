from pathlib import Path

import pytest

from tripsite_api.browser import BrowserSession


@pytest.fixture
async def session(tmp_path: Path):
    """A real, headless BrowserSession backed by the sandbox's pre-installed
    Chromium - shared across test modules that only need a Page to drive with
    page.set_content()/page.fill() etc., not real site navigation."""
    s = BrowserSession("fake", headless=True, state_dir=tmp_path)
    async with s:
        yield s
