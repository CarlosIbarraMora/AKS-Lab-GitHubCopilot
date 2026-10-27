# Add a deterministic `/plan` workflow to the orchestrator

## Goal
Implement a deterministic, non-LLM Microsoft Agent Framework `Workflow` behind the existing `POST /plan` endpoint, using the existing A2A HTTP clients to invoke specialists and preserving the existing `Goal` and `Plan` contracts. Hydrate the Copilot token and four MCP endpoint URLs from Azure Key Vault only when `ZAVA_KV_URL` is configured, and support running the complete nine-service fleet locally with Docker Compose without Azure resources or Key Vault.

### Functional requirements

- Accept the existing `Goal` input (`goal`, `sku`, `store_id`) and return the existing `Plan` output fields unchanged.
- Run the inventory/stock and pricing specialist steps in parallel; after both outcomes are available, plan a purchase order, then request shipping, then deterministically reconcile specialist outputs into `Plan`.
- Invoke specialists through the existing A2A HTTP client and preserve their actual output. The orchestrator workflow itself must not call a model, fabricate specialist data, submit a purchase order, book a shipment, or apply a price change.
- Represent failures, timeouts, and skipped dependent steps explicitly in the affected `Plan` text fields and `next_actions`. A failed or timed-out result must never be replaced with plausible-looking business data.

### Nonfunctional requirements

- Use async workflow steps and bounded A2A and specialist timeouts; retain the existing longer A2A read timeout appropriate for specialist invocations.
- Emit structured, correlation-friendly logs without logging `GITHUB_TOKEN`, secret values, authorization headers, or other credentials.
- Keep startup configuration deterministic: preserve environment variables already set, close Azure credentials reliably, and avoid Azure authentication/network access when Key Vault is not configured.

## Non-goals

- Do not change or rename the existing `Goal`, `Plan`, `/plan`, `/invoke`, or shared A2A response contracts.
- Do not replace the specialist agents, MCP servers, or A2A transport, or add model-based reasoning to the orchestrator's `/plan` path.
- Do not submit or persist purchase orders, book shipments, or make price changes; specialist actions remain advisory drafts, quotes, and recommendations.
- Do not create Azure resources, require Azure resources for Local Track A, or require Key Vault when `ZAVA_KV_URL` is unset.
- Do not change the existing `/invoke` interaction or make unrelated agent behavior changes.

## Personas

- Store manager: receives a concise, actionable stock-out plan with actual specialist findings and clear unavailable information.
- Supply planner: reviews inventory risk, supplier draft, shipping quote, pricing recommendation, and follow-up actions.
- Local developer: runs and tests the complete ZavaShop fleet without provisioning cloud resources or configuring Key Vault.
- Service operator: configures Key Vault hydration in an Azure-enabled environment without placing secrets in images or logs.

## Affected agents

- [x] `orchestrator` — implement the Workflow, deterministic reconciliation, `/plan` wiring, startup hydration, and local Compose changes.
- [x] `inventory` — preserve its A2A contract and hydrate its MCP URL/token at startup when Key Vault is enabled.
- [x] `supplier` — preserve its A2A contract and hydrate its MCP URL/token at startup when Key Vault is enabled.
- [x] `logistics` — preserve its A2A contract and hydrate its MCP URL/token at startup when Key Vault is enabled.
- [x] `pricing` — preserve its A2A contract and hydrate its MCP URL/token at startup when Key Vault is enabled.
- [x] MCP servers touched: `inventory-mcp`, `supplier-mcp`, `shipping-mcp`, and `pricing-mcp` — hydrate applicable configuration at startup when Key Vault is enabled.

All specialist calls remain service-to-service A2A HTTP calls; no specialist imports another agent's Python modules. Key Vault integration is shared startup configuration, not a change to MCP tool behavior.

## New / changed contracts

The following contracts already exist and are normative. They MUST remain unchanged:

