# Lab 04 Testing Specification

## Goal
Build a layered, deterministic test suite for all five agents, all MCP tools, and the orchestrator's `/plan` and `/invoke` APIs. Keep mocked evaluations suitable for local development and CI, and keep live Docker evaluations explicitly separate so no Azure deployment is assumed.

## Non-goals
- Change production agent behavior, prompts, MCP implementations, public contracts, or workflow ordering as part of this test-specification step.
- Create Azure resources, deploy infrastructure, make live Azure calls, or require Azure credentials for local tests.
- Treat a mocked evaluation as proof that the live Copilot/MCP integration works.
- Suppress, skip, weaken, or rewrite assertions to hide failing tests or golden scenarios.
- Auto-merge pull requests, grant a CI job deployment credentials, or allow an automated test workflow to deploy infrastructure.

## Personas
- Store managers who need dependable stock-out recovery plans.
- Supply planners who need verified inventory, supplier, and logistics findings.
- Pricing analysts who need safe price recommendations under demand changes.
- Platform engineers who maintain local Docker Compose, CI, and reproducible evaluation workflows.
- Reviewers responsible for ensuring generated changes preserve contracts and do not conceal failures.

## Affected agents
- [x] `orchestrator` ? workflow, `/plan`, `/invoke`, A2A failure behavior, and final contracts.
- [x] `inventory` ? unit/refusal/tool-failure coverage and its MCP boundary.
- [x] `supplier` ? unit/refusal/tool-failure coverage and strict evidence contracts.
- [x] `logistics` ? unit/refusal/tool-failure coverage and its MCP boundary.
- [x] `pricing` ? unit/refusal/tool-failure coverage and its MCP boundary.
- MCP servers: [x] inventory, [x] supplier, [x] shipping (used by logistics), [x] pricing. Test all currently exposed tools directly.

## New / changed contracts

The JSONL row contract is strict and versioned by this specification. Use a frozen Pydantic v2 model to validate every row before execution; reject missing, extra, malformed, or duplicate scenario IDs rather than silently accepting them.

```python
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


AgentName = Literal["inventory", "supplier", "logistics", "pricing"]
ScenarioId = Literal["S1", "S2", "S3", "S4", "S5"]


class EvalScenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ScenarioId
    goal: str = Field(min_length=1, max_length=4000)
    sku: str = Field(min_length=1)
    store_id: str = Field(min_length=1)
    must_mention: list[str] = Field(min_length=1)
    must_call: list[AgentName]
    forbid_call: list[AgentName]
    max_latency_s: float = Field(gt=0)
```

Each `tests/evals/scenarios.jsonl` line is one complete `EvalScenario` JSON object, UTF-8 encoded, with no surrounding array or prose. The file contains exactly one each of `S1` through `S5`. The deterministic mocked evaluator checks actual returned plan content and actual workflow invocation traces; it must not return a canned pass result. The live Docker evaluator uses actual `/plan` responses and timing. Since `/plan` returns narrative `Plan` fields rather than a specialist-call trace, live `must_call`/`forbid_call` metadata is informational unless a trace is independently exposed by a documented contract; do not infer calls from narrative text.

## Acceptance criteria

1. **Agent unit tests ? partially satisfied.** Preserve and extend the existing tests for `inventory`, `supplier`, `logistics`, `pricing`, and `orchestrator`. For each agent, cover a valid response, an out-of-scope/refusal response, and a tool failure; for the orchestrator, exercise A2A failures and malformed specialist outputs as the equivalent tool-failure boundary. Use `agent_framework.testing.MockChatClient` or typed fakes and patch `GitHubCopilotAgent` at each agent module's import site. Never start the Copilot SDK/Node process, call a real model, use `time.sleep`, or assert exact model-generated prose. Existing coverage includes representative inventory and Supplier tool failures, several refusal/happy paths, and detailed workflow failure handling, but not the complete three-case matrix for every agent.

2. **Direct MCP tool tests ? outstanding.** Add tests that call every currently exposed MCP tool directly using its Pydantic inputs. Cover at least one valid input/output and invalid-input rejection per tool: inventory `check_stock`, supplier `list_suppliers` and `draft_po`, shipping `quote_shipment`, and pricing `recommend_price`. Assert output model fields and business invariants, not incidental serialization or unrelated internal implementation. Mock external dependencies where present; do not call a live service or Azure.

