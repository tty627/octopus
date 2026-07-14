from __future__ import annotations

from packaging.version import Version

from octopus import __version__
from octopus.compatibility import (
    SUPPORTED_IN_PLACE_UPGRADE_SOURCES,
    compatibility_report,
    product_upgrade_compatibility,
)


def test_two_previous_beta_candidates_are_explicit_upgrade_and_rollback_sources() -> None:
    report = compatibility_report()

    assert report.product_version == __version__
    assert report.in_place_upgrade_sources == list(SUPPORTED_IN_PLACE_UPGRADE_SOURCES)
    assert len(report.upgrade_rehearsals) == 2
    assert all(item.supported for item in report.upgrade_rehearsals)
    assert all(item.binary_rollback_safe for item in report.upgrade_rehearsals)
    assert all(not item.persistent_migration_required for item in report.upgrade_rehearsals)
    assert all(
        Version(item.source_version) < Version(__version__)
        for item in report.upgrade_rehearsals
    )


def test_contract_matrix_covers_all_persistent_and_public_boundaries() -> None:
    contracts = {item.contract: item for item in compatibility_report().contracts}

    assert set(contracts) == {
        "global_config",
        "repository_config_and_state",
        "markdown_index",
        "local_api",
        "plugin_api",
        "search_cache",
    }
    assert contracts["plugin_api"].stability == "developer_preview"
    assert contracts["search_cache"].stability == "rebuildable"
    assert contracts["local_api"].current_version == "1.0"


def test_unsupported_or_future_product_versions_are_rejected() -> None:
    assert not product_upgrade_compatibility("0.5.0").supported
    assert not product_upgrade_compatibility("99.0.0").supported
    assert not product_upgrade_compatibility("not-a-version").supported
