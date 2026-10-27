"""GitHub Copilot SDK factory for the ZavaShop orchestrator agent."""

from __future__ import annotations

from dataclasses import dataclass

from agent_framework.github import GitHubCopilotAgent, GitHubCopilotOptions
from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject
from copilot.session import PermissionRequestResult
from copilot.session_events import PermissionPromptRequestCustomTool, PermissionRequest

from src.shared.copilot import build_copilot_client
from src.shared.settings import Settings

from .prompts import SYSTEM_PROMPT
from .tools import TOOLS

_DELEGATION_TOOLS = {"ask_inventory", "ask_supplier", "ask_logistics", "ask_pricing"}


def _approve_delegation_tools(request: PermissionRequest, _context: dict[str, str]) -> PermissionRequestResult:
    if isinstance(request, PermissionPromptRequestCustomTool) and request.tool_name in _DELEGATION_TOOLS:
        return PermissionDecisionApproveOnce()
    return PermissionDecisionReject(feedback="Only the four specialist delegation tools are permitted.")


@dataclass
class _AgentRunResult:
    output_text: str


@dataclass(frozen=True)
class _RunnableAgent:
    """Adapter satisfying the shared make_app runnable protocol."""

    agent: GitHubCopilotAgent

    async def run(self, message: str, /) -> _AgentRunResult:
        response = await self.agent.run(message)
        return _AgentRunResult(output_text=response.text)


async def build_agent(settings: Settings) -> _RunnableAgent:
    default_options: GitHubCopilotOptions = {
        "model": settings.copilot_model,
        "timeout": settings.copilot_timeout_seconds,
        "on_permission_request": _approve_delegation_tools,
    }
    agent = GitHubCopilotAgent(
        client=build_copilot_client(),
        instructions=SYSTEM_PROMPT,
        name="orchestrator",
        description="ZavaShop orchestrator for single-store stock-out response planning.",
        tools=list(TOOLS),
        default_options=default_options,
    )
    return _RunnableAgent(agent=agent)
