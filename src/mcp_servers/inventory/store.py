"""In-memory inventory store for the inventory MCP server."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from .models import StockQuery, StockReport

CRITICAL_STOCKOUT_DAYS = 2
HIGH_STOCKOUT_DAYS = 5
MIN_TRANSFER_UNITS = 40


@dataclass(frozen=True, slots=True)
class _InventoryPosition:
    on_hand: int
    reserved: int
    reorder_point: int
    days_until_stockout: int | None


# TODO: replace with Cosmos DB
_INVENTORY: dict[str, dict[str, _InventoryPosition]] = {
    "ZS-1042": {
        "store-101": _InventoryPosition(on_hand=6, reserved=4, reorder_point=24, days_until_stockout=2),
        "store-202": _InventoryPosition(on_hand=84, reserved=9, reorder_point=30, days_until_stockout=16),
        "wh-east": _InventoryPosition(on_hand=420, reserved=65, reorder_point=150, days_until_stockout=30),
    },
    "ZS-2048": {
        "store-101": _InventoryPosition(on_hand=38, reserved=3, reorder_point=20, days_until_stockout=12),
        "store-202": _InventoryPosition(on_hand=22, reserved=2, reorder_point=18, days_until_stockout=9),
        "wh-east": _InventoryPosition(on_hand=160, reserved=20, reorder_point=80, days_until_stockout=25),
    },
}


async def lookup_stock(query: StockQuery) -> StockReport:
    """Look up stock and recovery options for a SKU at a target store."""
    await asyncio.sleep(0)

    sku_locations = _INVENTORY.get(query.sku)
    if sku_locations is None:
        raise ValueError(f"No inventory record exists for SKU {query.sku}.")

    target = sku_locations.get(query.store_id)
    if target is None:
        raise ValueError(f"No inventory record exists for SKU {query.sku} at {query.store_id}.")

    transfer_sources = [
        (location_id, position, position.on_hand - position.reserved)
        for location_id, position in sku_locations.items()
        if location_id != query.store_id and position.on_hand - position.reserved >= MIN_TRANSFER_UNITS
    ]
    transfer_source = transfer_sources[0] if transfer_sources else None
    available = max(target.on_hand - target.reserved, 0)
    risk_level = _risk_level(
        available=available,
        reorder_point=target.reorder_point,
        days_until_stockout=target.days_until_stockout,
    )

    return StockReport(
        sku=query.sku,
        store_id=query.store_id,
        on_hand=target.on_hand,
        reserved=target.reserved,
        reorder_point=target.reorder_point,
        days_until_stockout=target.days_until_stockout,
        transfer_source_location_id=transfer_source[0] if transfer_source is not None else None,
        transfer_available_qty=transfer_source[2] if transfer_source is not None else 0,
        risk_level=risk_level,
        recommendation=_recommendation(
            query=query,
            available=available,
            risk_level=risk_level,
            transfer_source=transfer_source,
        ),
    )


def _risk_level(
    *,
    available: int,
    reorder_point: int,
    days_until_stockout: int | None,
) -> Literal["low", "medium", "high", "critical"]:
    if available == 0 or (days_until_stockout is not None and days_until_stockout <= CRITICAL_STOCKOUT_DAYS):
        return "critical"
    if available < reorder_point or (days_until_stockout is not None and days_until_stockout <= HIGH_STOCKOUT_DAYS):
        return "high"
    if available < reorder_point * 2:
        return "medium"
    return "low"


def _recommendation(
    *,
    query: StockQuery,
    available: int,
    risk_level: Literal["low", "medium", "high", "critical"],
    transfer_source: tuple[str, _InventoryPosition, int] | None,
) -> str:
    if transfer_source is not None and risk_level in {"critical", "high"}:
        location_id = transfer_source[0]
        return (
            f"{query.store_id} has {available} sellable units of {query.sku}; prioritize a transfer from "
            f"{location_id} and draft supplier replenishment as backup."
        )
    if risk_level in {"critical", "high"}:
        return f"{query.store_id} has {available} sellable units of {query.sku}; draft supplier replenishment now."
    return f"{query.store_id} has {available} sellable units of {query.sku}; monitor before ordering."
