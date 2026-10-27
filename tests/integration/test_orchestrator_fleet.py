"""In-process API integration tests for the Orchestrator and specialist fleet."""

from __future__ import annotations

from http import HTTPStatus
from typing import Protocol

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from src.agents.orchestrator.contracts import Plan
from src.shared.server import InvokeResponse
from tests.agents._mock import MockChatClient


class RecordedInvoker(Protocol):
    events: list[tuple[str, str]]
    goals: dict[str, str]


class IntegrationFleet(Protocol):
    client: AsyncClient
    invoker: RecordedInvoker
    supplier_chat: type[MockChatClient]
    orchestrator_chat: type[MockChatClient]


@pytest.mark.asyncio
async def test_plan_routes_through_workflow_and_loopback_fleet(fleet: IntegrationFleet) -> None:
    response = await fleet.client.post(
        "/plan",
        json={
            "goal": "Recover the critical stock-out using verified transfer and replenishment options.",
            "sku": "ZS-1042",
            "store_id": "store-101",
        },
    )

    assert response.status_code == HTTPStatus.OK
    plan = Plan.model_validate(response.json())
    assert plan.sku == "ZS-1042"
    assert plan.store_id == "store-101"
    assert "critical" in plan.stock_view
    assert "41.19" in plan.price_view
    assert "sup-fabrikam-rapid" in plan.po_view
    assert "store-202" in plan.shipping_view
    assert plan.next_actions

    started = {name: index for index, (name, state) in enumerate(fleet.invoker.events) if state == "started"}
    completed = {name: index for index, (name, state) in enumerate(fleet.invoker.events) if state == "completed"}
    assert set(started) == {"inventory", "pricing", "supplier", "logistics"}
    assert max(started["inventory"], started["pricing"]) < min(completed["inventory"], completed["pricing"])
    assert started["supplier"] > max(completed["inventory"], completed["pricing"])
    assert started["logistics"] > completed["supplier"]
    assert '"on_hand": 6' in fleet.invoker.goals["supplier"]
    assert "sup-fabrikam-rapid" in fleet.invoker.goals["logistics"]
    assert "store-202" in fleet.invoker.goals["logistics"]
    assert "approval_required" in fleet.invoker.goals["logistics"]
    assert "true" in fleet.invoker.goals["logistics"]


@pytest.mark.asyncio
async def test_plan_supplier_failure_skips_logistics_without_fabricated_findings(fleet: IntegrationFleet) -> None:
    fleet.supplier_chat.reset(errors=[RuntimeError("simulated supplier MCP failure")])

    response = await fleet.client.post(
        "/plan",
        json={
            "goal": "Recover the critical stock-out using verified evidence.",
            "sku": "ZS-1042",
            "store_id": "store-101",
        },
    )

    assert response.status_code == HTTPStatus.OK
    plan = Plan.model_validate(response.json())
    assert "failed" in plan.po_view
    assert "skipped" in plan.shipping_view
    assert "supplier_id" not in plan.po_view
    assert '"quantity": 80' not in plan.po_view
    assert not any(name == "logistics" for name, state in fleet.invoker.events if state == "started")
    assert ("supplier", "failed") in fleet.invoker.events
    assert "verified transfer" in plan.stock_view
    assert "41.19" in plan.price_view


@pytest.mark.asyncio
async def test_invoke_returns_valid_a2a_envelope_and_observed_specialist_calls(fleet: IntegrationFleet) -> None:
    expected_calls = ["ask_inventory", "ask_supplier", "ask_logistics", "ask_pricing"]
    fleet.orchestrator_chat.reset(
        responses=['{"summary":"Recovery plan ready for ZS-1042 at store-101."}'],
        tool_calls=[expected_calls],
    )

    response = await fleet.client.post(
        "/invoke",
        json={"run_id": "invoke-success", "goal": "Build a recovery plan for ZS-1042 at store-101."},
    )

    assert response.status_code == HTTPStatus.OK
    envelope = InvokeResponse.model_validate(response.json())
    assert envelope.run_id == "invoke-success"
    assert "ZS-1042" in envelope.output
    assert "store-101" in envelope.output
    assert envelope.tool_calls == expected_calls


@pytest.mark.asyncio
async def test_invoke_propagates_agent_failure_without_business_fallback(fleet: IntegrationFleet) -> None:
    fleet.orchestrator_chat.reset(errors=[RuntimeError("simulated Copilot invocation failure")])

    response = await fleet.client.post(
        "/invoke",
        json={"run_id": "invoke-failure", "goal": "Build a recovery plan for ZS-1042 at store-101."},
    )

    assert response.status_code == HTTPStatus.BAD_GATEWAY
    payload = response.json()
    assert payload["detail"] == "agent run failed"
    assert "recommended_price" not in response.text
    assert "supplier_id" not in response.text
    with pytest.raises(ValidationError):
        InvokeResponse.model_validate(payload)
