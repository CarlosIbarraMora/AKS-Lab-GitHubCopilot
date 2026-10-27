"""Deterministic MAF workflow for the orchestrator's /plan endpoint."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Literal, Never, cast

import httpx
import structlog
from agent_framework import Workflow, WorkflowBuilder, WorkflowContext, executor
from pydantic import BaseModel, ConfigDict, ValidationError

from src.shared.supplier_contracts import SupplierAgentOutput

from .a2a import LAB02_DEMAND_SIGNAL, LAB02_REPLENISHMENT_QUANTITY, SpecialistInvoker
from .contracts import Goal, Plan

SPECIALIST_TIMEOUT_SECONDS = 120.0
_PLANNING_STOCK_RISK_ASSUMPTION = "high"
_STOCK_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})
_SPECIALIST_JSON_KEYS: dict[str, frozenset[str]] = {
    "inventory": frozenset({"sku", "store_id", "stock_view", "risk_level", "nearby_locations", "recommendation"}),
    "pricing": frozenset(
        {"sku", "store_id", "price_view", "current_price", "recommended_price", "margin_impact", "rationale"}
    ),
    "supplier": frozenset({"sku", "store_id", "supplier_view", "po_view", "recommended_supplier", "next_action"}),
    "logistics": frozenset({"po_id", "sku", "source_location_id", "destination_store_id", "quantity", "shipping_view"}),
}
log = structlog.get_logger(__name__).bind(agent_name="orchestrator")


class SpecialistOutcome(BaseModel):
    """Internal result of one A2A workflow step."""

    model_config = ConfigDict(frozen=True)

    specialist: Literal["inventory", "pricing", "supplier", "logistics"]
    status: Literal["succeeded", "failed", "timed_out", "skipped"]
    output: str | None = None
    reason: str | None = None


class _PlanState(BaseModel):
    """Internal state carried between dependent workflow executors."""

    model_config = ConfigDict(frozen=True)

    goal: Goal
    outcomes: tuple[SpecialistOutcome, ...] = ()


def _get_outcome(state: _PlanState, specialist: str) -> SpecialistOutcome | None:
    return next((item for item in state.outcomes if item.specialist == specialist), None)


def _append_outcome(state: _PlanState, outcome: SpecialistOutcome) -> _PlanState:
    retained = tuple(item for item in state.outcomes if item.specialist != outcome.specialist)
    return state.model_copy(update={"outcomes": (*retained, outcome)})


def _is_refusal_or_error(output: str) -> bool:
    text = output.strip().lower()
    markers = (
        "i cannot help",
        "i can't help",
        "i am unable",
        "i'm unable",
        "unable to fulfill",
        "request refused",
        "data unavailable",
        "no data available",
    )
    return any(marker in text for marker in markers)


def _decode_json_object(output: str) -> dict[str, object] | None:
    """Decode a JSON object with a strict, statically typed result."""

    try:
        decoded: object = json.loads(output)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict):
        return None
    return cast(dict[str, object], decoded)


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _non_negative_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _valid_stock_risk(value: object) -> bool:
    return isinstance(value, str) and value in _STOCK_RISK_LEVELS


def _valid_inventory_stock_view(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if not isinstance(value, dict):
        return False
    fields = cast(dict[str, object], value)
    required = ("on_hand", "reserved", "reorder_point")
    days_until_stockout = fields.get("days_until_stockout")
    return all(_non_negative_integer(fields.get(key)) for key in required) and (
        days_until_stockout is None or _non_negative_integer(days_until_stockout)
    )


def _valid_inventory_location(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    location = cast(dict[str, object], value)
    source_id = location.get("transfer_source_location_id")
    store_id = location.get("store_id")
    transfer_qty = location.get("transfer_available_qty")
    if source_id is not None and not _non_empty_string(source_id):
        return False
    if store_id is not None and not _non_empty_string(store_id):
        return False
    if transfer_qty is not None and not _non_negative_integer(transfer_qty):
        return False
    return (source_id is not None) or (store_id is not None and transfer_qty is not None)


def _valid_inventory_payload(data: dict[str, object]) -> bool:
    return (
        _SPECIALIST_JSON_KEYS["inventory"].issubset(data)
        and _non_empty_string(data.get("sku"))
        and _non_empty_string(data.get("store_id"))
        and _valid_stock_risk(data.get("risk_level"))
        and _non_empty_string(data.get("recommendation"))
        and _valid_inventory_stock_view(data.get("stock_view"))
        and isinstance(data.get("nearby_locations"), list)
        and all(_valid_inventory_location(item) for item in cast(list[object], data["nearby_locations"]))
    )


def _valid_supplier_payload(data: dict[str, object]) -> bool:
    try:
        SupplierAgentOutput.model_validate(data)
    except ValidationError:
        return False
    return True


def _validate_specialist_text(
    output: object, specialist: Literal["inventory", "pricing", "supplier", "logistics"]
) -> tuple[bool, str | None]:
    reason: str | None = None
    if not isinstance(output, str) or not output.strip():
        reason = "empty_or_invalid_output"
    elif _is_refusal_or_error(output):
        reason = "specialist_refused_or_reported_unavailable"
    elif output.lstrip().startswith(("{", "[")):
        decoded = _decode_json_object(output)
        if decoded is None:
            reason = "malformed_specialist_output"
        elif decoded.get("error") or decoded.get("refusal"):
            reason = "specialist_refused_or_reported_unavailable"
        elif specialist == "inventory":
            if not _valid_inventory_payload(decoded):
                reason = "malformed_specialist_output"
        elif specialist == "supplier":
            if not _valid_supplier_payload(decoded):
                reason = "malformed_specialist_output"
        elif not _SPECIALIST_JSON_KEYS[specialist].issubset(decoded):
            reason = "malformed_specialist_output"
        else:
            required_views = {"pricing": ("price_view",), "logistics": ("shipping_view",)}[specialist]
            if any(not _non_empty_string(decoded.get(key)) for key in required_views):
                reason = "malformed_specialist_output"
    return reason is None, reason


def _has_required_stock_view(output: str) -> bool:
    """Check actual inventory quantities when stock findings use structured JSON."""

    if not output.lstrip().startswith("{"):
        return True
    data = _decode_json_object(output)
    return data is not None and _valid_inventory_payload(data)


def _is_transfer_source_location(location: object) -> bool:
    if not isinstance(location, dict):
        return False
    fields = cast(dict[str, object], location)
    transfer_qty = fields.get("transfer_available_qty")
    if _non_empty_string(fields.get("transfer_source_location_id")):
        return transfer_qty is None or _positive_integer(transfer_qty)
    return _non_empty_string(fields.get("store_id")) and _positive_integer(transfer_qty)


def _has_transfer_source(output: str) -> bool:
    """Find only a source location explicitly identified by inventory findings."""

    if not output.lstrip().startswith("{"):
        lowered = output.lower()
        return not any(marker in lowered for marker in ("no transfer source", "source unavailable", "no source"))
    data = _decode_json_object(output)
    if data is None:
        return False

    transfer_qty = data.get("transfer_available_qty")
    direct_source = _non_empty_string(data.get("transfer_source_location_id"))
    if direct_source:
        return transfer_qty is None or _positive_integer(transfer_qty)

    locations = data.get("nearby_locations")
    return isinstance(locations, list) and any(
        _is_transfer_source_location(item) for item in cast(list[object], locations)
    )


def _pricing_stock_risk(goal: Goal) -> tuple[str, str]:
    """Return a user-stated risk or an explicitly labeled stock-out planning assumption."""

    text = goal.goal.lower()
    for level in ("critical", "high", "medium", "low"):
        if re.search(rf"\b{level}\b", text):
            return level, "stated in the goal"
    return _PLANNING_STOCK_RISK_ASSUMPTION, "provisional stock-out planning assumption; not inventory-confirmed"


async def _invoke(
    *,
    client: SpecialistInvoker,
    specialist: Literal["inventory", "pricing", "supplier", "logistics"],
    url: str,
    goal: str,
) -> SpecialistOutcome:
    try:
        async with asyncio.timeout(SPECIALIST_TIMEOUT_SECONDS):
            output = await client.invoke(name=specialist, url=url, goal=goal)
    except (TimeoutError, httpx.TimeoutException):
        log.warning("orchestrator.specialist.timed_out", specialist=specialist)
        return SpecialistOutcome(specialist=specialist, status="timed_out", reason="specialist_timeout")
    except (httpx.HTTPError, ValidationError):
        log.warning("orchestrator.specialist.failed", specialist=specialist, reason="a2a_response_error")
        return SpecialistOutcome(specialist=specialist, status="failed", reason="a2a_response_error")
    except Exception as exc:
        log.warning(
            "orchestrator.specialist.failed",
            specialist=specialist,
            reason="invocation_error",
            error_type=type(exc).__name__,
        )
        return SpecialistOutcome(specialist=specialist, status="failed", reason="invocation_error")

    valid, reason = _validate_specialist_text(output, specialist)
    if not valid:
        log.warning("orchestrator.specialist.invalid_output", specialist=specialist, reason=reason)
        return SpecialistOutcome(
            specialist=specialist,
            status="failed",
            output=output,
            reason=reason,
        )
    log.info("orchestrator.specialist.succeeded", specialist=specialist)
    return SpecialistOutcome(specialist=specialist, status="succeeded", output=output)


def _skipped(specialist: Literal["supplier", "logistics"], reason: str) -> SpecialistOutcome:
    return SpecialistOutcome(specialist=specialist, status="skipped", reason=reason)


def _specialist_view(outcome: SpecialistOutcome | None, specialist: str) -> str:
    if outcome is None:
        return f"{specialist} status: failed; reason: workflow_outcome_missing."
    if outcome.status == "succeeded" and outcome.output is not None:
        return outcome.output
    actual_output = f"; actual specialist response: {outcome.output}" if outcome.output is not None else ""
    return f"{specialist} status: {outcome.status}; reason: {outcome.reason or 'no_details'}{actual_output}"


def _next_actions(outcomes: tuple[SpecialistOutcome, ...]) -> list[str]:
    by_name = {item.specialist: item for item in outcomes}
    actions: list[str] = []
    po = by_name.get("supplier")
    shipping = by_name.get("logistics")
    pricing = by_name.get("pricing")
    if po is not None and po.status == "succeeded":
        actions.append("Review the supplier draft; no purchase order has been submitted.")
    else:
        actions.append("Resolve the inventory or supplier result before preparing a purchase order.")
    if shipping is not None and shipping.status == "succeeded":
        actions.append("Review the logistics quote; no shipment has been booked.")
    else:
        actions.append("Provide or confirm a transfer source before requesting a shipping quote.")
    if pricing is not None and pricing.status == "succeeded":
        actions.append("Review the pricing recommendation; no price change has been applied.")
    else:
        actions.append("Request a pricing recommendation after resolving the reported specialist issue.")
    return actions


def build_plan_workflow(client: SpecialistInvoker) -> Workflow:
    """Create the deterministic stock/pricing fan-out and dependent PO/shipping workflow."""

    @executor(id="plan_start")
    async def start(goal: Goal, context: WorkflowContext[Goal]) -> None:
        await context.send_message(goal)

    @executor(id="inventory_step")
    async def inventory(goal: Goal, context: WorkflowContext[_PlanState]) -> None:
        request = (
            f"{goal.goal}\nValidate stock findings for SKU {goal.sku} at store {goal.store_id}. "
            "Report only actual stock and transfer data; do not infer missing values."
        )
        outcome = await _invoke(
            client=client,
            specialist="inventory",
            url=client.settings.inventory_a2a_url,
            goal=request,
        )
        await context.send_message(_PlanState(goal=goal, outcomes=(outcome,)))

    @executor(id="pricing_step")
    async def pricing(goal: Goal, context: WorkflowContext[_PlanState]) -> None:
        stock_risk, stock_risk_source = _pricing_stock_risk(goal)
        request = (
            f"Recommend pricing for SKU {goal.sku} at store {goal.store_id}. User goal: {goal.goal}\n"
            f"Use demand_signal={LAB02_DEMAND_SIGNAL}; this is the existing Lab 02 high-demand scenario assumption, "
            "not a measured demand finding. "
            f"Use stock_risk_level={stock_risk}; source: {stock_risk_source}. "
            "Inventory is running concurrently, so this risk is not yet inventory-confirmed. Do not claim these "
            "assumptions are specialist findings. Return the pricing MCP result and keep it advisory; do not apply "
            "a price change."
        )
        outcome = await _invoke(
            client=client,
            specialist="pricing",
            url=client.settings.pricing_a2a_url,
            goal=request,
        )
        await context.send_message(_PlanState(goal=goal, outcomes=(outcome,)))

    @executor(id="purchase_order_step")
    async def purchase_order(branches: list[_PlanState], context: WorkflowContext[_PlanState]) -> None:
        goal = branches[0].goal
        outcomes = tuple(item for branch in branches for item in branch.outcomes)
        state = _PlanState(goal=goal, outcomes=outcomes)
        stock = _get_outcome(state, "inventory")
        if stock is None or stock.status != "succeeded" or stock.output is None:
            result = _skipped("supplier", "required_inventory_findings_unavailable")
        elif not _has_required_stock_view(stock.output):
            result = _skipped("supplier", "required_stock_view_unavailable_or_malformed")
        else:
            request = (
                f"Draft a purchase order for SKU {goal.sku} at store {goal.store_id}. "
                f"Use only these actual inventory specialist findings: {stock.output}\n"
                f"The deterministic planned quantity is {LAB02_REPLENISHMENT_QUANTITY} units; this is a planning "
                "rule, not supplier capacity. Do not treat available supplier capacity as the order quantity. "
                "Return an advisory draft only; do not submit an order."
            )
            result = await _invoke(
                client=client,
                specialist="supplier",
                url=client.settings.supplier_a2a_url,
                goal=request,
            )
        await context.send_message(_append_outcome(state, result))

    @executor(id="shipping_step")
    async def shipping(state: _PlanState, context: WorkflowContext[_PlanState]) -> None:
        stock = _get_outcome(state, "inventory")
        po = _get_outcome(state, "supplier")
        if stock is None or stock.status != "succeeded" or stock.output is None:
            result = _skipped("logistics", "required_inventory_findings_unavailable")
        elif po is None or po.status != "succeeded" or po.output is None:
            result = _skipped("logistics", "purchase_order_planning_did_not_succeed")
        elif not _has_transfer_source(stock.output):
            result = _skipped("logistics", "transfer_source_unavailable_in_inventory_findings")
        else:
            request = (
                f"Quote a shipment for SKU {state.goal.sku} to store {state.goal.store_id} using only these "
                f"actual inventory findings: {stock.output}\nSupplier draft: {po.output}\n"
                f"The deterministic planning quantity is {LAB02_REPLENISHMENT_QUANTITY} units; supplier capacity "
                "is not the order quantity. Use a source only if the inventory findings explicitly provide it; "
                "otherwise report unavailable. Quote only; do not book a shipment."
            )
            result = await _invoke(
                client=client,
                specialist="logistics",
                url=client.settings.logistics_a2a_url,
                goal=request,
            )
        await context.send_message(_append_outcome(state, result))

    @executor(id="reconcile_plan")
    async def reconcile(state: _PlanState, context: WorkflowContext[Never, Plan]) -> None:
        by_name = {item.specialist: item for item in state.outcomes}
        price_outcome = by_name.get("pricing")
        price_view = _specialist_view(price_outcome, "pricing")
        if price_outcome is not None and price_outcome.status == "succeeded":
            risk, risk_source = _pricing_stock_risk(state.goal)
            price_view = (
                f"Planning context: demand_signal={LAB02_DEMAND_SIGNAL} is the Lab 02 assumption; "
                f"stock_risk_level={risk} ({risk_source}). Specialist response: {price_view}"
            )
        plan = Plan(
            sku=state.goal.sku,
            store_id=state.goal.store_id,
            stock_view=_specialist_view(by_name.get("inventory"), "inventory"),
            price_view=price_view,
            po_view=_specialist_view(by_name.get("supplier"), "supplier"),
            shipping_view=_specialist_view(by_name.get("logistics"), "logistics"),
            summary=(
                f"Recovery findings for SKU {state.goal.sku} at {state.goal.store_id} are assembled from the "
                "specialist results above. All outputs are advisory; no purchase order, shipment, or price change "
                "was executed."
            ),
            next_actions=_next_actions(state.outcomes),
        )
        await context.yield_output(plan)

    return (
        WorkflowBuilder(start_executor=start, output_from=[reconcile])
        .add_fan_out_edges(start, [inventory, pricing])
        .add_fan_in_edges([inventory, pricing], purchase_order)
        .add_edge(purchase_order, shipping)
        .add_edge(shipping, reconcile)
        .build()
    )


async def run_plan_workflow(workflow: Workflow, goal: Goal) -> Plan:
    """Execute a plan workflow and return its deterministic Plan output."""

    result = await workflow.run(goal)
    outputs = result.get_outputs()
    plan = next((item for item in outputs if isinstance(item, Plan)), None)
    if plan is None:
        raise RuntimeError("plan workflow completed without a Plan output")
    return plan
