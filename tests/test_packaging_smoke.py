from __future__ import annotations

from octopus.packaging_smoke import run_v2_dependency_smoke


def test_v2_packaging_dependencies_work_from_source() -> None:
    run_v2_dependency_smoke()
