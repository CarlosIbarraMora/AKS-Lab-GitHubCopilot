"""Regression tests for the deterministic orchestrator workflow and API contracts."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from http import HTTPStatus

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from src.agents.orchestrator import server as orchestrator_server
from src.agents.orchestrator import workflow as workflow_module
from src.agents.orchestrator.contracts import Goal, Plan
from src.agents.orchestrator.workflow import build_plan_workflow, run_plan_workflow
from src.shared.server import InvokeRequest, InvokeResponse
from src.shared.settings import Settings

SUCCESS_OUTPUTS = {
    "inventory": json.dumps(
        {
            "sku": "ZS-1042",
            "store_id": "store-101",
            "stock_view": "verified stock risk at store-101",
            "risk_level": "high",
            "nearby_locations": [{"transfer_source_location_id": "store-202"}],
            "recommendation": "replenish",
        }
    ),
    "pricing": json.dumps(
        {
            "sku": "ZS-1042",
            "store_id": "store-101",
            "price_view": "verified pricing recommendation",
            "current_price": "10.00",
            "recommended_price": "9.50",
            "margin_impact": "review",
            "rationale": "test fixture",
        }
    ),
    "supplier": json.dumps(
        {
            "sku": "ZS-1042",
            "store_id": "store-101",
            "supplier_view": {
                "sku": "ZS-1042",
                "suppliers": [
                    {
                        "supplier_id": "supplier-a",
                        "name": "Supplier A",
                        "available_qty": 240,
                        "unit_cost": 20.1,
                        "lead_time_days": 2,
                        "reliability_score": 0.88,
                    }
                ],
            },
            "po_view": {
                "supplier_id": "supplier-a",
                "quantity": 80,
                "unit_cost": 20.1,
                "estimated_delivery_days": 2,
                "status": "draft",
                "approval_required": True,
                "rationale": "Verified advisory purchase draft; approval is required before ordering.",
            },
            "recommended_supplier": "supplier-a",
            "next_action": "Review the advisory draft; approval is required before ordering.",
        }
    ),
    "logistics": json.dumps(
        {
            "po_id": "draft-1",
            "sku": "ZS-1042",
            "source_location_id": "store-202",
            "destination_store_id": "store-101",
            "quantity": 80,
            "shipping_view": "verified shipping quote",
        }
    ),
}


@dataclass
class WorkflowInvoker:
    responses: dict[str, str | Exception] = field(default_factory=lambda: dict(SUCCESS_OUTPUTS))
    hanging: set[str] = field(default_factory=set[str])
    started: set[str] = field(default_factory=set[str])
    completed: set[str] = field(default_factory=set[str])
    events: list[str] = field(default_factory=list[str])
    goals: dict[str, str] = field(default_factory=dict[str, str])
    both_parallel_steps_started: asyncio.Event = field(default_factory=asyncio.Event)
    settings: Settings = field(default_factory=Settings)

    async def invoke(self, *, name: str, url: str, goal: str, run_id: str | None = None) -> str:
        del url, run_id
        self.events.append(f"{name}_started")
        self.goals[name] = goal
        if name in {"inventory", "pricing"}:
            self.started.add(name)
            if self.started == {"inventory", "pricing"}:
                self.both_parallel_steps_started.set()
            await asyncio.wait_for(self.both_parallel_steps_started.wait(), timeout=1)
        elif name == "supplier":
            assert {"inventory", "pricing"} <= self.completed
        elif name == "logistics":
            assert "supplier" in self.completed
            assert '"approval_required": true' in goal
            assert '"supplier_id": "supplier-a"' in goal
        else:
            raise AssertionError(f"Unexpected specialist call: {name}")

        if name in self.hanging:
            await asyncio.Event().wait()

        response = self.responses[name]
        if isinstance(response, Exception):
            raise response

        self.completed.add(name)
        self.events.append(f"{name}_completed")
        return response


@pytest.mark.asyncio
async def test_workflow_runs_inventory_and_pricing_concurrently_then_supplier_shipping_and_reconcile() -> None:
    invoker = WorkflowInvoker()
    goal = Goal(
        goal="Recover the stock-out before Friday.",
        sku="ZS-1042",
        store_id="store-101",
    )

    plan = await run_plan_workflow(build_plan_workflow(invoker), goal)

    assert isinstance(plan, Plan)
    assert invoker.started == {"inventory", "pricing"}
    assert invoker.events.index("supplier_started") > invoker.events.index("inventory_completed")
    assert invoker.events.index("supplier_started") > invoker.events.index("pricing_completed")
    assert invoker.events.index("logistics_started") > invoker.events.index("supplier_completed")
    assert invoker.events[-1] in {"logistics_completed", "supplier_completed"}
    assert all("ZS-1042" in request and "store-101" in request for request in invoker.goals.values())
    assert "verified stock risk" in plan.stock_view
    assert "verified pricing recommendation" in plan.price_view
    assert "Verified advisory purchase draft" in plan.po_view
    assert '"approval_required": true' in plan.po_view
    assert '"supplier_id": "supplier-a"' in plan.po_view
    assert "verified shipping quote" in plan.shipping_view
    assert "no purchase order" in plan.summary
    assert "shipment" in plan.summary
    assert any("no shipment" in action for action in plan.next_actions)
    assert "price change was executed" in plan.summary
    assert any("no price change" in action for action in plan.next_actions)
    assert "80 units" in invoker.goals["supplier"]
    assert "supplier capacity" in invoker.goals["supplier"]
    assert "available supplier capacity as the order quantity" in invoker.goals["supplier"]


@pytest.mark.parametrize(
    ("response", "expected_reason"),
    [
        ("{", "malformed_specialist_output"),
        ("{}", "malformed_specialist_output"),
        ("", "empty_or_invalid_output"),
        ("I cannot help with this request.", "specialist_refused_or_reported_unavailable"),
    ],
)
@pytest.mark.asyncio
async def test_malformed_empty_and_refused_inventory_results_skip_dependents_without_fabrication(
    response: str, expected_reason: str
) -> None:
    invoker = WorkflowInvoker(responses={**SUCCESS_OUTPUTS, "inventory": response})

    plan = await run_plan_workflow(
        build_plan_workflow(invoker),
        Goal(goal="Recover stock.", sku="ZS-1042", store_id="store-101"),
    )

    assert "failed" in plan.stock_view
    assert expected_reason in plan.stock_view
    assert "skipped" in plan.po_view
    assert "skipped" in plan.shipping_view
    assert "verified pricing recommendation" in plan.price_view
    assert "supplier" not in invoker.goals
    assert "logistics" not in invoker.goals
    assert "verified stock risk" not in plan.stock_view
    assert plan.next_actions


@pytest.mark.parametrize(
    "invalid_supplier",
    [
        {"approval_required": False},
        {"quantity": 0},
        {"quantity": True},
        {"quantity": 241},
        {"unit_cost": 19.0},
        {"estimated_delivery_days": 4},
        {"supplier_id": "unlisted-supplier"},
        {"status": "submitted"},
    ],
)
@pytest.mark.asyncio
async def test_invalid_supplier_contract_skips_logistics_without_fabrication(
    invalid_supplier: dict[str, object],
) -> None:
    supplier_data = json.loads(SUCCESS_OUTPUTS["supplier"])
    supplier_data["po_view"].update(invalid_supplier)
    invoker = WorkflowInvoker(responses={**SUCCESS_OUTPUTS, "supplier": json.dumps(supplier_data)})

    plan = await run_plan_workflow(
        build_plan_workflow(invoker),
        Goal(goal="Recover stock.", sku="ZS-1042", store_id="store-101"),
    )

    assert "verified stock risk" in plan.stock_view
    assert "verified pricing recommendation" in plan.price_view
    assert "malformed_specialist_output" in plan.po_view
    assert "skipped" in plan.shipping_view
    assert "logistics" not in invoker.goals
    assert "verified shipping quote" not in plan.shipping_view


@pytest.mark.parametrize("error", [httpx.ConnectError("connection failed"), httpx.ReadTimeout("read timed out")])
@pytest.mark.asyncio
async def test_a2a_connection_and_read_timeouts_do_not_abort_pricing(error: Exception) -> None:
    invoker = WorkflowInvoker(responses={**SUCCESS_OUTPUTS, "inventory": error})

    plan = await run_plan_workflow(
        build_plan_workflow(invoker),
        Goal(goal="Recover stock.", sku="ZS-1042", store_id="store-101"),
    )

    assert ("timed_out" if isinstance(error, httpx.TimeoutException) else "failed") in plan.stock_view
    assert "verified pricing recommendation" in plan.price_view
    assert "skipped" in plan.po_view
    assert "skipped" in plan.shipping_view
    assert "verified stock risk" not in plan.stock_view


@pytest.mark.asyncio
async def test_workflow_timeout_is_explicit_and_inventory_dependents_are_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setattr(workflow_module, "SPECIALIST_TIMEOUT_SECONDS", 0.01)
    invoker = WorkflowInvoker(hanging={"inventory"})

    plan = await run_plan_workflow(
        build_plan_workflow(invoker),
        Goal(goal="Recover stock.", sku="ZS-1042", store_id="store-101"),
    )

    assert "timed_out" in plan.stock_view
    assert "specialist_timeout" in plan.stock_view
    assert "verified pricing recommendation" in plan.price_view
    assert "skipped" in plan.po_view
    assert "skipped" in plan.shipping_view


@pytest.mark.asyncio
async def test_supplier_failure_skips_shipping_but_preserves_actual_inventory_and_pricing() -> None:
    invoker = WorkflowInvoker(responses={**SUCCESS_OUTPUTS, "supplier": httpx.ConnectError("supplier unavailable")})

    plan = await run_plan_workflow(
        build_plan_workflow(invoker),
        Goal(goal="Recover stock.", sku="ZS-1042", store_id="store-101"),
    )

    assert "verified stock risk" in plan.stock_view
    assert "verified pricing recommendation" in plan.price_view
    assert "failed" in plan.po_view
    assert "skipped" in plan.shipping_view
    assert "logistics" not in invoker.goals
    assert "verified shipping quote" not in plan.shipping_view


@dataclass(frozen=True)
class FakeRunResult:
    output_text: str
    tool_calls: list[str]


@dataclass(frozen=True)
class FakeRunnable:
    async def run(self, message: str, /) -> FakeRunResult:
        return FakeRunResult(output_text=f"mock invocation for {message}", tool_calls=["mock_tool"])


@dataclass(frozen=True)
class ContractInvoker:
    settings: Settings

    async def invoke(self, *, name: str, url: str, goal: str, run_id: str | None = None) -> str:
        del url, goal, run_id
        return SUCCESS_OUTPUTS[name]


@pytest.mark.asyncio
async def test_plan_and_invoke_keep_existing_goal_plan_and_a2a_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def build_fake_agent(_settings: Settings) -> FakeRunnable:
        return FakeRunnable()

    monkeypatch.setattr(orchestrator_server, "build_agent", build_fake_agent)
    settings = Settings()
    app = orchestrator_server.create_app(settings=settings, specialist_client=ContractInvoker(settings=settings))
    goal = {"goal": "Recover stock.", "sku": "ZS-1042", "store_id": "store-101"}

    assert set(Goal.model_fields) == {"goal", "sku", "store_id"}
    assert set(Plan.model_fields) == {
        "sku",
        "store_id",
        "stock_view",
        "price_view",
        "po_view",
        "shipping_view",
        "summary",
        "next_actions",
    }
    assert set(InvokeRequest.model_fields) == {"run_id", "goal", "context"}
    assert set(InvokeResponse.model_fields) == {"run_id", "output", "tool_calls"}

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            plan_response = await client.post("/plan", json=goal)
            invoke_response = await client.post("/invoke", json={"run_id": "test-run", "goal": "Recover stock."})
            invalid_goal = await client.post("/plan", json={**goal, "unexpected": "forbidden"})

    assert plan_response.status_code == HTTPStatus.OK
    assert set(plan_response.json()) == set(Plan.model_fields)
    assert plan_response.json()["sku"] == "ZS-1042"
    assert "verified stock risk" in plan_response.json()["stock_view"]
    assert invoke_response.status_code == HTTPStatus.OK
    assert set(invoke_response.json()) == set(InvokeResponse.model_fields)
    assert invoke_response.json()["run_id"] == "test-run"
    assert invalid_goal.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
