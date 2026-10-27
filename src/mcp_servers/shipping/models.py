"""Pydantic contracts for the shipping MCP server."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ShipmentQuoteRequest(BaseModel):
    """Request a shipment quote for a SKU between two locations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(min_length=1, description="ZavaShop SKU to ship.")
    source_location_id: str = Field(min_length=1, description="Store or warehouse shipping the SKU.")
    destination_store_id: str = Field(min_length=1, description="Store receiving the shipment.")
    quantity: int = Field(ge=1, description="Units to ship.")


class ShipmentQuote(BaseModel):
    """Shipment quote or explicit unavailability for a requested route."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(description="ZavaShop SKU being shipped.")
    source_location_id: str = Field(description="Store or warehouse shipping the SKU.")
    destination_store_id: str = Field(description="Store receiving the shipment.")
    quantity: int = Field(ge=1, description="Units requested for shipment.")
    carrier: str | None = Field(default=None, description="Quoted carrier, or null if unavailable.")
    estimated_delivery_days: int | None = Field(
        default=None,
        ge=0,
        description="Estimated transit time in days, or null if unavailable.",
    )
    status: Literal["quoted", "unavailable"] = Field(description="Whether a local quote exists for this route.")
    rationale: str = Field(min_length=1, description="Quote details or the reason service is unavailable.")
