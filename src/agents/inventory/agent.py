"""GitHub Copilot SDK factory for the ZavaShop inventory specialist agent."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import structlog
from agent_framework import AgentResponse
from agent_framework.github import GitHubCopilotAgent, GitHubCopilotOptions
from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject
from copilot.session import PermissionRequestResult
from copilot.session_events import PermissionRequest, PermissionRequestMcp

from src.shared.copilot import build_copilot_client
from src.shared.settings import Settings

from .prompts import SYSTEM_PROMPT
from .tools import TOOLS

log = structlog.get_logger(__name__).bind(agent_name="inventory")


def _approve_inventory_stock_check(
    request: PermissionRequest,
    _context: dict[str, str],
) -> PermissionRequestResult:
    if isinstance(request, PermissionRequestMcp):
        if request.server_name in {"inventory", "inventory-mcp"} and request.tool_name in {
            "check_stock",
            "inventory-check_stock",
        }:
            return PermissionDecisionApproveOnce()

    return PermissionDecisionReject(feedback="Only inventory check_stock is permitted.")


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
        return _AgentRunResult(output_text=response.text, tool_calls=_observed_tool_calls(response))


async def build_agent(settings: Settings) -> _RunnableAgent:
    mcp_url = urlsplit(settings.inventory_mcp_url)
    log.info(
        "inventory.mcp_configured",
        scheme=mcp_url.scheme,
        host=mcp_url.hostname,
        port=mcp_url.port,
    )

    default_options: GitHubCopilotOptions = {
        "model": settings.copilot_model,
        "timeout": settings.copilot_timeout_seconds,
        "on_permission_request": _approve_inventory_stock_check,
        "mcp_servers": {
            "inventory": {
                "type": "http",
                "url": settings.inventory_mcp_url,
                "tools": ["check_stock"],
                "timeout": int(settings.copilot_timeout_seconds * 1000),
            },
        },
    }
    agent = GitHubCopilotAgent(
        client=build_copilot_client(),
        instructions=SYSTEM_PROMPT,
        name="inventory",
        description="ZavaShop inventory specialist for stock-risk interpretation.",
        tools=list(TOOLS),
        default_options=default_options,
    )
    return _RunnableAgent(agent=agent)
