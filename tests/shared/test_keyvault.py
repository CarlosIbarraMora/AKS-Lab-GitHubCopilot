"""Regression tests for optional asynchronous Key Vault hydration."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from azure.identity import aio as identity_aio
from azure.keyvault.secrets import aio as secrets_aio

from src.shared import keyvault as keyvault_module
from src.shared.keyvault import SECRET_ENVIRONMENT_MAP, hydrate_key_vault


class FakeCredential:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeSecretClient:
    def __init__(self, *, vault_url: str, credential: FakeCredential, fail_on_read: bool = False) -> None:
        assert vault_url == "https://mock.vault.invalid"
        assert credential is not None
        self.closed = False
        self.fail_on_read = fail_on_read

    async def __aenter__(self) -> FakeSecretClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.closed = True

    async def get_secret(self, secret_name: str) -> SimpleNamespace:
        if self.fail_on_read:
            raise RuntimeError("simulated Key Vault retrieval failure")
        return SimpleNamespace(value=f"mock-value-for-{secret_name.lower()}")


@pytest.mark.asyncio
async def test_unset_vault_url_does_not_create_azure_credentials_or_clients() -> None:
    credential_error = AssertionError("credential must not be used")
    client_error = AssertionError("client must not be used")
    with (
        patch.object(identity_aio, "DefaultAzureCredential", side_effect=credential_error) as credential,
        patch.object(secrets_aio, "SecretClient", side_effect=client_error) as client,
    ):
        await hydrate_key_vault(None)
        await hydrate_key_vault("")

    credential.assert_not_called()
    client.assert_not_called()


@pytest.mark.asyncio
async def test_secret_mapping_preserves_existing_environment_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for environment_name in SECRET_ENVIRONMENT_MAP.values():
        monkeypatch.delenv(environment_name, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "existing-token-value")
    monkeypatch.setenv("ZAVA_SHIPPING_MCP_URL", "http://existing-shipping-mcp/mcp")

    credential = FakeCredential()
    clients: list[FakeSecretClient] = []

    def make_client(*, vault_url: str, credential: FakeCredential) -> FakeSecretClient:
        client = FakeSecretClient(vault_url=vault_url, credential=credential)
        clients.append(client)
        return client

    with (
        patch.object(identity_aio, "DefaultAzureCredential", return_value=credential),
        patch.object(secrets_aio, "SecretClient", side_effect=make_client),
        patch.object(keyvault_module.log, "info") as info_log,
    ):
        await hydrate_key_vault("https://mock.vault.invalid")

    assert os.environ["GITHUB_TOKEN"] == "existing-token-value"
    assert os.environ["ZAVA_SHIPPING_MCP_URL"] == "http://existing-shipping-mcp/mcp"
    for secret_name, environment_name in SECRET_ENVIRONMENT_MAP.items():
        if environment_name not in {"GITHUB_TOKEN", "ZAVA_SHIPPING_MCP_URL"}:
            assert os.environ[environment_name] == f"mock-value-for-{secret_name.lower()}"
    assert all(
        f"mock-value-for-{secret_name.lower()}" not in str(info_log.call_args_list)
        for secret_name in SECRET_ENVIRONMENT_MAP
    )
    assert credential.closed
    assert clients and clients[0].closed


@pytest.mark.asyncio
async def test_keyvault_retrieval_error_is_surfaced_and_resources_are_closed() -> None:
    credential = FakeCredential()
    clients: list[FakeSecretClient] = []

    def make_client(*, vault_url: str, credential: FakeCredential) -> FakeSecretClient:
        client = FakeSecretClient(vault_url=vault_url, credential=credential, fail_on_read=True)
        clients.append(client)
        return client

    with (
        patch.object(identity_aio, "DefaultAzureCredential", return_value=credential),
        patch.object(secrets_aio, "SecretClient", side_effect=make_client),
        pytest.raises(RuntimeError, match="retrieval failure"),
    ):
        await hydrate_key_vault("https://mock.vault.invalid")

    assert credential.closed
    assert clients and clients[0].closed
