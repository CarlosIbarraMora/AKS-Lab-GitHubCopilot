"""Unit tests for the ZavaShop supplier specialist agent."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import cast

import pytest
from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject
from copilot.generated.session_events import PermissionRequestCustomTool, PermissionRequestMcp, PermissionRequestRead
from httpx import ASGITransport, AsyncClient

from src.agents.supplier.agent import (
    _approve_supplier_operations,  # pyright: ignore[reportPrivateUsage]
    build_agent,
)
from src.agents.supplier.prompts import SYSTEM_PROMPT
from src.shared.server import make_app
from src.shared.settings import Settings
from tests.agents._mock import MockChatClient, captured_options


def _mcp_servers(options: Mapping[str, object]) -> Mapping[str, object]:
    servers = options.get("mcp_servers")
    if not isinstance(servers, Mapping):
        raise AssertionError("mcp_servers was not configured")
    return cast("Mapping[str, object]", servers)


@pytest.mark.asyncio
async def test_supplier_agent_uses_supplier_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    MockChatClient.reset(responses=['{"sku":"ZS-1042","store_id":"store-101","po_view":"PO-ZS1042-STORE101"}'])
    monkeypatch.setattr("src.agents.supplier.agent.GitHubCopilotAgent", MockChatClient)

    settings = Settings(supplier_mcp_url="http://supplier.local/mcp")
    agent = await build_agent(settings)
    response = await agent.run("Draft a purchase order for SKU ZS-1042 at store-101.")
    options = captured_options()
    mcp_servers = _mcp_servers(options)

    assert "PO-ZS1042-STORE101" in response.output_text
    assert "supplier" in mcp_servers
    assert "gpt-5.5" == options.get("model")
    assert settings.copilot_timeout_seconds == options.get("timeout")


def test_supplier_permission_approves_only_allowed_sdk_mcp_operations() -> None:
    for server_name in ("supplier", "supplier-mcp"):
        for tool_name in ("list_suppliers", "supplier-list_suppliers", "draft_po", "supplier-draft_po"):
            request = PermissionRequestMcp(
                read_only=True,
                server_name=server_name,
                tool_name=tool_name,
                tool_title=tool_name,
            )
            assert isinstance(_approve_supplier_operations(request, {}), PermissionDecisionApproveOnce)

    unrelated_tool = PermissionRequestMcp(
        read_only=True,
        server_name="supplier",
        tool_name="delete_supplier",
        tool_title="Delete supplier",
    )
    unauthorized_server = PermissionRequestMcp(
        read_only=True,
        server_name="untrusted-supplier",
        tool_name="supplier-draft_po",
        tool_title="Draft purchase order",
    )
    file_read = PermissionRequestRead(intention="Read a local file", path="C:/private.txt")
    unrelated_custom_tool = PermissionRequestCustomTool(
        tool_name="draft_po",
        tool_description="External custom tool.",
    )

    assert isinstance(_approve_supplier_operations(unrelated_tool, {}), PermissionDecisionReject)
    assert isinstance(_approve_supplier_operations(unauthorized_server, {}), PermissionDecisionReject)
    assert isinstance(_approve_supplier_operations(file_read, {}), PermissionDecisionReject)
    assert isinstance(_approve_supplier_operations(unrelated_custom_tool, {}), PermissionDecisionReject)


@pytest.mark.asyncio
async def test_supplier_invoke_reports_only_sdk_observed_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.agents.supplier.agent.GitHubCopilotAgent", MockChatClient)
    app = make_app(name="supplier", build=build_agent, settings=Settings())

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            MockChatClient.reset(
                responses=['{"supplier_view":"approved suppliers"}'],
                tool_calls=[["supplier-list_suppliers", "supplier-draft_po"]],
            )
            observed_response = await client.post(
                "/invoke",
                json={"run_id": "supplier-observed", "goal": "Find suppliers and draft a purchase order."},
            )

            MockChatClient.reset(responses=['{"supplier_view":"no supplier lookup needed"}'])
            no_call_response = await client.post(
                "/invoke",
                json={"run_id": "supplier-no-call", "goal": "Refuse an unrelated request."},
            )

    assert observed_response.status_code == HTTPStatus.OK
    assert "supplier_view" in observed_response.json()["output"]
    assert observed_response.json()["tool_calls"] == ["supplier-list_suppliers", "supplier-draft_po"]
    assert no_call_response.status_code == HTTPStatus.OK
    assert "supplier_view" in no_call_response.json()["output"]
    assert no_call_response.json()["tool_calls"] == []


@pytest.mark.asyncio
async def test_supplier_agent_refuses_shipping_goal(monkeypatch: pytest.MonkeyPatch) -> None:
    MockChatClient.reset(responses=['{"refuse":"shipment quotes belong to logistics"}'])
    monkeypatch.setattr("src.agents.supplier.agent.GitHubCopilotAgent", MockChatClient)

    agent = await build_agent(Settings())
    response = await agent.run("Quote shipment for PO-ZS1042-STORE101.")

    assert "refuse" in response.output_text
    assert "shipment" in SYSTEM_PROMPT
    assert "Never invent" in SYSTEM_PROMPT
