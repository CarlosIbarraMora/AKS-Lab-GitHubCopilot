"""GitHub Copilot SDK factory for the ZavaShop pricing specialist agent."""

from __future__ import annotations

from dataclasses import dataclass

from agent_framework.github import GitHubCopilotAgent, GitHubCopilotOptions
from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject
from copilot.session import PermissionRequestResult
from copilot.session_events import PermissionPromptRequestMcp, PermissionRequest

from src.shared.copilot import build_copilot_client
from src.shared.settings import Settings

from .prompts import SYSTEM_PROMPT
from .tools import TOOLS


def _approve_pricing_recommendations(request: PermissionRequest, _context: dict[str, str]) -> PermissionRequestResult:
    if isinstance(request, PermissionPromptRequestMcp):
        if request.server_name == "pricing" and request.tool_name == "recommend_price":
            return PermissionDecisionApproveOnce()
    return PermissionDecisionReject(feedback="Only pricing recommend_price operations are permitted.")


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
        "on_permission_request": _approve_pricing_recommendations,
        "mcp_servers": {
            "pricing": {
                "type": "http",
                "url": settings.pricing_mcp_url,
                "tools": ["recommend_price"],
                "timeout": int(settings.copilot_timeout_seconds * 1000),
            },
        },
    }
    agent = GitHubCopilotAgent(
        client=build_copilot_client(),
        instructions=SYSTEM_PROMPT,
        name="pricing",
        description="ZavaShop pricing specialist for demand-aware price recommendations.",
        tools=list(TOOLS),
        default_options=default_options,
    )
    return _RunnableAgent(agent=agent)
