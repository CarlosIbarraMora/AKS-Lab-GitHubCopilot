"""Optional Azure Key Vault startup hydration for ZavaShop services."""

from __future__ import annotations

import os

import structlog

log = structlog.get_logger(__name__)

SECRET_ENVIRONMENT_MAP: dict[str, str] = {
    "GITHUB-TOKEN": "GITHUB_TOKEN",
    "INVENTORY-MCP-URL": "ZAVA_INVENTORY_MCP_URL",
    "SUPPLIER-MCP-URL": "ZAVA_SUPPLIER_MCP_URL",
    "SHIPPING-MCP-URL": "ZAVA_SHIPPING_MCP_URL",
    "PRICING-MCP-URL": "ZAVA_PRICING_MCP_URL",
}


async def hydrate_key_vault(vault_url: str | None) -> None:
    """Populate unset environment variables from Key Vault when a vault is configured.

    Args:
        vault_url: Key Vault URI, or None to disable cloud authentication and access.
    Returns:
        None. Azure failures are raised to fail service startup explicitly.
    """

    if not vault_url:
        log.info("keyvault.hydration.skipped", reason="vault_not_configured")
        return

    from azure.identity.aio import DefaultAzureCredential  # noqa: PLC0415
    from azure.keyvault.secrets.aio import SecretClient  # noqa: PLC0415

    credential = DefaultAzureCredential()
    try:
        async with SecretClient(vault_url=vault_url, credential=credential) as client:
            for secret_name, environment_name in SECRET_ENVIRONMENT_MAP.items():
                if environment_name in os.environ:
                    continue
                secret = await client.get_secret(secret_name)
                if secret.value is None:
                    raise RuntimeError(f"Key Vault secret {secret_name} has no value")
                os.environ[environment_name] = secret.value
                log.info("keyvault.secret.hydrated", secret_name=secret_name, environment_name=environment_name)
    finally:
        await credential.close()
