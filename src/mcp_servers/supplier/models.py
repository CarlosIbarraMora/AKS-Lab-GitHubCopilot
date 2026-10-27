"""Pydantic contracts for the supplier MCP server."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SupplierQuery(BaseModel):
    """Request supplier options for one SKU."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(min_length=1, description="ZavaShop SKU to source.")


class SupplierOption(BaseModel):
    """Supplier capacity and commercial terms for one SKU."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    supplier_id: str = Field(description="Stable supplier identifier.")
    name: str = Field(description="Supplier display name.")
    available_qty: int = Field(ge=0, description="Units currently available to order.")
    unit_cost: float = Field(gt=0, description="Unit wholesale cost in USD.")
    lead_time_days: int = Field(ge=0, description="Expected lead time in days.")
    reliability_score: float = Field(ge=0, le=1, description="Historical fulfillment reliability from 0 to 1.")


class SupplierList(BaseModel):
    """Ranked supplier options returned to specialist agents."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(description="ZavaShop SKU that was sourced.")
    suppliers: list[SupplierOption] = Field(description="Ranked supplier options for the SKU.")


class PurchaseOrderRequest(BaseModel):
    """Request a draft replenishment order for one SKU and store."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(min_length=1, description="ZavaShop SKU to order.")
    store_id: str = Field(min_length=1, description="Store receiving the replenishment.")
    supplier_id: str = Field(min_length=1, description="Chosen supplier identifier.")
    quantity: int = Field(ge=1, description="Units to include in the draft order.")


class PurchaseOrderDraft(BaseModel):
    """Draft purchase-order details; this contract does not authorize submission."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(description="ZavaShop SKU to order.")
    store_id: str = Field(description="Store receiving the replenishment.")
    supplier_id: str = Field(description="Chosen supplier identifier.")
    quantity: int = Field(ge=1, description="Units included in the draft order.")
    unit_cost: float = Field(gt=0, description="Supplier cost per unit in USD.")
    estimated_delivery_days: int = Field(ge=0, description="Expected supplier lead time in days.")
    status: Literal["draft"] = Field(description="Draft status; no purchase order is submitted.")
    rationale: str = Field(min_length=1, description="Reason and key terms for the draft.")