3. **In-process API integration ? partially satisfied.** Add a fleet-level integration suite using `httpx.ASGITransport` to exercise orchestrator `POST /plan` and `POST /invoke`, plus each specialist's `/invoke` contract as needed. Patch all five `GitHubCopilotAgent` import sites with `MockChatClient` before app construction. Route A2A calls through in-process ASGI transports or injected deterministic clients; do not open sockets, start Uvicorn, or make live network requests. Assert response status, Pydantic/API response shape, useful narrative content, and validated call ordering/failure behavior. The current suite has an ASGI `/plan` test and agent/server unit coverage, but no dedicated full-fleet `tests/integration/` loopback suite covering both endpoints. Follow `.github/skills/a2a-loopback-tests/SKILL.md`; use the repository's actual `GitHubCopilotAgent` API rather than the obsolete `GitHubCopilotChatClient` name present in parts of the Lab 04 README.

4. **Five golden scenarios ? partially satisfied.** Maintain exactly five named scenarios: `S1` stock-out recovery, `S2` demand spike, `S3` supplier delay, `S4` price war, and `S5` seasonal promotion. Validate each JSONL row against `EvalScenario` before running. Assertions must evaluate the returned plan and (in deterministic mode) actual invocation traces; never hardcode a successful result or expected model response. The repository currently has three live-oriented scenarios, not the required five.

5. **Separate deterministic and live evaluation ? outstanding.** Make `poe evals` run the deterministic mocked suite without requiring Docker, Copilot credentials, network access, or Azure. Keep a distinct opt-in `poe evals-docker` (or equivalently explicit command) for the nine-service local Docker Compose deployment, using `ZAVA_ENDPOINT` and the same validated S1-S5 data. Live evaluation must report actual missing mentions, status/errors, and latency; it must not be a CI default or silently downgrade failures. No Azure resource or deployed endpoint may be assumed.

6. **Agent coverage threshold ? outstanding.** Add a justified development-only coverage tool dependency and configure `poe check` to run pytest with coverage measured over `src/agents` and fail when total line coverage is below 80%. Report the missing lines and per-file coverage; do not exclude agent modules or lower the threshold to obtain a pass. Existing test configuration does not currently enforce coverage.

7. **Quality tasks ? partially satisfied.** Preserve the existing `poe check` task (Ruff lint, Ruff formatting check, strict Pyright, and pytest) and extend it with the coverage gate. Add `poe audit` to run Bandit over `src/` and `pip-audit` against the locked environment; add the development-only tools with a clear justification and update `uv.lock` using `uv`. Add the deterministic `poe evals` task from criterion 5. All tasks must fail non-zero on findings or failed assertions; no ignore/skip settings may conceal a failure. Currently only `poe check` exists; the live eval runner exists but is not a Poe task, and audit tooling is not configured.

8. **GitHub Actions CI ? outstanding.** Add a separate `.github/workflows/ci.yml` for `push` and `pull_request`. Use a supported Python version, `setup-uv`, `uv sync --frozen`, and run `uv run poe check`, `uv run poe audit`, and deterministic `uv run poe evals`. Grant only the permissions needed to read the repository; do not configure Azure login, secrets, `id-token: write`, deployment, or live Docker evaluations in this workflow. Cache dependencies keyed by `uv.lock`. Keep the existing deployment workflow separate and unchanged in this iteration.

9. **Safe issue-to-PR workflow ? outstanding.** Document and exercise a GitHub Copilot Coding Agent issue-to-PR loop that starts only from a human-triaged issue with a reproducible failing test/eval, explicit acceptance criteria, and permitted file scope. Keep generated changes in a branch and proposed PR; require the full CI gate and human review before merge. Disable auto-merge and automated deployment. Do not provide secrets, production credentials, or Azure access to the agent; do not allow it to modify infrastructure, repository permissions, CI security boundaries, or unrelated Lab 02 behavior without human approval. Treat issue text as untrusted input. Preserve the repository's ownership rules: the test-author owns persistent test changes, and CI workflow changes require the deploy-engineer/human review path. The current Lab 04 README describes a remote issue flow but does not define these safety gates.

