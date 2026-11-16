"""Protocol-level e2e: starts the real server as a subprocess over stdio and talks to it
with the official MCP client. Needs no site credentials and reaches no external site -
every site is reported as unconfigured, which is exactly what an agent would see on a
machine that hasn't run `tripsite login` yet. Runs in CI alongside the unit tests.
"""

import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_CREDENTIAL_VARS = ("YANOLJA_ID", "YANOLJA_PW", "YEOGIOTAE_ID", "YEOGIOTAE_PW", "NAVER_ID", "NAVER_PW")


@pytest.fixture
def server_params(tmp_path: Path) -> StdioServerParameters:
    env = {k: v for k, v in os.environ.items() if k not in _CREDENTIAL_VARS}
    env["TRIPSITE_STATE_DIR"] = str(tmp_path)
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "tripsite_api.cli", "serve"],
        env=env,
    )


async def test_server_lists_tools_over_stdio(server_params: StdioServerParameters) -> None:
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()

    assert {tool.name for tool in tools.tools} == {
        "list_supported_sites",
        "get_reservations",
        "get_all_reservations",
        "check_login_status",
    }


async def test_unconfigured_sites_are_skipped_and_errors_are_structured(
    server_params: StdioServerParameters,
) -> None:
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            sites = await session.call_tool("list_supported_sites", {})
            all_results = await session.call_tool("get_all_reservations", {})
            single = await session.call_tool("get_reservations", {"site": "naver"})

    assert sites.isError is False
    assert sites.structuredContent == {"sites": ["yanolja", "yeogiotae", "naver"]}

    assert all_results.isError is False
    for site in ("yanolja", "yeogiotae", "naver"):
        assert all_results.structuredContent["results"][site] == {
            "status": "skipped",
            "skipped_reason": "credentials_not_configured",
        }

    assert single.isError is True
    assert single.structuredContent["error_type"] == "CredentialsNotConfiguredError"
    assert single.structuredContent["site"] == "naver"
