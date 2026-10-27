"""Schema checks and deterministic WorkflowBuilder golden scenarios."""

from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path
from typing import Protocol

import httpx
import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from src.agents.orchestrator.contracts import Plan
from tests.agents._mock import MockChatClient
from tests.evals.contracts import EvalScenario, load_scenarios
from tests.evals.run_docker_evals import run_one

SCENARIO_TEST_PATH = Path(__file__)
SCENARIOS = load_scenarios()


class RecordedInvoker(Protocol):
    events: list[tuple[str, str]]
    goals: dict[str, str]


class IntegrationFleet(Protocol):
    client: AsyncClient
    invoker: RecordedInvoker
    supplier_chat: type[MockChatClient]


def _scenario_payload() -> dict[str, object]:
    return SCENARIOS[0].model_dump(mode="json")


def test_scenario_file_has_exactly_the_five_unique_golden_ids() -> None:
    assert tuple(scenario.id for scenario in SCENARIOS) == ("S1", "S2", "S3", "S4", "S5")
    assert all(scenario.must_mention for scenario in SCENARIOS)
    assert all(scenario.must_call for scenario in SCENARIOS)


@pytest.mark.parametrize(
    "payload",
    [
        {key: value for key, value in _scenario_payload().items() if key != "goal"},
        {**_scenario_payload(), "unexpected": "forbidden"},
        {**_scenario_payload(), "must_call": ["unknown-specialist"]},
        {**_scenario_payload(), "id": "S6"},
    ],
)
def test_eval_scenario_rejects_missing_extra_and_unknown_values(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EvalScenario.model_validate(payload)


def test_scenario_loader_rejects_malformed_jsonl_blank_rows_and_duplicate_ids(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.jsonl"
    malformed.write_text('{"id":', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid eval scenario at line 1"):
        load_scenarios(malformed)

    blank = tmp_path / "blank.jsonl"
    blank.write_text(SCENARIO_TEST_LINES[0] + "\n\n" + SCENARIO_TEST_LINES[1], encoding="utf-8")
    with pytest.raises(ValueError, match="Blank JSONL record"):
        load_scenarios(blank)

    duplicate = tmp_path / "duplicate.jsonl"
    first = json.dumps(_scenario_payload())
    duplicate.write_text("\n".join([first, first]), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate eval scenario id S1"):
        load_scenarios(duplicate)


def test_scenario_loader_rejects_missing_scenario_id_set(tmp_path: Path) -> None:
    missing = tmp_path / "missing.jsonl"
    missing.write_text("\n".join(SCENARIO_TEST_LINES[:-1]), encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one each"):
        load_scenarios(missing)


SCENARIO_TEST_LINES = [json.dumps(scenario.model_dump(mode="json")) for scenario in SCENARIOS]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: f"{scenario.id}-{scenario.goal[:24]}")
@pytest.mark.asyncio
async def test_golden_scenario_runs_real_workflow_and_checks_plan_and_trace(
    scenario: EvalScenario,
    fleet: IntegrationFleet,
) -> None:
    response = await fleet.client.post(
        "/plan",
        json={"goal": scenario.goal, "sku": scenario.sku, "store_id": scenario.store_id},
    )
    assert response.status_code == HTTPStatus.OK
    plan = Plan.model_validate(response.json())
    actual_plan = plan.model_dump_json().lower()
    missing_mentions = [term for term in scenario.must_mention if term.lower() not in actual_plan]
    assert not missing_mentions, f"{scenario.id} plan is missing expected content: {missing_mentions}"

    started = [name for name, state in fleet.invoker.events if state == "started"]
    completed = [name for name, state in fleet.invoker.events if state == "completed"]
    assert set(scenario.must_call) <= set(started)
    assert set(scenario.forbid_call).isdisjoint(started)
    assert set(scenario.must_call) <= set(completed)

    positions = {(name, state): index for index, (name, state) in enumerate(fleet.invoker.events)}
    assert max(positions[("inventory", "started")], positions[("pricing", "started")]) < min(
        positions[("inventory", "completed")], positions[("pricing", "completed")]
    )
    assert positions[("supplier", "started")] > max(
        positions[("inventory", "completed")], positions[("pricing", "completed")]
    )
    assert positions[("logistics", "started")] > positions[("supplier", "completed")]


@pytest.mark.asyncio
async def test_live_runner_reports_http_failures_without_external_network() -> None:
    scenario = SCENARIOS[0]

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"detail": "agent run failed"})

    async with AsyncClient(transport=httpx.MockTransport(respond), base_url="http://local.test") as client:
        result = await run_one(client, scenario)

    assert result.passed is False
    assert result.http_status == HTTPStatus.BAD_GATEWAY
    assert "HTTP 502" in result.errors


@pytest.mark.asyncio
async def test_live_runner_reports_missing_mentions_and_latency() -> None:
    scenario = SCENARIOS[0]
    response_plan = Plan(
        sku=scenario.sku,
        store_id=scenario.store_id,
        stock_view="available stock checked",
        price_view="advisory price review",
        po_view="advisory supplier draft",
        shipping_view="shipping quote available",
        summary="A recovery plan was assembled.",
        next_actions=["Review findings."],
    )

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(HTTPStatus.OK, json=response_plan.model_dump())

    constrained = scenario.model_copy(update={"must_mention": ["not-present"], "max_latency_s": 1e-9})
    async with AsyncClient(transport=httpx.MockTransport(respond), base_url="http://local.test") as client:
        result = await run_one(client, constrained)

    assert result.passed is False
    assert result.missing_mentions == ["not-present"]
    assert result.latency_s > constrained.max_latency_s
    assert any("exceeded" in error for error in result.errors)