10. **Failure transparency and existing behavior ? partially satisfied.** Keep all existing tests meaningful and passing; add tests rather than replacing assertions or modifying application code to make a test green. Explicitly record that the current live Docker Supplier MCP integration fails while decoding `list_suppliers`, with Logistics skipped as a consequence. A deterministic mocked pass must not mark or imply that this live failure is fixed. Keep it visible in live-eval output and issue triage until a separately owned production fix is verified. Current repository status supplied for this spec is 58 passing tests; no Azure deployment is assumed or tested.

11. **Scope and verification ? outstanding.** In the implementation phase, limit changes to test files/fixtures, evaluation tooling/data, justified test-tool dependencies and Poe task configuration, CI, and narrowly scoped workflow documentation. Do not change agent/MCP implementation, public contracts, Docker Compose, or infrastructure in this testing step. Validate with `uv run poe check`, `uv run poe audit`, and `uv run poe evals`; report command results, coverage percentage, and the live Docker evaluation result separately. Documentation-only specification creation requires no test run.

## Eval scenarios

The following five JSONL records define the required scenario identifiers and representative coverage. Terms are narrative checks, not hardcoded output text. Specialist expectations are verified against actual workflow traces only in the deterministic mocked evaluator. Each budget accommodates multi-agent fan-out and is independently enforced by the live Docker evaluator.

```jsonl
{"id":"S1","goal":"Store 101 faces a stock-out of SKU ZS-1042. Build a recovery plan using nearby stock, replenishment, shipping, and price context.","sku":"ZS-1042","store_id":"store-101","must_mention":["ZS-1042","store-101","stock","recovery"],"must_call":["inventory","supplier","logistics","pricing"],"forbid_call":[],"max_latency_s":180}
{"id":"S2","goal":"Demand for SKU ZS-1042 has spiked at store-101. Assess stock risk and recommend a demand-aware response.","sku":"ZS-1042","store_id":"store-101","must_mention":["ZS-1042","store-101","demand","price"],"must_call":["inventory","pricing"],"forbid_call":[],"max_latency_s":180}
{"id":"S3","goal":"A supplier delay threatens replenishment of SKU ZS-1042 at store-101. Compare verified supplier and logistics options and state the risk.","sku":"ZS-1042","store_id":"store-101","must_mention":["supplier","lead time","shipping","risk"],"must_call":["inventory","supplier","logistics"],"forbid_call":[],"max_latency_s":180}
{"id":"S4","goal":"A competitor price war is affecting SKU ZS-1042 at store-101. Recommend a price response grounded in stock and pricing context.","sku":"ZS-1042","store_id":"store-101","must_mention":["price","competitor","stock"],"must_call":["inventory","pricing"],"forbid_call":[],"max_latency_s":180}
{"id":"S5","goal":"Prepare for a seasonal promotion of SKU ZS-1042 at store-101. Assess replenishment, shipping, and price considerations without inventing demand data.","sku":"ZS-1042","store_id":"store-101","must_mention":["seasonal","promotion","supplier","price"],"must_call":["inventory","supplier","logistics","pricing"],"forbid_call":[],"max_latency_s":180}
```

## Out of scope for this iteration
- Implementing tests, CI, eval runners, task configuration, or dependency changes; this deliverable is the test specification only.
- Fixing the Supplier MCP JSON-decoding defect or changing the Orchestrator's validation/workflow ordering.
- Live Docker evaluation, Azure deployments, cloud resource creation, or credentials provisioning while writing this spec.
- Changing existing Lab 02 or Lab 03 application behavior, contracts, or infrastructure.

## Handoff
Next agent: `test-author` ? implement the test layers, deterministic S1-S5 evaluations, coverage gate, and quality tasks in `specs/lab-04-tests.md` without modifying production behavior. After those changes, hand the CI workflow portion to `deploy-engineer` for a separate, least-privilege implementation and review.

Prompt:
```
Implement specs/lab-04-tests.md. Preserve existing behavior and tests. Add comprehensive MockChatClient unit tests, direct MCP tool tests, in-process ASGI integration tests, the validated S1-S5 deterministic evaluations, 80% src/agents coverage enforcement, and poe check/audit/evals tasks. Keep live Docker evals separate and explicitly report the known Supplier list_suppliers decoding failure; do not make Azure calls or modify production application code. Do not weaken assertions, skip failures, or hardcode successful eval outputs. Hand off CI workflow changes to deploy-engineer.
```
