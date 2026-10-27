"""GitHub Copilot SDK factory for the ZavaShop supplier specialist agent."""

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


def _approve_supplier_operations(request: PermissionRequest, _context: dict[str, str]) -> PermissionRequestResult:
    if isinstance(request, PermissionPromptRequestMcp):
        if request.server_name == "supplier" and request.tool_name in {"list_suppliers", "draft_po"}:
            return PermissionDecisionApproveOnce()
    return PermissionDecisionReject(feedback="Only supplier lookup and purchase-order drafting are permitted.")


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
        "on_permission_request": _approve_supplier_operations,
        "mcp_servers": {
            "supplier": {
                "type": "http",
                "url": settings.supplier_mcp_url,
                "tools": ["list_suppliers", "draft_po"],
                "timeout": int(settings.copilot_timeout_seconds * 1000),
            },
        },
    }
    agent = GitHubCopilotAgent(
        client=build_copilot_client(),
        instructions=SYSTEM_PROMPT,
        name="supplier",
        description="ZavaShop supplier specialist for replenishment and purchase-order drafts.",
        tools=list(TOOLS),
        default_options=default_options,
    )
    return _RunnableAgent(agent=agent)
