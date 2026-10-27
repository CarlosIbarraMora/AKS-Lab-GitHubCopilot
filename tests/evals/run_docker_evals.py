"""Opt-in live Docker evaluator for the ZavaShop /plan endpoint."""

from __future__ import annotations

import asyncio
import json
import os
import time
from http import HTTPStatus

import httpx
from pydantic import BaseModel, ConfigDict

from src.agents.orchestrator.contracts import Plan
from tests.evals.contracts import EvalScenario, load_scenarios


class LiveEvalResult(BaseModel):
    """Observed result of one live HTTP scenario."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    passed: bool
    latency_s: float
    http_status: int | None
    missing_mentions: list[str]
    errors: list[str]
    notes: list[str]


def _flatten_plan(plan: Plan) -> str:
    return plan.model_dump_json().lower()


async def run_one(client: httpx.AsyncClient, scenario: EvalScenario) -> LiveEvalResult:
    started = time.perf_counter()
    status: int | None = None
    missing_mentions: list[str] = []
    errors: list[str] = []
    notes: list[str] = []
    try:
        response = await client.post(
            "/plan",
            json={"goal": scenario.goal, "sku": scenario.sku, "store_id": scenario.store_id},
            timeout=httpx.Timeout(scenario.max_latency_s + 30),
        )
        status = response.status_code
        if status != HTTPStatus.OK:
            errors.append(f"HTTP {status}")
        else:
            try:
                plan = Plan.model_validate(response.json())
            except (ValueError, json.JSONDecodeError) as exc:
                errors.append(f"invalid Plan response: {type(exc).__name__}")
            else:
                actual = _flatten_plan(plan)
                missing_mentions = [term for term in scenario.must_mention if term.lower() not in actual]
    except httpx.HTTPError as exc:
        errors.append(f"HTTP request failed: {type(exc).__name__}")

    latency_s = time.perf_counter() - started
    if latency_s > scenario.max_latency_s:
        errors.append(f"latency {latency_s:.2f}s exceeded {scenario.max_latency_s:.2f}s budget")
    if scenario.must_call or scenario.forbid_call:
        notes.append("specialist expectations are informational for live /plan; no trace is exposed by Plan")
    return LiveEvalResult(
        id=scenario.id,
        passed=status == HTTPStatus.OK and not errors and not missing_mentions,
        latency_s=latency_s,
        http_status=status,
        missing_mentions=missing_mentions,
        errors=errors,
        notes=notes,
    )


async def run() -> int:
    """Warm the explicitly configured live endpoint, then evaluate S1-S5."""

    endpoint = os.environ.get("ZAVA_ENDPOINT", "http://localhost:8000").rstrip("/")
    scenarios = load_scenarios()
    client_timeout = httpx.Timeout(max(scenario.max_latency_s for scenario in scenarios) + 30)
    results: list[LiveEvalResult] = []
    print("Known live issue: Supplier MCP list_suppliers JSON decoding may fail and cause Logistics to be skipped.")
    async with httpx.AsyncClient(base_url=endpoint, timeout=client_timeout) as client:
        try:
            ready = await client.get("/readyz")
            if ready.status_code != HTTPStatus.OK:
                raise RuntimeError(f"/readyz returned HTTP {ready.status_code}")
            warmup = await client.post(
                "/plan",
                json={
                    "goal": "Warm-up request; do not make or submit a business decision.",
                    "sku": "ZS-1042",
                    "store_id": "store-101",
                },
                timeout=client_timeout,
            )
            warmup.raise_for_status()
            Plan.model_validate(warmup.json())
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            print(f"LIVE EVAL BLOCKED endpoint={endpoint} reason={type(exc).__name__}")
            print(
                "Known issue: live Supplier MCP list_suppliers may fail JSON decoding, "
                "which causes Logistics to be skipped."
            )
            return 1

        for scenario in scenarios:
            result = await run_one(client, scenario)
            results.append(result)
            outcome = "PASS" if result.passed else "FAIL"
            print(f"{outcome} {result.id} latency={result.latency_s:.2f}s http={result.http_status}")
            if result.missing_mentions:
                print(f"  missing_mentions={result.missing_mentions}")
            for error in result.errors:
                print(f"  error={error}")
            for note in result.notes:
                print(f"  note={note}")

    failures = sum(not result.passed for result in results)
    print(f"live_failures={failures} total={len(results)} endpoint={endpoint}")
    if failures:
        print("Known issue: the live Supplier MCP list_suppliers result may fail JSON decoding, skipping Logistics.")
    return failures


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
