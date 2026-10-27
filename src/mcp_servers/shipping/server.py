"""FastMCP entrypoint for the shipping MCP server."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.shared.keyvault import hydrate_key_vault

from .models import ShipmentQuote, ShipmentQuoteRequest
from .store import quote_shipment as quote_shipment_from_store

log = structlog.get_logger(__name__).bind(service="shipping-mcp")


@asynccontextmanager
async def _keyvault_lifespan(_server: FastMCP[None]) -> AsyncGenerator[None, None]:
    """Hydrate optional Key Vault settings before serving MCP requests."""

    await hydrate_key_vault(os.environ.get("ZAVA_KV_URL"))
    yield


mcp = FastMCP("shipping-mcp", host="0.0.0.0", port=8080, streamable_http_path="/mcp", lifespan=_keyvault_lifespan)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Request) -> JSONResponse:
    """Return process health for container probes."""
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/readyz", methods=["GET"])
async def readyz(_request: Request) -> JSONResponse:
    """Return readiness for container probes."""
    return JSONResponse({"status": "ready", "name": "shipping-mcp"})


@mcp.tool()
async def quote_shipment(request: ShipmentQuoteRequest) -> ShipmentQuote:
    """Quote a shipment between ZavaShop locations without initiating it.

    Args:
        request: SKU, source location, destination store, and shipment quantity.
    Returns:
        ShipmentQuote with the route, carrier and ETA, or an explicit unavailable status.
    """
    log.info(
        "tool.quote_shipment",
        sku=request.sku,
        source_location_id=request.source_location_id,
        destination_store_id=request.destination_store_id,
        quantity=request.quantity,
    )
    return await quote_shipment_from_store(request)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
