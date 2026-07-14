from __future__ import annotations

from typing import Literal

from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .api import API_CONTRACT_VERSION
from .migrations import GLOBAL_SCHEMA_VERSION, REPOSITORY_SCHEMA_VERSION
from .models import SchemaInfo
from .plugin_sdk import PLUGIN_API_VERSION
from .search import SEARCH_SCHEMA_VERSION

COMPATIBILITY_REPORT_SCHEMA_VERSION = "1.0"
SUPPORTED_IN_PLACE_UPGRADE_SOURCES = ("0.6.0a1", "0.7.0a1")


class ContractCompatibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: str
    current_version: str
    stability: Literal["stable", "developer_preview", "rebuildable"]
    rollback_policy: str


class ProductUpgradeCompatibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_version: str
    target_version: str = __version__
    supported: bool
    persistent_migration_required: bool
    binary_rollback_safe: bool
    reason: str


class CompatibilityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = COMPATIBILITY_REPORT_SCHEMA_VERSION
    product_version: str = __version__
    supported_platforms: list[str] = Field(default_factory=lambda: ["Windows 11 x64"])
    in_place_upgrade_sources: list[str]
    contracts: list[ContractCompatibility]
    upgrade_rehearsals: list[ProductUpgradeCompatibility]


def product_upgrade_compatibility(source_version: str) -> ProductUpgradeCompatibility:
    try:
        source = Version(source_version)
        target = Version(__version__)
    except ValueError:
        return ProductUpgradeCompatibility(
            source_version=source_version,
            supported=False,
            persistent_migration_required=False,
            binary_rollback_safe=False,
            reason="invalid_product_version",
        )
    if source_version not in SUPPORTED_IN_PLACE_UPGRADE_SOURCES or source >= target:
        return ProductUpgradeCompatibility(
            source_version=source_version,
            supported=False,
            persistent_migration_required=False,
            binary_rollback_safe=False,
            reason="source_version_not_in_supported_beta_window",
        )
    return ProductUpgradeCompatibility(
        source_version=source_version,
        supported=True,
        persistent_migration_required=False,
        binary_rollback_safe=True,
        reason="persistent_contracts_unchanged",
    )


def compatibility_report() -> CompatibilityReport:
    contracts = [
        ContractCompatibility(
            contract="global_config",
            current_version=GLOBAL_SCHEMA_VERSION,
            stability="stable",
            rollback_policy="checksum backup and explicit rollback",
        ),
        ContractCompatibility(
            contract="repository_config_and_state",
            current_version=REPOSITORY_SCHEMA_VERSION,
            stability="stable",
            rollback_policy="reject newer schemas before write",
        ),
        ContractCompatibility(
            contract="markdown_index",
            current_version=SchemaInfo().octopus_schema,
            stability="stable",
            rollback_policy="same-major forward compatibility from v1.0",
        ),
        ContractCompatibility(
            contract="local_api",
            current_version=API_CONTRACT_VERSION,
            stability="stable",
            rollback_policy="additive v1 evolution",
        ),
        ContractCompatibility(
            contract="plugin_api",
            current_version=PLUGIN_API_VERSION,
            stability="developer_preview",
            rollback_policy="incompatible ranges are disabled before execution",
        ),
        ContractCompatibility(
            contract="search_cache",
            current_version=SEARCH_SCHEMA_VERSION,
            stability="rebuildable",
            rollback_policy="discard and rebuild from committed index",
        ),
    ]
    rehearsals = [
        product_upgrade_compatibility(version)
        for version in SUPPORTED_IN_PLACE_UPGRADE_SOURCES
    ]
    return CompatibilityReport(
        in_place_upgrade_sources=list(SUPPORTED_IN_PLACE_UPGRADE_SOURCES),
        contracts=contracts,
        upgrade_rehearsals=rehearsals,
    )
