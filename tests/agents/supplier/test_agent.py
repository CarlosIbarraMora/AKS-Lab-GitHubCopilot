"""Unit tests for the ZavaShop supplier specialist agent."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import cast

import pytest
from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject
from copilot.generated.session_events import PermissionRequestCustomTool, PermissionRequestMcp, PermissionRequestRead
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from src.agents.supplier.agent import (
    _approve_supplier_operations,  # pyright: ignore[reportPrivateUsage]
    build_agent,
)
from src.agents.supplier.prompts import SYSTEM_PROMPT
from src.shared.server import make_app
from src.shared.settings import Settings
from src.shared.supplier_contracts import SupplierAgentOutput
from tests.agents._mock import MockChatClient, captured_options

SUPPLIER_CATALOG = {
    "sku": "ZS-1042",
    "suppliers": [
        {
            "supplier_id": "sup-fabrikam-rapid",
            "name": "Fabrikam Rapid Replenishment",
            "available_qty": 240,
            "unit_cost": 20.1,
            "lead_time_days": 2,
            "reliability_score": 0.88,
        },
        {
            "supplier_id": "sup-northstar",
            "name": "Northstar Apparel Manufacturing",
            "available_qty": 900,
            "unit_cost": 18.75,
            "lead_time_days": 4,
            "reliability_score": 0.97,
        },
    ],
}
SUPPLIER_DRAFT = {
    "sku": "ZS-1042",
    "store_id": "store-101",
    "supplier_id": "sup-fabrikam-rapid",
    "quantity": 80,
    "unit_cost": 20.1,
    "estimated_delivery_days": 2,
    "status": "draft",
    "rationale": "Draft only; approval is required before ordering.",
}
SUPPLIER_TOOL_CALLS = ["supplier-list_suppliers", "supplier-draft_po"]
SUPPLIER_TOOL_RESULTS = [SUPPLIER_CATALOG, SUPPLIER_DRAFT]
PLANNED_QUANTITY = 80
SELECTED_UNIT_COST = 20.1
SELECTED_LEAD_TIME_DAYS = 2


def _mcp_servers(options: Mapping[str, object]) -> Mapping[str, object]:
    servers = options.get("mcp_servers")
    if not isinstance(servers, Mapping):
        raise AssertionError("mcp_servers was not configured")
    return cast("Mapping[str, object]", servers)


@pytest.mark.asyncio
async def test_supplier_agent_uses_supplier_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    MockChatClient.reset(
        responses=['{"recommended_supplier":"model-invented"}'],
        tool_calls=[SUPPLIER_TOOL_CALLS],
        tool_results=[SUPPLIER_TOOL_RESULTS],
    )
    monkeypatch.setattr("src.agents.supplier.agent.GitHubCopilotAgent", MockChatClient)

    settings = Settings(supplier_mcp_url="http://supplier.local/mcp")
    agent = await build_agent(settings)
    response = await agent.run("Draft a purchase order for SKU ZS-1042 at store-101.")
    output = SupplierAgentOutput.model_validate_json(response.output_text)
    options = captured_options()
    mcp_servers = _mcp_servers(options)

    assert output.sku == "ZS-1042"
    assert output.store_id == "store-101"
    assert output.recommended_supplier == "sup-fabrikam-rapid"
    assert output.po_view.quantity == PLANNED_QUANTITY
    assert output.po_view.approval_required is True
    supplier_name = output.supplier_view.suppliers[0].name
    assert supplier_name is not None
    assert "Fabrikam Rapid Replenishment" in supplier_name
    assert "supplier" in mcp_servers
    assert "gpt-5.5" == options.get("model")
    assert settings.copilot_timeout_seconds == options.get("timeout")


@pytest.mark.asyncio
async def test_differently_worded_supplier_responses_normalize_to_identical_verified_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    MockChatClient.reset(
        responses=[
            '{"supplier_view":[],"po_view":{}}',
            '{"supplier_view":{"suppliers":[]},"next_action":"model wording B"}',
            "Supplier response: selected a supplier and drafted an order.",
        ],
        tool_calls=[SUPPLIER_TOOL_CALLS] * 3,
        tool_results=[SUPPLIER_TOOL_RESULTS] * 3,
    )
    monkeypatch.setattr("src.agents.supplier.agent.GitHubCopilotAgent", MockChatClient)
    agent = await build_agent(Settings())

    outputs = [
        SupplierAgentOutput.model_validate_json((await agent.run("Prepare the advisory draft.")).output_text)
        for _ in range(3)
    ]

    assert outputs[0] == outputs[1] == outputs[2]
    assert outputs[0].recommended_supplier == "sup-fabrikam-rapid"
    assert outputs[0].po_view.quantity == PLANNED_QUANTITY
    assert outputs[0].po_view.unit_cost == SELECTED_UNIT_COST
    assert outputs[0].po_view.estimated_delivery_days == SELECTED_LEAD_TIME_DAYS
    assert outputs[0].po_view.approval_required is True


@pytest.mark.parametrize(
    ("tool_calls", "tool_results"),
    [
        (SUPPLIER_TOOL_CALLS, [SUPPLIER_CATALOG]),
        (SUPPLIER_TOOL_CALLS, ["not-json", SUPPLIER_DRAFT]),
        ([*SUPPLIER_TOOL_CALLS, "supplier-list_suppliers"], [*SUPPLIER_TOOL_RESULTS, SUPPLIER_CATALOG]),
        (SUPPLIER_TOOL_CALLS, [RuntimeError("supplier MCP unavailable"), SUPPLIER_DRAFT]),
        (
            SUPPLIER_TOOL_CALLS,
            [SUPPLIER_CATALOG, {**SUPPLIER_DRAFT, "supplier_id": "supplier-not-listed"}],
        ),
        (SUPPLIER_TOOL_CALLS, [SUPPLIER_CATALOG, {**SUPPLIER_DRAFT, "quantity": 241}]),
        (SUPPLIER_TOOL_CALLS, [SUPPLIER_CATALOG, {**SUPPLIER_DRAFT, "unit_cost": 18.75}]),
        (SUPPLIER_TOOL_CALLS, [SUPPLIER_CATALOG, {**SUPPLIER_DRAFT, "estimated_delivery_days": 4}]),
        (SUPPLIER_TOOL_CALLS, [SUPPLIER_CATALOG, {**SUPPLIER_DRAFT, "rationale": "Draft for review."}]),
    ],
)
@pytest.mark.asyncio
async def test_supplier_agent_fails_explicitly_for_invalid_mcp_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tool_calls: list[str],
    tool_results: list[object | Exception],
) -> None:
    MockChatClient.reset(
        responses=['{"sku":"ZS-1042","store_id":"store-101"}'],
        tool_calls=[tool_calls],
        tool_results=[tool_results],
    )
    monkeypatch.setattr("src.agents.supplier.agent.GitHubCopilotAgent", MockChatClient)
    agent = await build_agent(Settings())

    with pytest.raises((RuntimeError, ValidationError)):
        await agent.run("Draft a purchase order.")


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
                responses=['{"supplier_view":"model-generated value is ignored"}'],
                tool_calls=[SUPPLIER_TOOL_CALLS],
                tool_results=[SUPPLIER_TOOL_RESULTS],
            )
            observed_response = await client.post(
                "/invoke",
                json={"run_id": "supplier-observed", "goal": "Find suppliers and draft a purchase order."},
            )

            MockChatClient.reset(responses=['{"refusal":"shipment quotes belong to logistics"}'])
            no_call_response = await client.post(
                "/invoke",
                json={"run_id": "supplier-no-call", "goal": "Refuse an unrelated request."},
            )

    assert observed_response.status_code == HTTPStatus.OK
    assert "supplier_view" in observed_response.json()["output"]
    assert observed_response.json()["tool_calls"] == ["supplier-list_suppliers", "supplier-draft_po"]
    assert no_call_response.status_code == HTTPStatus.OK
    assert "refusal" in no_call_response.json()["output"]
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
