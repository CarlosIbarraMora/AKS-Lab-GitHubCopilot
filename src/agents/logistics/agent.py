"""GitHub Copilot SDK factory for the ZavaShop logistics specialist agent."""

from __future__ import annotations

from dataclasses import dataclass

from agent_framework import AgentResponse
from agent_framework.github import GitHubCopilotAgent, GitHubCopilotOptions
from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject
from copilot.session import PermissionRequestResult
from copilot.session_events import PermissionRequest, PermissionRequestMcp

from src.shared.copilot import build_copilot_client
from src.shared.settings import Settings

from .prompts import SYSTEM_PROMPT
from .tools import TOOLS


def _approve_shipping_quotes(request: PermissionRequest, _context: dict[str, str]) -> PermissionRequestResult:
    if isinstance(request, PermissionRequestMcp):
        if request.server_name in {"shipping", "shipping-mcp"} and request.tool_name in {
            "quote_shipment",
            "shipping-quote_shipment",
        }:
            return PermissionDecisionApproveOnce()
    return PermissionDecisionReject(feedback="Only shipping quote_shipment is permitted.")


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
    default_options: GitHubCopilotOptions = {
        "model": settings.copilot_model,
        "timeout": settings.copilot_timeout_seconds,
        "on_permission_request": _approve_shipping_quotes,
        "mcp_servers": {
            "shipping": {
                "type": "http",
                "url": settings.shipping_mcp_url,
                "tools": ["quote_shipment"],
                "timeout": int(settings.copilot_timeout_seconds * 1000),
            },
        },
    }
    agent = GitHubCopilotAgent(
        client=build_copilot_client(),
        instructions=SYSTEM_PROMPT,
        name="logistics",
        description="ZavaShop logistics specialist for shipment quoting and transfer guidance.",
        tools=list(TOOLS),
        default_options=default_options,
    )
    return _RunnableAgent(agent=agent)
