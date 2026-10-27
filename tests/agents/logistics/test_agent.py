"""Unit tests for the ZavaShop logistics specialist agent."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import cast

import pytest
from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject
from copilot.generated.session_events import PermissionRequestCustomTool, PermissionRequestMcp, PermissionRequestRead
from httpx import ASGITransport, AsyncClient

from src.agents.logistics.agent import (
    _approve_shipping_quotes,  # pyright: ignore[reportPrivateUsage]
    build_agent,
)
from src.agents.logistics.prompts import SYSTEM_PROMPT
from src.shared.server import make_app
from src.shared.settings import Settings
from tests.agents._mock import MockChatClient, captured_options


def _mcp_servers(options: Mapping[str, object]) -> Mapping[str, object]:
    servers = options.get("mcp_servers")
    if not isinstance(servers, Mapping):
        raise AssertionError("mcp_servers was not configured")
    return cast("Mapping[str, object]", servers)


@pytest.mark.asyncio
async def test_logistics_agent_uses_shipping_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    MockChatClient.reset(responses=['{"po_id":"PO-ZS1042-STORE101","shipping_view":"use expedited carrier"}'])
    monkeypatch.setattr("src.agents.logistics.agent.GitHubCopilotAgent", MockChatClient)

    settings = Settings(shipping_mcp_url="http://shipping.local/mcp")
    agent = await build_agent(settings)
    response = await agent.run("Quote shipment for PO-ZS1042-STORE101 to store-101 quantity 80.")
    options = captured_options()
    mcp_servers = _mcp_servers(options)

    assert "PO-ZS1042-STORE101" in response.output_text
    assert "shipping" in mcp_servers
    assert "gpt-5.5" == options.get("model")
    assert settings.copilot_timeout_seconds == options.get("timeout")


def test_shipping_permission_approves_only_allowed_sdk_mcp_tool_names() -> None:
    for server_name in ("shipping", "shipping-mcp"):
        for tool_name in ("quote_shipment", "shipping-quote_shipment"):
            request = PermissionRequestMcp(
                read_only=True,
                server_name=server_name,
                tool_name=tool_name,
                tool_title=tool_name,
            )
            assert isinstance(_approve_shipping_quotes(request, {}), PermissionDecisionApproveOnce)

    unrelated_tool = PermissionRequestMcp(
        read_only=True,
        server_name="shipping",
        tool_name="initiate_shipment",
        tool_title="Initiate shipment",
    )
    unauthorized_server = PermissionRequestMcp(
        read_only=True,
        server_name="untrusted-shipping",
        tool_name="shipping-quote_shipment",
        tool_title="Quote shipment",
    )
    custom_tool = PermissionRequestCustomTool(
        tool_name="quote_shipment",
        tool_description="External custom tool.",
    )
    file_read = PermissionRequestRead(intention="Read a local file", path="C:/private.txt")

    assert isinstance(_approve_shipping_quotes(unrelated_tool, {}), PermissionDecisionReject)
    assert isinstance(_approve_shipping_quotes(unauthorized_server, {}), PermissionDecisionReject)
    assert isinstance(_approve_shipping_quotes(custom_tool, {}), PermissionDecisionReject)
    assert isinstance(_approve_shipping_quotes(file_read, {}), PermissionDecisionReject)


@pytest.mark.asyncio
async def test_logistics_invoke_reports_observed_tool_calls_and_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.agents.logistics.agent.GitHubCopilotAgent", MockChatClient)
    app = make_app(name="logistics", build=build_agent, settings=Settings())

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            MockChatClient.reset(
                responses=['{"shipping_view":"quote available"}'],
                tool_calls=[["shipping-quote_shipment"]],
            )
            observed_response = await client.post(
                "/invoke",
                json={"run_id": "shipping-observed", "goal": "Quote this shipment route."},
            )

            MockChatClient.reset(responses=['{"shipping_view":"no call observed"}'])
            no_call_response = await client.post(
                "/invoke",
                json={"run_id": "shipping-no-call", "goal": "Refuse an unrelated request."},
            )

    assert observed_response.status_code == HTTPStatus.OK
    assert "shipping_view" in observed_response.json()["output"]
    assert observed_response.json()["tool_calls"] == ["shipping-quote_shipment"]
    assert no_call_response.status_code == HTTPStatus.OK
    assert "shipping_view" in no_call_response.json()["output"]
    assert no_call_response.json()["tool_calls"] == []


@pytest.mark.asyncio
async def test_logistics_agent_refuses_pricing_goal(monkeypatch: pytest.MonkeyPatch) -> None:
    MockChatClient.reset(responses=['{"refuse":"pricing strategy belongs to pricing"}'])
    monkeypatch.setattr("src.agents.logistics.agent.GitHubCopilotAgent", MockChatClient)

    agent = await build_agent(Settings())
    response = await agent.run("Recommend price for ZS-1042 at store-101.")

    assert "refuse" in response.output_text
    assert "pricing" in SYSTEM_PROMPT
    assert "Never invent" in SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_logistics_agent_handles_mcp_tool_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    MockChatClient.reset(
        responses=['{"error":"shipping quote unavailable","retryable":true}'],
        tool_calls=[["shipping-quote_shipment"]],
        tool_results=[[RuntimeError("shipping MCP unavailable")]],
    )
    monkeypatch.setattr("src.agents.logistics.agent.GitHubCopilotAgent", MockChatClient)

    agent = await build_agent(Settings())
    result = await agent.run("Request a quote from store-202 to store-101.")

    assert "error" in result.output_text
    assert "unavailable" in result.output_text
    assert result.tool_calls == ["shipping-quote_shipment"]
