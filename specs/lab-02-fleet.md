# ZavaShop Local Stock-Out Response Fleet

## Goal

Build the ZavaShop stock-out response fleet for Low-Cost Edition Track A on local k3d, with five MAF agents and four MCP servers and no Azure resources. Given a stock-out goal containing a SKU and store, have the orchestrator return a validated `Plan` with `stock_view`, `price_view`, `po_view`, `shipping_view`, and `summary`.

## Non-goals

- Do not create, deploy to, or require Azure resources, AKS, or Azure Container Apps.
- Do not implement real supplier, warehouse, shipping, or pricing-system integrations; local MCP fixtures are sufficient for this iteration.
- Do not execute purchase orders, transfers, or price changes; the fleet returns recommendations and drafts only.
- Do not add a model provider other than the GitHub Copilot SDK with `model="gpt-5.5"`.
- Do not add application code as part of this requirements handoff.

## Personas

- **Store manager:** needs an actionable response to a SKU stock-out at a specific store.
- **Supply planner:** needs stock risk, replenishment, and transfer options in one plan.
- **Supplier operations specialist:** needs a purchase-order draft recommendation without an order being submitted.
- **Logistics coordinator:** needs a local transfer or shipment quote to compare recovery options.
- **Pricing analyst:** needs a price recommendation that accounts for the stock situation.
- **Local platform developer:** needs to run and validate the fleet on k3d without provisioning Azure.

## Affected agents

All specialist agents communicate with the orchestrator over A2A. External-system-shaped operations are exposed by MCP servers, not implemented as direct I/O in agent code. For Track A, the MCP servers return deterministic local fixture data.

- [ ] `orchestrator` — accepts a stock-out request, invokes the four specialist agents over A2A, and assembles the `Plan` response.
- [ ] `inventory` — calls the inventory MCP server and returns `StockView`.
- [ ] `supplier` — calls the supplier MCP server and returns a draft `PurchaseOrderView`; it must not submit an order.
- [ ] `logistics` — calls the shipping MCP server and returns `ShippingView`.
- [ ] `pricing` — calls the pricing MCP server and returns `PriceView`.
- [ ] MCP server `inventory` — exposes `check_stock(StockQuery) -> StockReport`.
- [ ] MCP server `supplier` — exposes `draft_po(PurchaseOrderRequest) -> PurchaseOrderDraft`.
- [ ] MCP server `shipping` — exposes `quote_shipment(ShipmentQuoteRequest) -> ShipmentQuote`.
- [ ] MCP server `pricing` — exposes `recommend_price(PricingQuery) -> PriceRecommendation`.

## New / changed contracts

The following immutable Pydantic v2 models are the public request, A2A view, and response contracts. Every model crossing a process boundary must use the corresponding typed contract.

```python
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PlanRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    goal: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    store_id: str = Field(min_length=1)


class StockView(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str
    store_id: str
    on_hand: int = Field(ge=0)
    reserved: int = Field(ge=0)
    reorder_point: int = Field(ge=0)
    days_until_stockout: int | None = Field(default=None, ge=0)
    transfer_source_location_id: str | None = None
    transfer_available_qty: int = Field(default=0, ge=0)
    risk_level: Literal["low", "medium", "high", "critical"]
    recommendation: str = Field(min_length=1)


class PriceView(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str
    store_id: str
    current_price: float = Field(gt=0)
    recommended_price: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    rationale: str = Field(min_length=1)


class PurchaseOrderView(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str
    store_id: str
    supplier_id: str
    quantity: int = Field(ge=1)
    unit_cost: float = Field(gt=0)
    estimated_delivery_days: int = Field(ge=0)
    status: Literal["draft"]
    rationale: str = Field(min_length=1)


class ShippingView(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str
    source_location_id: str
    destination_store_id: str
    quantity: int = Field(ge=1)
    carrier: str | None = None
    estimated_delivery_days: int | None = Field(default=None, ge=0)
    status: Literal["quoted", "unavailable"]
    rationale: str = Field(min_length=1)


class Plan(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str
    store_id: str
    stock_view: StockView
    price_view: PriceView
    po_view: PurchaseOrderView
    shipping_view: ShippingView
    summary: str = Field(min_length=1)
```

