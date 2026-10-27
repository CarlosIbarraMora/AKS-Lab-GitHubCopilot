"""GitHub Copilot SDK factory for the ZavaShop orchestrator agent."""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from agent_framework import AgentResponse
from agent_framework.github import GitHubCopilotAgent, GitHubCopilotOptions
from copilot.generated.rpc import PermissionDecisionApproveOnce
from copilot.session import PermissionRequestResult

from src.shared.copilot import build_copilot_client
from src.shared.settings import Settings

from .prompts import SYSTEM_PROMPT
from .tools import TOOLS

log = structlog.get_logger(__name__).bind(agent_name="orchestrator")


def _approve_all(_request: object, _context: dict[str, str]) -> PermissionRequestResult:
    """Approve a single Copilot tool invocation under the orchestrator's constrained tool set."""

    return PermissionDecisionApproveOnce()


@dataclass
class _AgentRunResult:
    output_text: str
    tool_calls: list[str]


def _observed_tool_calls(response: AgentResponse) -> list[str]:
    return [
        content.name
        for message in response.messages or ()
        for content in message.contents
        if content.type == "function_call" and content.name is not None
    ]


@dataclass(frozen=True)
class _RunnableAgent:
    """Adapter satisfying the shared make_app runnable protocol."""

    agent: GitHubCopilotAgent

    async def run(self, message: str, /) -> _AgentRunResult:
        response_stream = self.agent.run(message, stream=True)
        async for _update in response_stream:
            pass
        response = await response_stream.get_final_response()
        tool_calls = _observed_tool_calls(response)
        log.info("orchestrator.delegations_observed", tool_calls=tool_calls)
        return _AgentRunResult(output_text=response.text, tool_calls=tool_calls)


async def build_agent(settings: Settings) -> _RunnableAgent:
    default_options = GitHubCopilotOptions(
        model=settings.copilot_model,
        timeout=settings.copilot_timeout_seconds,
        on_permission_request=_approve_all,
    )
    agent = GitHubCopilotAgent(
        client=build_copilot_client(),
        instructions=SYSTEM_PROMPT,
        name="orchestrator",
        description="ZavaShop orchestrator for single-store stock-out response planning.",
        tools=list(TOOLS),
        default_options=default_options,
    )
    return _RunnableAgent(agent=agent)
