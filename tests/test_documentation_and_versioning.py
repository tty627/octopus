from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from packaging.version import Version

from octopus import __version__
from octopus.models import ExtractedDocument, IndexingInfo, RunReport, SchemaInfo, utc_now

ROOT = Path(__file__).resolve().parents[1]
RELEASES = [*(f"v0.{minor}" for minor in range(1, 10)), "v1.0"]
REQUIRED_HEADINGS = {
    "## 状态",
    "## 目标用户",
    "## 核心问题",
    "## 用户价值",
    "## 范围",
    "## 非目标",
    "## 交付物",
    "## 指标",
    "## 验收清单",
    "## 依赖",
    "## 风险",
    "## 迁移要求",
    "## 退出条件",
}
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\((<[^>]+>|[^)]+)\)")
IGNORED_DOCUMENTATION_DIRS = {".git", ".pytest_cache", ".venv", ".octopus-dev", "dist"}


def repository_markdown_files() -> list[Path]:
    documents: list[Path] = []
    for directory, child_directories, filenames in os.walk(ROOT):
        child_directories[:] = [
            name for name in child_directories if name not in IGNORED_DOCUMENTATION_DIRS
        ]
        documents.extend(
            Path(directory) / filename for filename in filenames if filename.endswith(".md")
        )
    return documents


def test_every_planned_release_has_the_governance_sections() -> None:
    for version in RELEASES:
        path = ROOT / "docs" / "releases" / f"{version}.md"
        assert path.exists(), version
        lines = path.read_text(encoding="utf-8").splitlines()
        headings = {line.strip() for line in lines if line.startswith("## ")}
        assert headings >= REQUIRED_HEADINGS, (version, sorted(REQUIRED_HEADINGS - headings))


def test_internal_markdown_links_resolve() -> None:
    failures: list[str] = []
    for document in repository_markdown_files():
        for raw_target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
            target = raw_target.removeprefix("<").removesuffix(">").split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            resolved = (document.parent / target).resolve()
            if not resolved.exists():
                failures.append(f"{document.relative_to(ROOT)} -> {target}")
    assert failures == []


def test_product_version_has_one_source_and_schema_versions_remain_independent() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dynamic"] == ["version"]
    assert pyproject["tool"]["hatch"]["version"]["path"] == "src/octopus/__init__.py"
    assert str(Version(__version__)) == __version__
    assert IndexingInfo().generator_version == __version__
    assert ExtractedDocument(name="x", document_type="text").parser_version == __version__
    report = RunReport(
        run_id="version-test",
        repository_id="repository",
        started_at=utc_now(),
        finished_at=utc_now(),
        status="success",
    )
    assert report.version == __version__
    assert SchemaInfo().octopus_schema == "0.2"


def test_windows_numeric_versions_order_development_and_prereleases() -> None:
    script = ROOT / "packaging" / "write_version_info.py"
    spec = importlib.util.spec_from_file_location("octopus_write_version_info", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    values = [
        module.windows_numeric_version(value)
        for value in ("0.4.0.dev0", "0.4.0a1", "0.4.0b1", "0.4.0rc1", "0.4.0")
    ]
    numeric = [tuple(int(part) for part in value.split(".")) for value in values]
    assert numeric == sorted(numeric)
    result = subprocess.run(
        [sys.executable, str(script), "--print-numeric"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", result.stdout.strip())


def test_release_workflow_checks_versions_and_marks_prereleases() -> None:
    workflow = (ROOT / ".github" / "workflows" / "windows-package.yml").read_text(
        encoding="utf-8"
    )
    installer = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert "-ExpectedVersion $expectedVersion" in workflow
    assert 'releaseArguments += "--prerelease"' in workflow
    assert "validate_windows_install.ps1" in workflow
    assert "VersionInfoVersion={#AppNumericVersion}" in installer