```python
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StockQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str = Field(min_length=1)
    store_id: str = Field(min_length=1)


class StockReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str
    store_id: str
    on_hand: int = Field(ge=0)
    reserved: int = Field(ge=0)
    reorder_point: int = Field(ge=0)
    days_until_stockout: int | None = Field(default=None, ge=0)
    transfer_source_location_id: str | None = None
    transfer_available_qty: int = Field(default=0, ge=0)
    risk_level: Literal["low", "medium", "high", "critical"]
    recommendation: str = Field(min_length=1)


class PurchaseOrderRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str = Field(min_length=1)
    store_id: str = Field(min_length=1)
    quantity: int = Field(ge=1)


class PurchaseOrderDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str
    store_id: str
    supplier_id: str
    quantity: int = Field(ge=1)
    unit_cost: float = Field(gt=0)
    estimated_delivery_days: int = Field(ge=0)
    status: Literal["draft"]
    rationale: str = Field(min_length=1)


class ShipmentQuoteRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str = Field(min_length=1)
    source_location_id: str = Field(min_length=1)
    destination_store_id: str = Field(min_length=1)
    quantity: int = Field(ge=1)


class ShipmentQuote(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str
    source_location_id: str
    destination_store_id: str
    quantity: int = Field(ge=1)
    carrier: str | None = None
    estimated_delivery_days: int | None = Field(default=None, ge=0)
    status: Literal["quoted", "unavailable"]
    rationale: str = Field(min_length=1)


class PricingQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str = Field(min_length=1)
    store_id: str = Field(min_length=1)
    on_hand: int = Field(ge=0)
    replenishment_qty: int = Field(ge=0)


class PriceRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str
    store_id: str
    current_price: float = Field(gt=0)
    recommended_price: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    rationale: str = Field(min_length=1)
```

## Acceptance criteria

1. A local k3d deployment can start all five agent services and all four MCP services and serve the orchestrator without provisioning or contacting Azure resources.
2. Each of the five agents is built through `GitHubCopilotAgent` with `client=build_copilot_client()` and `GitHubCopilotOptions(model="gpt-5.5", timeout=settings.copilot_timeout_seconds, on_permission_request=_approve_all, mcp_servers={...})`; no agent uses `GitHubCopilotChatClient`, `ChatAgent`, or `MCPStreamableHTTPTool`.
3. Each agent service provides `GET /healthz`, `GET /readyz`, and `POST /invoke`; health and readiness reflect client initialization and MCP connectivity respectively.
4. Each of the four MCP servers exposes its listed typed tool and accepts/returns the corresponding Pydantic contract; the local fixture for `ZS-1042` at `store-101` returns valid stock, supplier, shipment, and pricing data.
5. A valid stock-out `PlanRequest` is handled by the orchestrator, which calls inventory, supplier, logistics, and pricing specialists over A2A and returns a `Plan` whose `sku` and `store_id` match the request.
6. Every successful `Plan` contains all five required fields (`stock_view`, `price_view`, `po_view`, `shipping_view`, `summary`); the nested views validate against their declared types and include the requested SKU/store identifiers where applicable.
7. A supplier recommendation remains a draft and a shipment result remains a quote; no MCP tool submits a purchase order, initiates a transfer, or changes a price.
8. An unavailable or failed required specialist/MCP result is surfaced as an explicit request failure; the orchestrator does not silently omit a view or return a success-shaped partial `Plan`.
9. Unit and integration tests verify the contracts and A2A aggregation with model calls mocked using `MockChatClient`; `uv run poe check` passes.
10. The local run does not depend on Azure services, and no credential value is committed to the repository.
11. The evaluation scenario below passes with a response that mentions the SKU, store, all four view keys, and summary within its stated latency budget.

## Eval scenarios

```jsonl
{"id":"lab-02-stockout-zs1042-store101","goal":"SKU ZS-1042 is out at store-101. Build a recovery plan with stock, pricing, purchase-order, and shipping recommendations.","sku":"ZS-1042","store_id":"store-101","must_mention":["ZS-1042","store-101","stock_view","price_view","po_view","shipping_view","summary"],"must_call":["inventory","supplier","logistics","pricing"],"forbid_call":[],"max_latency_s":180}
```

## Out of scope for this iteration

- Azure subscriptions, identities, Key Vault, AKS, Azure Container Apps, Azure Container Registry, Bicep, and Helm-based Azure deployment.
- Production databases, live supplier APIs, real shipping carriers, payment systems, or external pricing engines.
- Automatic execution of the recommended replenishment, shipment, or pricing actions.
- Multi-SKU/multi-store planning, authentication/authorization beyond what is needed to run locally, and production-scale observability.

## Handoff

Next custom agent: `mcp-builder` — implement the four local MCP servers and their typed tools from this spec, using deterministic fixture data for `ZS-1042` at `store-101`. Keep external service integrations out of scope and do not create or require Azure resources. After the MCP contracts are in place, hand off to `agent-builder` for the four specialist agents and then `orchestrator-architect` for A2A aggregation and the final `Plan` endpoint.

Prompt to give it: `Implement the four local MCP servers from specs/lab-02-fleet.md for Low-Cost Edition Track A (local k3d). Use deterministic fixtures for ZS-1042 at store-101, typed frozen Pydantic v2 request/response models, and tests. Do not create or require Azure resources. Run uv run poe check and report.`