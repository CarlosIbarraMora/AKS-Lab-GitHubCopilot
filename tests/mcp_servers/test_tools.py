"""Direct contract and business-rule tests for FastMCP tools."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.mcp_servers.inventory.models import StockQuery, StockReport
from src.mcp_servers.inventory.server import check_stock
from src.mcp_servers.pricing.models import PriceRecommendation, PricingQuery
from src.mcp_servers.pricing.server import recommend_price
from src.mcp_servers.shipping.models import ShipmentQuote, ShipmentQuoteRequest
from src.mcp_servers.shipping.server import quote_shipment
from src.mcp_servers.supplier.models import (
    PurchaseOrderDraft,
    PurchaseOrderRequest,
    SupplierList,
    SupplierQuery,
)
from src.mcp_servers.supplier.server import draft_po, list_suppliers

EXPECTED_ON_HAND = 6
EXPECTED_RESERVED = 4
EXPECTED_TRANSFER_AVAILABLE = 75
EXPECTED_SUPPLIER_COUNT = 3
EXPECTED_DRAFT_QUANTITY = 80
EXPECTED_SUPPLIER_CAPACITY = 240
EXPECTED_LEAD_TIME_DAYS = 2
PRICE_FLOOR = 34.99
PRICE_CEILING = 44.99


@pytest.mark.asyncio
async def test_check_stock_returns_valid_contract_and_transfer_capacity() -> None:
    report = await check_stock(StockQuery(sku="ZS-1042", store_id="store-101"))

    assert isinstance(report, StockReport)
    assert report.sku == "ZS-1042"
    assert report.store_id == "store-101"
    assert report.on_hand == EXPECTED_ON_HAND
    assert report.reserved == EXPECTED_RESERVED
    assert report.risk_level == "critical"
    assert report.transfer_source_location_id == "store-202"
    assert report.transfer_available_qty == EXPECTED_TRANSFER_AVAILABLE
    assert "store-202" in report.recommendation


@pytest.mark.asyncio
async def test_check_stock_rejects_invalid_and_unknown_inputs() -> None:
    with pytest.raises(ValidationError):
        StockQuery(sku="", store_id="store-101")
    with pytest.raises(ValidationError):
        StockQuery.model_validate({"sku": "ZS-1042", "store_id": "store-101", "extra": True})
    with pytest.raises(ValueError, match="No inventory record"):
        await check_stock(StockQuery(sku="UNKNOWN-SKU", store_id="store-101"))


@pytest.mark.asyncio
async def test_list_suppliers_returns_ranked_verified_catalog() -> None:
    suppliers = await list_suppliers(SupplierQuery(sku="ZS-1042"))

    assert isinstance(suppliers, SupplierList)
    assert suppliers.sku == "ZS-1042"
    assert len(suppliers.suppliers) == EXPECTED_SUPPLIER_COUNT
    assert suppliers.suppliers[0].supplier_id == "sup-northstar"
    assert {supplier.supplier_id for supplier in suppliers.suppliers} == {
        "sup-northstar",
        "sup-contoso-textiles",
        "sup-fabrikam-rapid",
    }
    assert all(supplier.available_qty > 0 and supplier.unit_cost > 0 for supplier in suppliers.suppliers)
    assert all(0 <= supplier.reliability_score <= 1 for supplier in suppliers.suppliers)


@pytest.mark.asyncio
async def test_list_suppliers_rejects_invalid_and_unknown_sku() -> None:
    with pytest.raises(ValidationError):
        SupplierQuery(sku="")
    with pytest.raises(ValueError, match="No supplier information"):
        await list_suppliers(SupplierQuery(sku="UNKNOWN-SKU"))


@pytest.mark.asyncio
async def test_draft_po_preserves_capacity_and_advisory_status() -> None:
    draft = await draft_po(
        PurchaseOrderRequest(
            sku="ZS-1042",
            store_id="store-101",
            supplier_id="sup-fabrikam-rapid",
            quantity=EXPECTED_DRAFT_QUANTITY,
        )
    )

    assert isinstance(draft, PurchaseOrderDraft)
    assert draft.sku == "ZS-1042"
    assert draft.store_id == "store-101"
    assert draft.supplier_id == "sup-fabrikam-rapid"
    assert draft.quantity == EXPECTED_DRAFT_QUANTITY
    assert draft.quantity <= EXPECTED_SUPPLIER_CAPACITY
    assert draft.unit_cost == pytest.approx(20.1)
    assert draft.estimated_delivery_days == EXPECTED_LEAD_TIME_DAYS
    assert draft.status == "draft"
    assert "Approval is required" in draft.rationale


@pytest.mark.asyncio
async def test_draft_po_rejects_invalid_quantity_capacity_and_supplier() -> None:
    with pytest.raises(ValidationError):
        PurchaseOrderRequest(sku="ZS-1042", store_id="store-101", supplier_id="sup-fabrikam-rapid", quantity=0)
    with pytest.raises(ValueError, match="cannot draft"):
        await draft_po(
            PurchaseOrderRequest(
                sku="ZS-1042",
                store_id="store-101",
                supplier_id="sup-fabrikam-rapid",
                quantity=241,
            )
        )
    with pytest.raises(ValueError, match="is not available"):
        await draft_po(
            PurchaseOrderRequest(
                sku="ZS-1042",
                store_id="store-101",
                supplier_id="unknown-supplier",
                quantity=1,
            )
        )


@pytest.mark.asyncio
async def test_quote_shipment_returns_quote_without_initiating_shipment() -> None:
    quote = await quote_shipment(
        ShipmentQuoteRequest(
            sku="ZS-1042",
            source_location_id="store-202",
            destination_store_id="store-101",
            quantity=75,
        )
    )

    assert isinstance(quote, ShipmentQuote)
    assert quote.sku == "ZS-1042"
    assert quote.source_location_id == "store-202"
    assert quote.destination_store_id == "store-101"
    assert quote.quantity == EXPECTED_TRANSFER_AVAILABLE
    assert quote.status == "quoted"
    assert quote.carrier == "Fabrikam RapidShip"
    assert quote.estimated_delivery_days == 1
    assert "no shipment was initiated" in quote.rationale


@pytest.mark.asyncio
async def test_quote_shipment_rejects_invalid_quantity_and_reports_unknown_route() -> None:
    with pytest.raises(ValidationError):
        ShipmentQuoteRequest(
            sku="ZS-1042",
            source_location_id="store-202",
            destination_store_id="store-101",
            quantity=0,
        )
    quote = await quote_shipment(
        ShipmentQuoteRequest(
            sku="ZS-1042",
            source_location_id="store-101",
            destination_store_id="store-202",
            quantity=1,
        )
    )
    assert quote.status == "unavailable"
    assert quote.carrier is None
    assert quote.estimated_delivery_days is None


@pytest.mark.asyncio
async def test_recommend_price_returns_bounded_usd_recommendation() -> None:
    recommendation = await recommend_price(
        PricingQuery(
            sku="ZS-1042",
            store_id="store-101",
            demand_signal="high demand spike",
            stock_risk_level="critical",
            on_hand=6,
            replenishment_qty=80,
        )
    )

    assert isinstance(recommendation, PriceRecommendation)
    assert recommendation.sku == "ZS-1042"
    assert recommendation.store_id == "store-101"
    assert recommendation.currency == "USD"
    assert recommendation.current_price == pytest.approx(39.99)
    assert PRICE_FLOOR <= recommendation.recommended_price <= PRICE_CEILING
    assert recommendation.recommended_price > recommendation.current_price
    assert "critical" in recommendation.rationale
    assert recommendation.margin_impact


@pytest.mark.asyncio
async def test_recommend_price_rejects_invalid_contract_and_unknown_sku() -> None:
    with pytest.raises(ValidationError):
        PricingQuery.model_validate(
            {
                "sku": "ZS-1042",
                "store_id": "store-101",
                "demand_signal": "high",
                "stock_risk_level": "extreme",
            }
        )
    with pytest.raises(ValidationError):
        PricingQuery(sku="ZS-1042", store_id="store-101", demand_signal="", stock_risk_level="low")
    with pytest.raises(ValueError, match="No pricing information"):
        await recommend_price(
            PricingQuery(
                sku="UNKNOWN-SKU",
                store_id="store-101",
                demand_signal="balanced",
                stock_risk_level="low",
            )
        )
