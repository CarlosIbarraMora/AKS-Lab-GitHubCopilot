"""Unit tests for the ZavaShop orchestrator agent."""

from __future__ import annotations

import pytest

from src.agents.orchestrator.agent import build_agent
from src.shared.settings import Settings
from tests.agents._mock import MockChatClient, captured_options


async def test_orchestrator_agent_uses_copilot_with_a2a_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    MockChatClient.reset(responses=['{"summary":"ok"}'])
    monkeypatch.setattr("src.agents.orchestrator.agent.GitHubCopilotAgent", MockChatClient)

    agent = await build_agent(Settings())
    response = await agent.run("Build a plan for SKU ZS-1042 at store-101.")
    options = captured_options()

    assert "summary" in response.output_text
    assert "gpt-5.5" == options.get("model")
    assert "mcp_servers" not in options
    assert MockChatClient.messages == ["Build a plan for SKU ZS-1042 at store-101."]


async def test_orchestrator_agent_refuses_out_of_scope_goal(monkeypatch: pytest.MonkeyPatch) -> None:
    MockChatClient.reset(responses=['{"refuse":"medical advice is outside supply-chain planning"}'])
    monkeypatch.setattr("src.agents.orchestrator.agent.GitHubCopilotAgent", MockChatClient)

    agent = await build_agent(Settings())
    result = await agent.run("Diagnose a medical condition for a store employee.")

    assert "refuse" in result.output_text
    assert "outside" in result.output_text


async def test_orchestrator_agent_propagates_tool_invocation_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    MockChatClient.reset(errors=[RuntimeError("orchestrator tool invocation failed")])
    monkeypatch.setattr("src.agents.orchestrator.agent.GitHubCopilotAgent", MockChatClient)

    agent = await build_agent(Settings())

    with pytest.raises(RuntimeError, match="orchestrator tool invocation failed"):
        await agent.run("Build a plan for SKU ZS-1042 at store-101.")

    assert MockChatClient.messages == ["Build a plan for SKU ZS-1042 at store-101."]
