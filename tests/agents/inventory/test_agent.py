"""Unit tests for the ZavaShop inventory specialist agent."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import cast

import pytest
from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject
from copilot.generated.session_events import PermissionRequestCustomTool, PermissionRequestMcp, PermissionRequestRead
from httpx import ASGITransport, AsyncClient

from src.agents.inventory.agent import (
    _approve_inventory_stock_check,  # pyright: ignore[reportPrivateUsage]
    build_agent,
)
from src.agents.inventory.prompts import SYSTEM_PROMPT
from src.shared.server import make_app
from src.shared.settings import Settings
from tests.agents._mock import MockChatClient, captured_options


def _mcp_servers(options: Mapping[str, object]) -> Mapping[str, object]:
    servers = options.get("mcp_servers")
    if not isinstance(servers, Mapping):
        raise AssertionError("mcp_servers was not configured")
    return cast("Mapping[str, object]", servers)


@pytest.mark.asyncio
async def test_inventory_agent_happy_path_uses_inventory_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    MockChatClient.reset(
        responses=[
            '{"sku":"ZS-1042","store_id":"store-101","stock_view":"stock at risk",'
            '"risk_level":"high","nearby_locations":["store-202","wh-east"],'
            '"recommendation":"transfer stock"}'
        ]
    )
    monkeypatch.setattr("src.agents.inventory.agent.GitHubCopilotAgent", MockChatClient)

    settings = Settings(inventory_mcp_url="http://inventory.local/mcp")
    agent = await build_agent(settings)
    response = await agent.run("Check stock for SKU ZS-1042 at store-101.")
    options = captured_options()
    mcp_servers = _mcp_servers(options)

    assert "ZS-1042" in response.output_text
    assert "store-101" in response.output_text
    assert "stock" in response.output_text
    assert "inventory" in mcp_servers
    inventory_server = mcp_servers["inventory"]
    assert isinstance(inventory_server, Mapping)
    assert inventory_server["url"] == settings.inventory_mcp_url
    assert inventory_server["tools"] == ["check_stock"]
    assert inventory_server["timeout"] == int(settings.copilot_timeout_seconds * 1000)
    assert options["on_permission_request"] is _approve_inventory_stock_check
    assert "check_stock" in SYSTEM_PROMPT
    assert "query object" in SYSTEM_PROMPT
    assert "gpt-5.5" == options.get("model")
    assert settings.copilot_timeout_seconds == options.get("timeout")
    assert MockChatClient.messages == ["Check stock for SKU ZS-1042 at store-101."]


def test_inventory_permission_approves_only_the_sdk_mcp_stock_tool() -> None:
    stock_check = PermissionRequestMcp(
        read_only=True,
        server_name="inventory",
        tool_name="check_stock",
        tool_title="Check stock",
        args={"query": {"sku": "ZS-1042", "store_id": "store-101"}},
    )
    sdk_prefixed_stock_check = PermissionRequestMcp(
        read_only=True,
        server_name="inventory",
        tool_name="inventory-check_stock",
        tool_title="Check stock",
        args={"query": {"sku": "ZS-1042", "store_id": "store-101"}},
    )
    protocol_named_stock_check = PermissionRequestMcp(
        read_only=True,
        server_name="inventory-mcp",
        tool_name="check_stock",
        tool_title="Check stock",
        args={"query": {"sku": "ZS-1042", "store_id": "store-101"}},
    )
    untrusted_server_stock_check = PermissionRequestMcp(
        read_only=True,
        server_name="untrusted-inventory",
        tool_name="check_stock",
        tool_title="Check stock",
    )
    unrelated_mcp_tool = PermissionRequestMcp(
        read_only=True,
        server_name="inventory",
        tool_name="delete_stock",
        tool_title="Delete stock",
    )
    file_read = PermissionRequestRead(intention="Read a local file", path="C:/private.txt")
    unrelated_custom_tool = PermissionRequestCustomTool(
        tool_name="check_stock",
        tool_description="Untrusted tool from another source.",
    )

    assert isinstance(_approve_inventory_stock_check(stock_check, {}), PermissionDecisionApproveOnce)
    assert isinstance(_approve_inventory_stock_check(sdk_prefixed_stock_check, {}), PermissionDecisionApproveOnce)
    assert isinstance(_approve_inventory_stock_check(protocol_named_stock_check, {}), PermissionDecisionApproveOnce)
    assert isinstance(_approve_inventory_stock_check(untrusted_server_stock_check, {}), PermissionDecisionReject)
    assert isinstance(_approve_inventory_stock_check(unrelated_mcp_tool, {}), PermissionDecisionReject)
    assert isinstance(_approve_inventory_stock_check(file_read, {}), PermissionDecisionReject)
    assert isinstance(_approve_inventory_stock_check(unrelated_custom_tool, {}), PermissionDecisionReject)


@pytest.mark.asyncio
async def test_inventory_invoke_reports_sdk_observed_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    MockChatClient.reset(
        responses=['{"sku":"ZS-1042","store_id":"store-101","risk_level":"critical"}'],
        tool_calls=[["inventory-check_stock"]],
    )
    monkeypatch.setattr("src.agents.inventory.agent.GitHubCopilotAgent", MockChatClient)
    app = make_app(name="inventory", build=build_agent, settings=Settings())

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/invoke",
                json={"run_id": "inventory-smoke", "goal": "Check ZS-1042 at store-101"},
            )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["output"] == ('{"sku":"ZS-1042","store_id":"store-101","risk_level":"critical"}')
    assert response.json()["tool_calls"] == ["inventory-check_stock"]


@pytest.mark.asyncio
async def test_inventory_agent_refuses_out_of_scope_supplier_goal(monkeypatch: pytest.MonkeyPatch) -> None:
    MockChatClient.reset(
        responses=[
            '{"refuse":"supplier and purchase order requests belong to the supplier specialist","redirect":"supplier"}'
        ]
    )
    monkeypatch.setattr("src.agents.inventory.agent.GitHubCopilotAgent", MockChatClient)

    agent = await build_agent(Settings())
    response = await agent.run("Draft a purchase order for supplier replenishment of ZS-1042.")

    assert "refuse" in response.output_text
    assert "supplier" in response.output_text
    assert "purchase-order" in SYSTEM_PROMPT
    assert "Never invent" in SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_inventory_agent_surfaces_tool_error_once(monkeypatch: pytest.MonkeyPatch) -> None:
    MockChatClient.reset(errors=[RuntimeError("inventory MCP unavailable")])
    monkeypatch.setattr("src.agents.inventory.agent.GitHubCopilotAgent", MockChatClient)

    agent = await build_agent(Settings())

    with pytest.raises(RuntimeError, match="inventory MCP unavailable"):
        await agent.run("Check stock for SKU ZS-1042 at store-101.")

    assert MockChatClient.messages == ["Check stock for SKU ZS-1042 at store-101."]
