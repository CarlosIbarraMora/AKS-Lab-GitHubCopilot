"""A2A HTTP client for orchestrator-to-specialist calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

import httpx

from src.shared.server import InvokeRequest, InvokeResponse
from src.shared.settings import Settings

A2A_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
LAB02_REPLENISHMENT_QUANTITY = 80
LAB02_DEMAND_SIGNAL = "high"


@dataclass(frozen=True)
class A2AClient:
    """HTTP client for invoking ZavaShop specialist agents."""

    settings: Settings

    async def invoke(self, *, name: str, url: str, goal: str, run_id: str | None = None) -> str:
        request = InvokeRequest(run_id=run_id or uuid4().hex, goal=goal, context={"caller": "orchestrator"})
        async with httpx.AsyncClient(timeout=A2A_TIMEOUT) as client:
            response = await client.post(url, json=request.model_dump())
            response.raise_for_status()
        envelope = InvokeResponse.model_validate(response.json())
        return envelope.output


SpecialistClient = A2AClient


class SpecialistInvoker(Protocol):
    """Protocol for specialist invokers used by production code and tests."""

    @property
    def settings(self) -> Settings:
        """Settings used to resolve specialist endpoint URLs."""

        ...

    async def invoke(self, *, name: str, url: str, goal: str, run_id: str | None = None) -> str: ...
