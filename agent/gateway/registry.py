"""Registry and extension contract for concrete messaging gateways."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from agent.gateway.config import GatewayRuntimeConfig
from agent.gateway.contracts import GatewayAdapter


@dataclass(frozen=True)
class GatewayEnvField:
    """One environment variable managed by the gateway setup wizard."""

    key: str
    label: str
    description: str
    secret: bool = False
    required: bool = True
    prompt: bool = True


@dataclass(frozen=True)
class GatewayDefinition:
    """Human-facing metadata for one gateway integration."""

    id: str
    name: str
    description: str
    env_fields: tuple[GatewayEnvField, ...]
    run_hint: str


AccessPolicy = Callable[[str], bool]


class GatewayIntegration(Protocol):
    """Everything the host needs to configure and run one gateway."""

    definition: GatewayDefinition

    def dependency_error(self) -> str | None: ...
    def load_settings(self, *, project_root: Path | None = None) -> Any: ...
    def validate_settings(self, settings: Any) -> list[str]: ...
    def config_values(self, settings: Any) -> Mapping[str, str]: ...
    def save_settings(
        self, values: Mapping[str, str], *, project_root: Path | None = None,
    ) -> Path: ...
    def verify_settings(self, values: Mapping[str, str]) -> str | None: ...
    def summary(self, settings: Any) -> tuple[tuple[str, str], ...]: ...
    def runtime_config(self, settings: Any) -> GatewayRuntimeConfig: ...
    def access_policy(self, settings: Any) -> AccessPolicy: ...
    def create_adapter(self, settings: Any) -> GatewayAdapter: ...


from agent.gateway.integrations.telegram.integration import TELEGRAM_INTEGRATION

GATEWAY_INTEGRATIONS: tuple[GatewayIntegration, ...] = (TELEGRAM_INTEGRATION,)
INTEGRATIONS_BY_ID = {
    integration.definition.id: integration
    for integration in GATEWAY_INTEGRATIONS
}


def get_gateway_integration(gateway_id: str) -> GatewayIntegration | None:
    """Return a registered integration by stable id."""
    return INTEGRATIONS_BY_ID.get(gateway_id)
