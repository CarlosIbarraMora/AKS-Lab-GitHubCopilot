"""In-memory supplier catalog and purchase-order draft store."""

from __future__ import annotations

import asyncio
import hashlib

from .models import PurchaseOrderDraft, PurchaseOrderRequest, SupplierList, SupplierOption, SupplierQuery

# TODO: replace with Cosmos DB
_SUPPLIERS: dict[str, list[SupplierOption]] = {
    "ZS-1042": [
        SupplierOption(
            supplier_id="sup-northstar",
            name="Northstar Apparel Manufacturing",
            available_qty=900,
            unit_cost=18.75,
            lead_time_days=4,
            reliability_score=0.97,
        ),
        SupplierOption(
            supplier_id="sup-contoso-textiles",
            name="Contoso Textiles Direct",
            available_qty=500,
            unit_cost=17.95,
            lead_time_days=6,
            reliability_score=0.91,
        ),
        SupplierOption(
            supplier_id="sup-fabrikam-rapid",
            name="Fabrikam Rapid Replenishment",
            available_qty=240,
            unit_cost=20.10,
            lead_time_days=2,
            reliability_score=0.88,
        ),
    ],
    "ZS-2048": [
        SupplierOption(
            supplier_id="sup-contoso-textiles",
            name="Contoso Textiles Direct",
            available_qty=320,
            unit_cost=12.40,
            lead_time_days=5,
            reliability_score=0.91,
        ),
    ],
}
_PURCHASE_ORDERS: dict[str, PurchaseOrderDraft] = {}


async def find_suppliers(query: SupplierQuery) -> SupplierList:
    """Return ranked supplier options for a known SKU."""
    await asyncio.sleep(0)
    suppliers = _SUPPLIERS.get(query.sku)
    if suppliers is None:
        raise ValueError(f"No supplier information exists for SKU {query.sku}.")

    ranked_suppliers = sorted(
        suppliers,
        key=lambda supplier: (-supplier.reliability_score, supplier.lead_time_days, supplier.unit_cost),
    )
    return SupplierList(sku=query.sku, suppliers=ranked_suppliers)


async def create_purchase_order(request: PurchaseOrderRequest) -> PurchaseOrderDraft:
    """Store and return a deterministic purchase-order draft without submitting it."""
    await asyncio.sleep(0)
    supplier = _supplier_for(request.sku, request.supplier_id)
    if request.quantity > supplier.available_qty:
        raise ValueError(
            f"Supplier {supplier.supplier_id} has {supplier.available_qty} units available; "
            f"cannot draft {request.quantity} units."
        )

    draft = PurchaseOrderDraft(
        sku=request.sku,
        store_id=request.store_id,
        supplier_id=supplier.supplier_id,
        quantity=request.quantity,
        unit_cost=supplier.unit_cost,
        estimated_delivery_days=supplier.lead_time_days,
        status="draft",
        rationale=(
            f"Draft only: {supplier.name} can supply {request.quantity} units at "
            f"${supplier.unit_cost:.2f} per unit with an estimated {supplier.lead_time_days}-day lead time. "
            "Approval is required before ordering."
        ),
    )
    _PURCHASE_ORDERS[_draft_key(request)] = draft
    return draft


def _supplier_for(sku: str, supplier_id: str) -> SupplierOption:
    for supplier in _SUPPLIERS.get(sku, []):
        if supplier.supplier_id == supplier_id:
            return supplier
    raise ValueError(f"Supplier {supplier_id} is not available for SKU {sku}.")


def _draft_key(request: PurchaseOrderRequest) -> str:
    draft_data = f"{request.sku}:{request.store_id}:{request.supplier_id}:{request.quantity}"
    return f"PO-{hashlib.sha256(draft_data.encode()).hexdigest()[:10].upper()}"
