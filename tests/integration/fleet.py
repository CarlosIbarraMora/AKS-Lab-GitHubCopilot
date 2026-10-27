"""Reusable in-process ASGI fleet fixtures for A2A integration tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import ClassVar
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, HTTPStatusError

from src.agents.inventory.agent import build_agent as build_inventory_agent
from src.agents.logistics.agent import build_agent as build_logistics_agent
from src.agents.orchestrator.server import create_app as create_orchestrator_app
from src.agents.pricing.agent import build_agent as build_pricing_agent
from src.agents.supplier.agent import build_agent as build_supplier_agent
from src.shared.server import InvokeRequest, InvokeResponse, make_app
from src.shared.settings import Settings
from tests.agents._mock import MockChatClient

INVENTORY_OUTPUT = {
    "sku": "ZS-1042",
    "store_id": "store-101",
    "stock_view": {"on_hand": 6, "reserved": 4, "reorder_point": 24, "days_until_stockout": 2},
    "risk_level": "critical",
    "nearby_locations": [{"store_id": "store-202", "transfer_available_qty": 75}],
    "recommendation": "Prioritize the verified transfer from store-202.",
}
PRICING_OUTPUT = {
    "sku": "ZS-1042",
    "store_id": "store-101",
    "price_view": "Price recommendation is advisory.",
    "current_price": "39.99",
    "recommended_price": "41.19",
    "margin_impact": "Estimated unit margin remains positive.",
    "rationale": "Validated against demand and stock-risk inputs.",
}
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
LOGISTICS_OUTPUT = {
    "po_id": "draft-1",
    "sku": "ZS-1042",
    "source_location_id": "store-202",
    "destination_store_id": "store-101",
    "quantity": 80,
    "shipping_view": "Verified transfer route quote.",
}


class InventoryMockChatClient(MockChatClient):
    responses: ClassVar[list[str]] = []
    errors: ClassVar[list[Exception]] = []
    tool_calls: ClassVar[list[list[str]]] = []
    tool_results: ClassVar[list[list[object | Exception]]] = []


class SupplierMockChatClient(MockChatClient):
    responses: ClassVar[list[str]] = []
    errors: ClassVar[list[Exception]] = []
    tool_calls: ClassVar[list[list[str]]] = []
    tool_results: ClassVar[list[list[object | Exception]]] = []


class LogisticsMockChatClient(MockChatClient):
    responses: ClassVar[list[str]] = []
    errors: ClassVar[list[Exception]] = []
    tool_calls: ClassVar[list[list[str]]] = []
    tool_results: ClassVar[list[list[object | Exception]]] = []


class PricingMockChatClient(MockChatClient):
    responses: ClassVar[list[str]] = []
    errors: ClassVar[list[Exception]] = []
    tool_calls: ClassVar[list[list[str]]] = []
    tool_results: ClassVar[list[list[object | Exception]]] = []


class OrchestratorMockChatClient(MockChatClient):
    responses: ClassVar[list[str]] = []
    errors: ClassVar[list[Exception]] = []
    tool_calls: ClassVar[list[list[str]]] = []
    tool_results: ClassVar[list[list[object | Exception]]] = []


@dataclass
class ASGILoopbackInvoker:
    """Route actual workflow A2A calls to specialist FastAPI apps in-process."""

    settings: Settings
    apps: dict[str, FastAPI]
    events: list[tuple[str, str]]
    goals: dict[str, str]
    _parallel_steps: set[str]
    _parallel_started: asyncio.Event

    async def invoke(self, *, name: str, url: str, goal: str, run_id: str | None = None) -> str:
        if name not in self.apps:
            raise AssertionError(f"Unexpected specialist invocation: {name}")
        expected_urls = {
            "inventory": self.settings.inventory_a2a_url,
            "supplier": self.settings.supplier_a2a_url,
            "logistics": self.settings.logistics_a2a_url,
            "pricing": self.settings.pricing_a2a_url,
        }
        expected_url = expected_urls[name]
        if url != expected_url:
            raise AssertionError(f"Unexpected A2A URL for {name}")

        self.events.append((name, "started"))
        self.goals[name] = goal
        if name in {"inventory", "pricing"}:
            self._parallel_steps.add(name)
            if self._parallel_steps == {"inventory", "pricing"}:
                self._parallel_started.set()
            await asyncio.wait_for(self._parallel_started.wait(), timeout=5)
        elif name == "supplier":
            if not {("inventory", "completed"), ("pricing", "completed")} <= set(self.events):
                raise AssertionError("Supplier ran before inventory and pricing completed")
        elif name == "logistics" and ("supplier", "completed") not in self.events:
            raise AssertionError("Logistics ran before Supplier completed")

        request = InvokeRequest(
            run_id=run_id or uuid4().hex,
            goal=goal,
            context={"caller": "orchestrator"},
        )
        try:
            async with AsyncClient(
                transport=ASGITransport(app=self.apps[name]),
                base_url=f"http://{name}.test",
            ) as client:
                response = await client.post("/invoke", json=request.model_dump())
            response.raise_for_status()
            envelope = InvokeResponse.model_validate(response.json())
        except HTTPStatusError:
            self.events.append((name, "failed"))
            raise

        self.events.append((name, "completed"))
        return envelope.output


@dataclass
class ASGIFleet:
    client: AsyncClient
    invoker: ASGILoopbackInvoker
    supplier_chat: type[MockChatClient]
    orchestrator_chat: type[MockChatClient]


@pytest.fixture
async def fleet(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[ASGIFleet]:
    """Start real FastAPI apps with isolated mocks and no Azure configuration."""

    monkeypatch.setattr("src.agents.inventory.agent.GitHubCopilotAgent", InventoryMockChatClient)
    monkeypatch.setattr("src.agents.supplier.agent.GitHubCopilotAgent", SupplierMockChatClient)
    monkeypatch.setattr("src.agents.logistics.agent.GitHubCopilotAgent", LogisticsMockChatClient)
    monkeypatch.setattr("src.agents.pricing.agent.GitHubCopilotAgent", PricingMockChatClient)
    monkeypatch.setattr("src.agents.orchestrator.agent.GitHubCopilotAgent", OrchestratorMockChatClient)

    settings = Settings(
        inventory_a2a_url="http://inventory.test/invoke",
        supplier_a2a_url="http://supplier.test/invoke",
        logistics_a2a_url="http://logistics.test/invoke",
        pricing_a2a_url="http://pricing.test/invoke",
        kv_url=None,
    )
    InventoryMockChatClient.reset(responses=[json.dumps(INVENTORY_OUTPUT)])
    SupplierMockChatClient.reset(
        responses=["Supplier output is normalized from MCP evidence."],
        tool_calls=[["supplier-list_suppliers", "supplier-draft_po"]],
        tool_results=[[SUPPLIER_CATALOG, SUPPLIER_DRAFT]],
    )
    LogisticsMockChatClient.reset(responses=[json.dumps(LOGISTICS_OUTPUT)])
    PricingMockChatClient.reset(responses=[json.dumps(PRICING_OUTPUT)])
    OrchestratorMockChatClient.reset(responses=["Plan orchestration completed."])

    specialist_apps = {
        "inventory": make_app(name="inventory", build=build_inventory_agent, settings=settings),
        "supplier": make_app(name="supplier", build=build_supplier_agent, settings=settings),
        "logistics": make_app(name="logistics", build=build_logistics_agent, settings=settings),
        "pricing": make_app(name="pricing", build=build_pricing_agent, settings=settings),
    }
    invoker = ASGILoopbackInvoker(
        settings=settings,
        apps=specialist_apps,
        events=[],
        goals={},
        _parallel_steps=set(),
        _parallel_started=asyncio.Event(),
    )
    orchestrator_app = create_orchestrator_app(settings=settings, specialist_client=invoker)

    async with AsyncExitStack() as stack:
        for app in (*specialist_apps.values(), orchestrator_app):
            await stack.enter_async_context(app.router.lifespan_context(app))
        client = await stack.enter_async_context(
            AsyncClient(transport=ASGITransport(app=orchestrator_app), base_url="http://orchestrator.test")
        )
        yield ASGIFleet(
            client=client,
            invoker=invoker,
            supplier_chat=SupplierMockChatClient,
            orchestrator_chat=OrchestratorMockChatClient,
        )
