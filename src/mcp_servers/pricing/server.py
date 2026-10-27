"""FastMCP entrypoint for the pricing MCP server."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.shared.keyvault import hydrate_key_vault

from .models import PriceRecommendation, PricingQuery
from .store import recommend_price_for

log = structlog.get_logger(__name__).bind(service="pricing-mcp")


@asynccontextmanager
async def _keyvault_lifespan(_server: FastMCP[None]) -> AsyncGenerator[None, None]:
    """Hydrate optional Key Vault settings before serving MCP requests."""

    await hydrate_key_vault(os.environ.get("ZAVA_KV_URL"))
    yield


mcp = FastMCP("pricing-mcp", host="0.0.0.0", port=8080, streamable_http_path="/mcp", lifespan=_keyvault_lifespan)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Request) -> JSONResponse:
    """Return process health for container probes."""
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/readyz", methods=["GET"])
async def readyz(_request: Request) -> JSONResponse:
    """Return readiness for container probes."""
    return JSONResponse({"status": "ready", "name": "pricing-mcp"})


@mcp.tool()
async def recommend_price(query: PricingQuery) -> PriceRecommendation:
    """Recommend a price for one SKU using demand and inventory context.

    Args:
        query: SKU, store, demand signal, and stock risk level to price.
    Returns:
        PriceRecommendation with current and recommended prices, currency, margin impact, and rationale.
    """
    log.info(
        "tool.recommend_price",
        sku=query.sku,
        store_id=query.store_id,
        demand_signal=query.demand_signal,
        stock_risk_level=query.stock_risk_level,
    )
    return await recommend_price_for(query)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