```python
from pydantic import BaseModel, ConfigDict, Field


class Goal(BaseModel):
    """Structured stock-out goal accepted by the /plan endpoint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    goal: str = Field(min_length=1, description="Natural-language stock-out recovery goal.")
    sku: str = Field(min_length=1, description="SKU to recover.")
    store_id: str = Field(min_length=1, description="Target store identifier.")


class Plan(BaseModel):
    """Stock-out response plan assembled by the orchestrator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str
    store_id: str
    stock_view: str
    price_view: str
    po_view: str
    shipping_view: str
    summary: str
    next_actions: list[str]
```

A recommended internal, non-HTTP workflow outcome contract for retaining execution status without inventing results is:

```python
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SpecialistOutcome(BaseModel):
    """Internal result of one A2A workflow step."""

    model_config = ConfigDict(frozen=True)

    specialist: Literal["inventory", "pricing", "supplier", "logistics"]
    status: Literal["succeeded", "failed", "timed_out", "skipped"]
    output: str | None = None
    reason: str | None = None
```

`SpecialistOutcome` is internal to orchestration; it does not change `Plan` or any process-boundary API. The implementation may use an equivalent strongly typed internal representation. `output` contains only the specialist's actual A2A response. For unsuccessful outcomes, `reason` is a safe, concise category or message that contains no credential or secret material. Key Vault secret names map to environment variables as follows: `GITHUB-TOKEN` → `GITHUB_TOKEN`; `INVENTORY-MCP-URL` → `ZAVA_INVENTORY_MCP_URL`; `SUPPLIER-MCP-URL` → `ZAVA_SUPPLIER_MCP_URL`; `SHIPPING-MCP-URL` → `ZAVA_SHIPPING_MCP_URL`; `PRICING-MCP-URL` → `ZAVA_PRICING_MCP_URL`.

### Workflow topology

```text
                         +----------------------+
                         | POST /plan: Goal      |
                         | goal, sku, store_id   |
                         +----------+-----------+
                                    |
                    +---------------+----------------+
                    |                                |
                    v                                v
          +-------------------+            +-------------------+
          | Inventory A2A     |            | Pricing A2A       |
          | stock findings    |            | price finding     |
          +---------+---------+            +---------+---------+
                    |                                |
                    +---------------+----------------+
                                    |
                                    v
                          +-------------------+
                          | Purchase-order    |
                          | planning via A2A  |
                          +---------+---------+
                                    |
                                    v
                          +-------------------+
                          | Shipping quote    |
                          | via A2A           |
                          +---------+---------+
                                    |
                                    v
                          +-------------------+
                          | Deterministic     |
                          | reconcile to Plan|
                          +---------+---------+
                                    |
                                    v
                           +------------------+
                           | POST /plan result|
                           | existing Plan   |
                           +------------------+
```

The workflow MUST make stock and price eligible to execute concurrently, wait for their outcomes before purchase-order planning, and wait for the purchase-order outcome before shipping. When a prerequisite needed for a downstream action is unavailable, mark that step `skipped`, explain why in the `Plan`, and continue reconciliation. Pricing is independent of the supplier and shipping steps. Reconciliation is ordinary deterministic code over actual specialist outputs and explicit outcome statuses; it is not an LLM call.

## Acceptance criteria

