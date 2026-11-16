"""MCP tool surface, exercised in-process through FastMCP's tool manager (no transport).

The protocol itself is covered end-to-end over real stdio in tests/e2e/test_mcp_stdio.py.
"""

import json
from pathlib import Path

import pytest
from mcp.types import CallToolResult

from fakes import FakeClient, credentials_loader, make_settings
from tripsite_api.errors import LoginRequiredError
from tripsite_api.mcp_server import build_server
from tripsite_api.service import ReservationService

EXPECTED_TOOLS = {"list_supported_sites", "get_reservations", "get_all_reservations", "check_login_status"}


@pytest.fixture
async def server_and_service(tmp_path: Path):
    behaviours: dict[str, dict] = {"naver": {"error": LoginRequiredError("session expired")}}

    def factory(credentials, session, settings, *, lock=None):
        return FakeClient(credentials.site, behaviours.get(credentials.site, {}))

    service = ReservationService(
        make_settings(tmp_path), credentials_loader=credentials_loader({"yanolja", "naver"}), client_factory=factory
    )
    yield build_server(service), service
    await service.close()


async def test_registers_exactly_the_documented_tools(server_and_service) -> None:
    server, _ = server_and_service
    tools = await server.list_tools()
    assert {tool.name for tool in tools} == EXPECTED_TOOLS


async def test_list_supported_sites_returns_structured_sites(server_and_service) -> None:
    server, _ = server_and_service
    result = await server.call_tool("list_supported_sites", {})

    assert isinstance(result, CallToolResult)
    assert result.isError is False
    assert result.structuredContent == {"sites": ["yanolja", "yeogiotae", "naver"]}


async def test_get_reservations_success_returns_structured_payload(server_and_service) -> None:
    server, _ = server_and_service
    result = await server.call_tool("get_reservations", {"site": "yanolja"})

    assert result.isError is False
    assert result.structuredContent["site"] == "yanolja"
    assert result.structuredContent["reservations"][0]["reservation_id"] == "yanolja-1"


async def test_error_result_is_flagged_and_carries_structured_fields(server_and_service) -> None:
    server, _ = server_and_service
    result = await server.call_tool("get_reservations", {"site": "naver"})

    assert result.isError is True
    assert result.structuredContent == {
        "error_type": "LoginRequiredError",
        "message": "session expired",
        "site": "naver",
    }
    # The text content mirrors the structured payload, so clients that only read text still work.
    assert json.loads(result.content[0].text) == result.structuredContent


async def test_unknown_site_is_a_structured_error_not_a_crash(server_and_service) -> None:
    server, _ = server_and_service
    result = await server.call_tool("check_login_status", {"site": "expedia"})

    assert result.isError is True
    assert result.structuredContent["error_type"] == "UnsupportedSiteError"


async def test_get_all_reservations_reports_each_site_status(server_and_service) -> None:
    server, _ = server_and_service
    result = await server.call_tool("get_all_reservations", {})

    assert result.isError is False
    results = result.structuredContent["results"]
    assert results["yanolja"]["status"] == "ok"
    assert results["naver"]["status"] == "error"
    assert results["yeogiotae"]["skipped_reason"] == "credentials_not_configured"
