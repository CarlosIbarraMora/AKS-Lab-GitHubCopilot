"""Strict schema and loader for Lab 04 golden evaluation scenarios."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCENARIOS_PATH = Path(__file__).with_name("scenarios.jsonl")
EXPECTED_SCENARIO_IDS = ("S1", "S2", "S3", "S4", "S5")
AgentName = Literal["inventory", "supplier", "logistics", "pricing"]
ScenarioId = Literal["S1", "S2", "S3", "S4", "S5"]


class EvalScenario(BaseModel):
    """Validated inputs and measurable gates for one golden scenario."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    id: ScenarioId
    goal: str = Field(min_length=1, max_length=4000)
    sku: str = Field(min_length=1)
    store_id: str = Field(min_length=1)
    must_mention: list[str] = Field(min_length=1)
    must_call: list[AgentName]
    forbid_call: list[AgentName]
    max_latency_s: float = Field(gt=0)


def load_scenarios(path: Path = SCENARIOS_PATH) -> tuple[EvalScenario, ...]:
    """Load exactly one valid JSONL record for each required scenario ID."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"Unable to read eval scenarios from {path}") from exc
    if not lines:
        raise ValueError("Eval scenario JSONL is empty")

    scenarios: list[EvalScenario] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise ValueError(f"Blank JSONL record at line {line_number}")
        try:
            scenario = EvalScenario.model_validate_json(line)
        except ValidationError as exc:
            raise ValueError(f"Invalid eval scenario at line {line_number}") from exc
        if scenario.id in seen_ids:
            raise ValueError(f"Duplicate eval scenario id {scenario.id} at line {line_number}")
        seen_ids.add(scenario.id)
        scenarios.append(scenario)

    if len(scenarios) != len(EXPECTED_SCENARIO_IDS) or set(seen_ids) != set(EXPECTED_SCENARIO_IDS):
        raise ValueError("Eval JSONL must contain exactly one each of S1, S2, S3, S4, and S5")
    return tuple(scenarios)
