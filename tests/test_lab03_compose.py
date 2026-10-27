"""Static regression checks for the nine-service local Compose fleet."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SERVICES = {
    "inventory-mcp",
    "supplier-mcp",
    "shipping-mcp",
    "pricing-mcp",
    "inventory",
    "supplier",
    "logistics",
    "pricing",
    "orchestrator",
}


def test_compose_defines_nine_runtime_services_with_healthchecks_and_healthy_dependencies() -> None:
    config = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = config["services"]

    assert set(services) - {"zavashop-base"} == RUNTIME_SERVICES
    assert services["zavashop-base"]["profiles"] == ["build"]
    for name in RUNTIME_SERVICES:
        healthcheck = services[name]["healthcheck"]
        assert any("/healthz" in str(argument) for argument in healthcheck["test"]), name
        for dependency in services[name].get("depends_on", {}).values():
            assert dependency["condition"] == "service_healthy", name


def test_compose_keeps_local_auth_explicit_and_container_runtime_non_root() -> None:
    config = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = config["services"]

    for name in ("inventory", "supplier", "logistics", "pricing", "orchestrator"):
        environment = services[name]["environment"]
        assert environment["GITHUB_TOKEN"] == "${GITHUB_TOKEN:?set GITHUB_TOKEN in your shell}"
        assert environment["ZAVA_COPILOT_TIMEOUT_SECONDS"] == "120"
        assert "ZAVA_KV_URL" not in environment

    dockerfile = (ROOT / "src" / "Dockerfile.base").read_text(encoding="utf-8")
    assert "HOME=/home/zava" in dockerfile
    assert "XDG_CACHE_HOME=/home/zava/.cache" in dockerfile
    assert "--uid 10001" in dockerfile
    assert "--home /home/zava" in dockerfile
    assert "chown -R 10001:10001 /home/zava /app" in dockerfile
    assert "USER 10001:10001" in dockerfile
