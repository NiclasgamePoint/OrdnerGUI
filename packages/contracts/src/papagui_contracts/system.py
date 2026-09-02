"""System discovery and capability negotiation contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from ._base import (
    ContractValidationError,
    JsonDto,
    JsonValue,
    mapping_get,
    require_int,
    require_mapping,
    require_string,
    string_tuple,
)


CONTRACT_VERSION = "0.4.2"


class Capability(str, Enum):
    COMPONENT_GENERATIONS = "component_generations"
    SOURCE_RELATIVE_PATHS = "source_relative_paths"
    CUSTOMER_REVISIONS = "customer_revisions"
    IDEMPOTENT_MUTATIONS = "idempotent_mutations"
    ADMIN_SESSIONS = "admin_sessions"
    OFFLINE_CUSTOMER_SYNC = "offline_customer_sync"


DEFAULT_CAPABILITIES = tuple(item.value for item in Capability)


@dataclass(frozen=True, slots=True)
class Capabilities(JsonDto):
    """Features and schema versions supported by a server.

    Feature names intentionally remain strings. This lets an older client retain
    and forward capabilities introduced by a newer server while callers can use
    :class:`Capability` for all names known to this contract version.
    """

    api_versions: tuple[str, ...] = ("2", "1")
    generation_schema_versions: tuple[int, ...] = (2, 1)
    features: tuple[str, ...] = DEFAULT_CAPABILITIES

    def __post_init__(self) -> None:
        api_versions = tuple(dict.fromkeys(string_tuple(self.api_versions, "api_versions")))
        if not isinstance(self.generation_schema_versions, (list, tuple)):
            raise ContractValidationError("generation_schema_versions must be an array of integers")
        schema_versions = tuple(
            dict.fromkeys(
                require_int(item, "generation_schema_versions", minimum=1)
                for item in self.generation_schema_versions
            )
        )
        features = tuple(dict.fromkeys(string_tuple(self.features, "features")))
        if not api_versions:
            raise ContractValidationError("api_versions must not be empty")
        if not schema_versions:
            raise ContractValidationError("generation_schema_versions must not be empty")
        object.__setattr__(self, "api_versions", api_versions)
        object.__setattr__(self, "generation_schema_versions", schema_versions)
        object.__setattr__(self, "features", features)

    def supports(self, capability: Capability | str) -> bool:
        value = capability.value if isinstance(capability, Capability) else capability
        return value in self.features

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "Capabilities":
        payload = require_mapping(payload, "capabilities")
        raw_features = mapping_get(payload, "features", None)
        if raw_features is None:
            # v1 advertised features as boolean keys.
            raw_features = [
                key
                for key, value in payload.items()
                if key not in {"api_versions", "generation_schema_versions"} and value is True
            ]
        raw_schemas = mapping_get(payload, "generation_schema_versions", (1,))
        if not isinstance(raw_schemas, (list, tuple)):
            raise ContractValidationError("generation_schema_versions must be an array of integers")
        return cls(
            api_versions=string_tuple(mapping_get(payload, "api_versions", ("1",)), "api_versions"),
            generation_schema_versions=tuple(
                require_int(item, "generation_schema_versions", minimum=1) for item in raw_schemas
            ),
            features=string_tuple(raw_features, "features"),
        )


@dataclass(frozen=True, slots=True)
class SystemInfo(JsonDto):
    server_version: str
    capabilities: Capabilities = field(default_factory=Capabilities)
    api_version: str = "2"
    contracts_version: str = CONTRACT_VERSION
    service_name: str = "papagui-server"

    def __post_init__(self) -> None:
        require_string(self.server_version, "server_version")
        require_string(self.api_version, "api_version")
        require_string(self.contracts_version, "contracts_version")
        require_string(self.service_name, "service_name")
        if not isinstance(self.capabilities, Capabilities):
            raise ContractValidationError("capabilities must be a Capabilities DTO")

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "SystemInfo":
        payload = require_mapping(payload, "system_info")
        raw_capabilities = mapping_get(payload, "capabilities", {})
        version = mapping_get(
            payload,
            "server_version",
            mapping_get(payload, "version", "unknown"),
        )
        return cls(
            server_version=require_string(version, "server_version"),
            capabilities=Capabilities.from_dict(require_mapping(raw_capabilities, "capabilities")),
            api_version=require_string(mapping_get(payload, "api_version", "1"), "api_version"),
            contracts_version=require_string(
                mapping_get(payload, "contracts_version", "0.4.1"),
                "contracts_version",
            ),
            service_name=require_string(
                mapping_get(payload, "service_name", "papagui-index"),
                "service_name",
            ),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "server_version": self.server_version,
            "api_version": self.api_version,
            "contracts_version": self.contracts_version,
            "service_name": self.service_name,
            "capabilities": self.capabilities.to_dict(),
        }


SystemCapabilities = Capabilities
