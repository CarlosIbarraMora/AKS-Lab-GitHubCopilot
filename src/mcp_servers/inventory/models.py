"""Pydantic contracts for the inventory MCP server."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StockQuery(BaseModel):
    """Request stock status for one SKU at one store."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(min_length=1, description="ZavaShop SKU to inspect.")
    store_id: str = Field(min_length=1, description="Target store location ID.")


class StockReport(BaseModel):
    """Inventory status and recovery guidance for a requested SKU and store."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(description="ZavaShop SKU that was inspected.")
    store_id: str = Field(description="Target store location ID.")
    on_hand: int = Field(ge=0, description="Units physically on hand.")
    reserved: int = Field(ge=0, description="Units already reserved for orders.")
    reorder_point: int = Field(ge=0, description="Threshold that triggers replenishment.")
    days_until_stockout: int | None = Field(default=None, ge=0, description="Estimated days until stock reaches zero.")
    transfer_source_location_id: str | None = Field(
        default=None,
        description="Nearby location with available stock for a transfer, if any.",
    )
    transfer_available_qty: int = Field(default=0, ge=0, description="Sellable units at the transfer source.")
    risk_level: Literal["low", "medium", "high", "critical"] = Field(description="Stock-out risk level.")
    recommendation: str = Field(min_length=1, description="Suggested inventory action.")
