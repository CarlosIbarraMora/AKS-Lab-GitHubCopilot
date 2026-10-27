"""Function tools for orchestrator A2A specialist delegation."""

from __future__ import annotations

from agent_framework import FunctionTool, tool

from src.shared.settings import Settings

from .a2a import LAB02_DEMAND_SIGNAL, LAB02_REPLENISHMENT_QUANTITY, SpecialistClient

_settings = Settings()
_client = SpecialistClient(settings=_settings)
_STOCK_RISK_LEVELS = {"low", "medium", "high", "critical"}


def _validate_stock_risk_level(stock_risk_level: str) -> None:
    if stock_risk_level not in _STOCK_RISK_LEVELS:
        raise ValueError("stock_risk_level must be low, medium, high, or critical")


def _validate_planned_quantity(quantity: int) -> None:
    if quantity != LAB02_REPLENISHMENT_QUANTITY:
        raise ValueError(f"Lab 02 replenishment and shipment quantity must be {LAB02_REPLENISHMENT_QUANTITY}")


@tool(name="ask_inventory", description="Get stock, sellable units, risk, and transfer source for one SKU and store.")
async def ask_inventory(sku: str, store_id: str) -> str:
    """Fetch inventory findings before deciding replenishment, shipping, or pricing inputs.

    Args:
        sku: The requested ZavaShop SKU.
        store_id: The target store identifier.
    Returns:
        Inventory specialist output containing stock, risk, and nearby transfer details.
    """

    goal = (
        f"Check stock risk for SKU {sku} at {store_id}. In stock_view, report on_hand, reserved, "
        "and sellable quantity explicitly. In nearby_locations, report transfer_source_location_id and "
        "transfer_available_qty when present; use the risk_level field. Do not infer missing values."
    )
    return await _client.invoke(name="inventory", url=_settings.inventory_a2a_url, goal=goal)


@tool(name="ask_supplier", description="List suppliers and draft the planned 80-unit replenishment PO.")
async def ask_supplier(sku: str, store_id: str, on_hand: int, reserved: int, stock_risk_level: str) -> str:
    """Draft the Lab 02 planned quantity using inventory findings, not supplier capacity as order size.

    Args:
        sku: The requested ZavaShop SKU.
        store_id: The target store identifier.
        on_hand: Physical units on hand reported by inventory.
        reserved: Units reserved for orders as reported by inventory.
        stock_risk_level: The risk level reported by inventory.
    Returns:
        Supplier specialist output with the draft purchase order and requested quantity.
    """

    _validate_stock_risk_level(stock_risk_level)
    if on_hand < 0 or reserved < 0:
        raise ValueError("Inventory quantities cannot be negative")
    if reserved > on_hand:
        raise ValueError("reserved cannot exceed on_hand")
    sellable = on_hand - reserved
    goal = (
        f"List suppliers and draft a purchase order for SKU {sku} at {store_id} "
        f"for exactly {LAB02_REPLENISHMENT_QUANTITY} units. Inventory findings: on_hand={on_hand}, "
        f"reserved={reserved}, sellable={sellable}, stock_risk_level={stock_risk_level}. "
        "The supplier's available_qty is capacity, not the requested order quantity; do not substitute it. "
        "Return the draft quantity explicitly."
    )
    return await _client.invoke(name="supplier", url=_settings.supplier_a2a_url, goal=goal)


@tool(name="ask_logistics", description="Quote the planned replenishment shipment from the inventory transfer source.")
async def ask_logistics(sku: str, source_location_id: str, destination_store_id: str, quantity: int) -> str:
    """Quote a shipment using the inventory source and planned purchase-order quantity.

    Args:
        sku: The requested ZavaShop SKU.
        source_location_id: The transfer source reported by inventory.
        destination_store_id: The target store identifier.
        quantity: The planned purchase-order quantity, exactly 80 units for Lab 02.
    Returns:
        Logistics specialist output with the shipment quote or explicit unavailability.
    """

    _validate_planned_quantity(quantity)
    if not source_location_id.strip() or not destination_store_id.strip():
        raise ValueError("A source location and destination store are required for a shipment quote")
    goal = (
        f"Quote shipment for SKU {sku} from source location {source_location_id} to destination store "
        f"{destination_store_id} quantity {quantity}. This is a quote only; do not initiate a shipment."
    )
    return await _client.invoke(name="logistics", url=_settings.logistics_a2a_url, goal=goal)


@tool(
    name="ask_pricing",
    description="Recommend a price using inventory risk, demand signal, and planned replenishment.",
)
async def ask_pricing(
    sku: str,
    store_id: str,
    stock_risk_level: str,
    on_hand: int,
    replenishment_qty: int,
    demand_signal: str | None = None,
) -> str:
    """Get price guidance using validated stock and planning context.

    Args:
        sku: The requested ZavaShop SKU.
        store_id: The target store identifier.
        demand_signal: User-provided signal; omit it to use the Lab 02 high-demand scenario assumption.
        stock_risk_level: The risk level reported by inventory.
        on_hand: Physical units on hand reported by inventory.
        replenishment_qty: Planned replenishment quantity from the draft PO.
    Returns:
        Pricing specialist recommendation based on the provided inputs.
    """

    _validate_stock_risk_level(stock_risk_level)
    if demand_signal is not None and not demand_signal.strip():
        raise ValueError("A provided demand signal cannot be empty")
    if on_hand < 0:
        raise ValueError("on_hand cannot be negative")
    _validate_planned_quantity(replenishment_qty)
    demand_context = demand_signal or LAB02_DEMAND_SIGNAL
    if demand_signal is None:
        demand_context = f"{demand_context} (Lab 02 stock-out planning assumption, not observed market data)"
    goal = (
        f"Recommend price for SKU {sku} at {store_id} with demand_signal={demand_context}, "
        f"stock_risk_level={stock_risk_level}, on_hand={on_hand}, "
        f"replenishment_qty={replenishment_qty}. Use these values as inputs; do not invent alternatives."
    )
    return await _client.invoke(name="pricing", url=_settings.pricing_a2a_url, goal=goal)


TOOLS: list[FunctionTool] = [ask_inventory, ask_supplier, ask_logistics, ask_pricing]
