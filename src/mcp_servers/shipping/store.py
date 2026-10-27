"""In-memory shipment quote fixtures and quote store."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .models import ShipmentQuote, ShipmentQuoteRequest


@dataclass(frozen=True, slots=True)
class CarrierRate:
    """Internal deterministic carrier rate for a supported SKU route."""

    carrier: str
    estimated_delivery_days: int


# TODO: replace with Cosmos DB
_SHIPMENT_RATES: dict[tuple[str, str, str], CarrierRate] = {
    ("ZS-1042", "store-202", "store-101"): CarrierRate(
        carrier="Fabrikam RapidShip",
        estimated_delivery_days=1,
    ),
    ("ZS-1042", "wh-east", "store-101"): CarrierRate(
        carrier="Northwind Freight",
        estimated_delivery_days=2,
    ),
    ("ZS-1042", "wh-east", "store-202"): CarrierRate(
        carrier="Blue Yonder Logistics",
        estimated_delivery_days=4,
    ),
}
_SHIPMENT_QUOTES: dict[tuple[str, str, str, int], ShipmentQuote] = {}


async def quote_shipment(request: ShipmentQuoteRequest) -> ShipmentQuote:
    """Return and store the deterministic quote for a supported SKU route."""
    await asyncio.sleep(0)
    quote_key = (request.sku, request.source_location_id, request.destination_store_id, request.quantity)
    existing_quote = _SHIPMENT_QUOTES.get(quote_key)
    if existing_quote is not None:
        return existing_quote

    rate = _SHIPMENT_RATES.get(quote_key[:3])
    if rate is None:
        quote = ShipmentQuote(
            sku=request.sku,
            source_location_id=request.source_location_id,
            destination_store_id=request.destination_store_id,
            quantity=request.quantity,
            carrier=None,
            estimated_delivery_days=None,
            status="unavailable",
            rationale=(
                f"No local shipment quote is available for SKU {request.sku} from "
                f"{request.source_location_id} to {request.destination_store_id}."
            ),
        )
    else:
        quote = ShipmentQuote(
            sku=request.sku,
            source_location_id=request.source_location_id,
            destination_store_id=request.destination_store_id,
            quantity=request.quantity,
            carrier=rate.carrier,
            estimated_delivery_days=rate.estimated_delivery_days,
            status="quoted",
            rationale=(
                f"{rate.carrier} can ship {request.quantity} units of SKU {request.sku} from "
                f"{request.source_location_id} to {request.destination_store_id} in "
                f"{rate.estimated_delivery_days} day(s). This is a quote only; no shipment was initiated."
            ),
        )

    _SHIPMENT_QUOTES[quote_key] = quote
    return quote
