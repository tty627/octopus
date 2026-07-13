from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

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
    assert __version__ == "0.4.0.dev0"
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
