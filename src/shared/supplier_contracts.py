"""Canonical Supplier agent response contract shared with the orchestrator."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SupplierOptionSummary(BaseModel):
    """Verified capacity and commercial terms for one supplier."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", allow_inf_nan=False)

    supplier_id: str = Field(min_length=1)
    name: str | None = Field(default=None, min_length=1)
    available_qty: int = Field(ge=0)
    unit_cost: float = Field(gt=0)
    lead_time_days: int = Field(ge=0)
    reliability_score: float = Field(ge=0, le=1)


class SupplierCatalogView(BaseModel):
    """Canonical SupplierList-shaped view used in agent responses."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    sku: str = Field(min_length=1)
    suppliers: list[SupplierOptionSummary] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_supplier_ids(self) -> Self:
        supplier_ids = [supplier.supplier_id for supplier in self.suppliers]
        if len(supplier_ids) != len(set(supplier_ids)):
            raise ValueError("supplier IDs must be unique")
        return self


class SupplierDraftView(BaseModel):
    """Advisory purchase-order draft; approval is always required before ordering."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", allow_inf_nan=False)

    supplier_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    unit_cost: float = Field(gt=0)
    estimated_delivery_days: int = Field(ge=0)
    status: Literal["draft"]
    approval_required: Literal[True]
    rationale: str = Field(min_length=1)


class SupplierAgentOutput(BaseModel):
    """Canonical JSON response contract for SupplierAgent."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    sku: str = Field(min_length=1)
    store_id: str = Field(min_length=1)
    supplier_view: SupplierCatalogView
    po_view: SupplierDraftView
    recommended_supplier: str = Field(min_length=1)
    next_action: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_draft_against_supplier_findings(self) -> Self:
        if self.supplier_view.sku != self.sku:
            raise ValueError("supplier catalog SKU must match the response SKU")
        if self.recommended_supplier != self.po_view.supplier_id:
            raise ValueError("recommended supplier must match the draft supplier")

        supplier = next(
            (option for option in self.supplier_view.suppliers if option.supplier_id == self.recommended_supplier),
            None,
        )
        if supplier is None:
            raise ValueError("draft supplier must exist in the supplier catalog")
        if self.po_view.quantity > supplier.available_qty:
            raise ValueError("draft quantity exceeds supplier capacity")
        if self.po_view.unit_cost != supplier.unit_cost:
            raise ValueError("draft unit cost must match supplier terms")
        if self.po_view.estimated_delivery_days != supplier.lead_time_days:
            raise ValueError("draft delivery estimate must match supplier lead time")
        return self
