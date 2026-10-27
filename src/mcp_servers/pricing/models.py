"""Pydantic contracts for the pricing MCP server."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PricingQuery(BaseModel):
    """Request a price recommendation for one SKU and store."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(min_length=1, description="ZavaShop SKU to price.")
    store_id: str = Field(min_length=1, description="Store where the price applies.")
    demand_signal: str = Field(min_length=1, description="Observed demand signal for the SKU.")
    stock_risk_level: Literal["low", "medium", "high", "critical"] = Field(
        description="Inventory stock-out risk level."
    )
    on_hand: int = Field(default=0, ge=0, description="Units currently on hand, when available.")
    replenishment_qty: int = Field(default=0, ge=0, description="Units planned for replenishment, when available.")


class PriceRecommendation(BaseModel):
    """Typed pricing recommendation returned by the pricing MCP server."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(description="ZavaShop SKU that was priced.")
    store_id: str = Field(description="Store where the price applies.")
    current_price: float = Field(gt=0, description="Current shelf price in USD.")
    recommended_price: float = Field(gt=0, description="Recommended shelf price in USD.")
    currency: Literal["USD"] = Field(description="Currency used for both prices.")
    rationale: str = Field(min_length=1, description="Reasoning behind the recommendation.")
    margin_impact: str = Field(description="Expected unit margin impact summary.")
