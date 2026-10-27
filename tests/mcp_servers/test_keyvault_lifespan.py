"""Startup hydration integration checks for the MCP server entrypoints."""

from __future__ import annotations

import ast
import importlib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
from azure.identity import aio as identity_aio
from azure.keyvault.secrets import aio as secrets_aio

from src.shared import server as shared_server
from src.shared.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
MCP_MODULES = (
    "src.mcp_servers.inventory.server",
    "src.mcp_servers.supplier.server",
    "src.mcp_servers.shipping.server",
    "src.mcp_servers.pricing.server",
)
AGENT_MODULES = (
    "src.agents.inventory.server",
    "src.agents.supplier.server",
    "src.agents.logistics.server",
    "src.agents.pricing.server",
    "src.agents.orchestrator.server",
)


@pytest.mark.asyncio
async def test_mcp_lifespans_do_not_use_azure_when_vault_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZAVA_KV_URL", raising=False)
    credential_error = AssertionError("credential must not be used without Key Vault")
    client_error = AssertionError("SecretClient must not be used without Key Vault")

    with (
        patch.object(identity_aio, "DefaultAzureCredential", side_effect=credential_error) as credential,
        patch.object(secrets_aio, "SecretClient", side_effect=client_error) as secret_client,
    ):
        for module_name in MCP_MODULES:
            module = importlib.import_module(module_name)
            async with module.mcp._mcp_server.lifespan(module.mcp._mcp_server):
                pass

    credential.assert_not_called()
    secret_client.assert_not_called()


@pytest.mark.asyncio
async def test_each_mcp_fastmcp_lifespan_invokes_shared_helper_before_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZAVA_KV_URL", "https://mock.vault.invalid")

    observed: list[str | None] = []

    async def fake_hydrate(vault_url: str | None) -> None:
        observed.append(vault_url)

    for module_name in MCP_MODULES:
        module = importlib.import_module(module_name)
        observed.clear()

        monkeypatch.setattr(module, "hydrate_key_vault", fake_hydrate)
        async with module.mcp._mcp_server.lifespan(module.mcp._mcp_server):
            assert observed == ["https://mock.vault.invalid"]


def test_all_five_agent_entrypoints_share_hydrating_server_lifecycle() -> None:
    for module_name in AGENT_MODULES:
        module_path = ROOT.joinpath(*module_name.split(".")).with_suffix(".py")
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        call_names = {
            node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "make_app" in call_names or "create_app" in call_names, module_name

    shared_source = Path(shared_server.__file__).read_text(encoding="utf-8")
    shared_tree = ast.parse(shared_source)
    assert any(
        isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "hydrate_key_vault"
        for node in ast.walk(shared_tree)
    )


@pytest.mark.asyncio
async def test_shared_agent_lifecycle_hydrates_before_building_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    async def fake_hydrate(vault_url: str | None) -> None:
        assert vault_url == "https://mock.vault.invalid"
        events.append("hydrated")

    @dataclass
    class FakeRunResult:
        output_text: str

    class FakeAgent:
        async def run(self, message: str, /) -> FakeRunResult:
            return FakeRunResult(output_text=message)

    async def fake_build(_settings: Settings) -> FakeAgent:
        events.append("agent_built")
        return FakeAgent()

    monkeypatch.setattr(shared_server, "hydrate_key_vault", fake_hydrate)
    app = shared_server.make_app(
        name="startup-test",
        build=fake_build,
        settings=Settings(kv_url="https://mock.vault.invalid"),
    )

    async with app.router.lifespan_context(app):
        assert events == ["hydrated", "agent_built"]


@pytest.mark.parametrize("module_name", MCP_MODULES)
def test_mcp_server_keeps_health_routes_and_tools_registered(module_name: str) -> None:
    module: ModuleType = importlib.import_module(module_name)
    route_paths = {route.path for route in module.mcp._custom_starlette_routes}

    assert "/healthz" in route_paths
    assert "/readyz" in route_paths
    assert module.mcp._tool_manager.list_tools()