1. `POST /plan` validates the existing `Goal` fields and returns a `Plan` with exactly the existing fields; the `Goal` and `Plan` definitions, `/invoke` contract, and `/plan` route response shape do not change.
2. The `/plan` implementation runs as a Microsoft Agent Framework `Workflow` and contains no orchestrator model invocation. A test demonstrates that stock and pricing steps can overlap, both complete before purchase-order planning begins, shipping follows purchase-order planning, and reconciliation follows the workflow steps.
3. All specialist calls are made through existing A2A HTTP clients. The workflow propagates the validated `goal`, `sku`, `store_id`, and only actual preceding specialist output or an explicit deterministic planning rule; supplier capacity must not be presented as an order quantity.
4. A2A connection/read timeouts, specialist errors, refusals, and malformed or empty outputs are represented as unsuccessful outcomes. Tests show an error or timeout does not abort unrelated eligible steps, fabricate a specialist result, or cause a false success value to appear in the `Plan`.
5. If stock information required for a purchase-order or shipping step is unavailable, dependent steps are marked skipped with an explicit reason. A partial `Plan` still conforms to the unchanged contract, identifies unavailable/failed/skipped results in the corresponding string fields, and includes useful `next_actions`; it does not imply a purchase, shipment, or price change happened.
6. Add `src/shared/keyvault.py` with async hydration using `azure.keyvault.secrets.aio.SecretClient` and `DefaultAzureCredential`. It maps the five documented secret names to their environment variables, does not overwrite an environment variable that is already set, closes credential/client resources reliably (credential closure in `finally`), and does not log secrets.
7. When `ZAVA_KV_URL` is unset, hydration performs no Azure credential or network operation and local startup does not require Azure resources, Azure login, or Key Vault. When it is configured, the helper is invoked by all five agent servers and all four MCP servers; Key Vault authentication or retrieval errors are surfaced explicitly rather than silently treated as successful hydration.
8. `docker-compose.yml` runs all nine runtime services (five agents and four MCP servers), uses service health checks against `/healthz`, passes `GITHUB_TOKEN` from the host with a fail-fast `${GITHUB_TOKEN:?...}` expression, uses `ZAVA_COPILOT_TIMEOUT_SECONDS=120`, and does not require Key Vault. The local base image follows the Lab 03 README requirements: non-root UID 10001, writable `/home/zava` and cache, `HOME=/home/zava`, and `XDG_CACHE_HOME=/home/zava/.cache`.
9. Compose validation confirms the nine runtime services and their health/dependency configuration. A local integration check can build and start the fleet without Azure resources; it may require a user-provided host `GITHUB_TOKEN` for Copilot-backed specialist services. No token is committed, printed, or logged.
10. Run `uv run poe check` (or the repository virtual-environment `poe.exe check` if `uv` is unavailable). Add focused tests for workflow order/concurrency, A2A success/failure/timeout handling, unchanged `/plan` and `/invoke` contracts, Key Vault secret mapping/no-overwrite/no-KV path/cleanup, and Compose service and healthcheck configuration. Tests must mock Azure and A2A boundaries; do not make real Azure calls.

## Eval scenarios

```jsonl
{"id":"lab03-basic-stockout-plan","goal":"Store 101 will stock out of SKU ZS-1042 by Friday. Build a recovery plan for store-101.","sku":"ZS-1042","store_id":"store-101","must_mention":["ZS-1042","store-101","stock","purchase","shipping","price"],"must_call":["inventory","pricing","supplier","logistics"],"forbid_call":[],"max_latency_s":180}
{"id":"lab03-critical-risk-next-actions","goal":"SKU ZS-1042 at store-101 has critical stock-out risk. Return the specialist-backed plan and identify follow-up actions.","sku":"ZS-1042","store_id":"store-101","must_mention":["ZS-1042","store-101","critical","next_actions"],"must_call":["inventory","pricing","supplier","logistics"],"forbid_call":[],"max_latency_s":180}
```

`must_call` and `forbid_call` document expected specialist participation for review; the current eval runner records these fields but does not use them as a pass gate. Failure and timeout behavior is verified through mocked workflow tests, not by requiring an external specialist outage during eval runs.

## Out of scope for this iteration

- Creating or deploying Azure Key Vault, identities, permissions, AKS, or Azure Container Apps resources.
- Changing specialist MCP tools, specialist prompts, inventory/supplier/shipping/pricing business data, or specialist response contracts.
- Running live Azure calls or committing credentials, `.env` files, or secret values.
- Changing `/invoke` model, permission policy, streaming telemetry, or response contract.

## Handoff

Next agent: `orchestrator-architect`.

Prompt: `Implement specs/lab-03-plan-workflow.md. Preserve Goal and Plan contracts and the existing /invoke behavior. Build the deterministic Microsoft Agent Framework Workflow, async Key Vault hydration, and nine-service local Docker Compose setup; do not create Azure resources. Add no persistent tests—hand test ownership to test-author after source implementation.`
