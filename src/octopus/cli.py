from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
import webbrowser
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Protocol, cast

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .activation import export_activation_records, summarize_activation_exports
from .compatibility import compatibility_report
from .config import (
    create_repository,
    global_config_lock,
    load_global_config,
    load_repository_config,
    load_repository_state,
    resolve_repository,
    save_global_config,
)
from .credentials import CredentialStoreError, resolve_ai_api_key
from .diagnostics import (
    create_diagnostic_bundle,
    diagnostic_summary,
    prepare_diagnostic_share,
)
from .engine import UpdateEngine
from .evaluation import (
    StudyRecord,
    evaluate_retrieval,
    load_retrieval_tasks,
    study_assignments,
    study_record_json,
    summarize_study,
)
from .markmap import render_markmap
from .migrations import (
    apply_migrations,
    migration_report_markdown,
    migration_rollback_markdown,
    plan_migrations,
    rollback_migration,
)
from .plugin_sdk import (
    check_plugin_compatibility,
    discover_plugins,
    load_plugin_manifest,
    reference_plugins_directory,
    run_plugin,
)
from .prompts import PROMPT_VERSION
from .release_audit import audit_release
from .search import SearchIndex, search_report_markdown
from .search_evaluation import (
    default_search_evaluation_dataset_path,
    evaluate_search_dataset,
    load_search_evaluation_dataset,
)
from .service_control import (
    api_status,
    run_api_server,
    service_token_path,
    start_api_process,
    stop_api_process,
)
from .transactions import load_run_report
from .upgrade import check_for_upgrade
from .utils import atomic_write_json, atomic_write_text
from .validation import validate_repository
from .watcher import run_watch_loop, start_watch, stop_watch, watch_status


class _ReconfigureTextStream(Protocol):
    def __call__(self, *, encoding: str, errors: str) -> None: ...


def _configure_windows_utf8_output(*streams: object) -> None:
    if sys.platform != "win32":
        return
    for stream in streams or (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            cast(_ReconfigureTextStream, reconfigure)(encoding="utf-8", errors="replace")


_configure_windows_utf8_output()

app = typer.Typer(
    name="octopus",
    help="Local-first, link-centric file indexing CLI.",
    no_args_is_help=True,
)
repo_app = typer.Typer(help="Manage registered Octopus repositories.")
watch_app = typer.Typer(help="Manage the polling watcher.")
api_app = typer.Typer(help="Manage the authenticated loopback Local API.")
service_app = typer.Typer(help="Manage the optional Windows SCM service.")
upgrade_app = typer.Typer(help="Check for Octopus software updates.")
acceptance_app = typer.Typer(help="Export and summarize local anonymous acceptance records.")
evaluation_app = typer.Typer(help="Run retrieval evaluation and controlled user studies.")
plugin_app = typer.Typer(help="Inspect and run isolated Octopus developer-preview plugins.")
diagnostics_app = typer.Typer(help="Create and inspect local, content-free diagnostic bundles.")
app.add_typer(repo_app, name="repo")
app.add_typer(watch_app, name="watch")
app.add_typer(api_app, name="api")
app.add_typer(service_app, name="service")
app.add_typer(upgrade_app, name="upgrade")
app.add_typer(acceptance_app, name="acceptance")
app.add_typer(evaluation_app, name="evaluate")
app.add_typer(plugin_app, name="plugin")
app.add_typer(diagnostics_app, name="diagnostics")
console = Console()


RepositoryOption = Annotated[
    str | None,
    typer.Option("--repository", "-r", help="Index path, repository ID, or repository name."),
]


def _repository(value: str | None) -> Path:
    try:
        return resolve_repository(value)
    except (FileNotFoundError, ValueError) as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(2) from error


def _diagnostic_repositories(repository: str | None, all_repositories: bool) -> list[Path]:
    if all_repositories and repository is not None:
        raise ValueError("--all and --repository are mutually exclusive")
    if all_repositories:
        return [
            Path(item.index_repository_path)
            for item in load_global_config().repositories.values()
            if item.enabled
        ]
    return [_repository(repository)]


@diagnostics_app.command("create")
def diagnostics_create(
    output: Annotated[Path, typer.Option("--output", "-o")],
    repository: RepositoryOption = None,
    all_repositories: Annotated[bool, typer.Option("--all")] = False,
) -> None:
    """Create a local-only bundle without paths, queries, content, or credentials."""
    try:
        indexes = _diagnostic_repositories(repository, all_repositories)
        created = create_diagnostic_bundle(output, indexes)
    except (OSError, ValueError) as error:
        console.print(f"[red]Unable to create diagnostics:[/red] {error}")
        raise typer.Exit(2) from error
    console.print_json(
        json.dumps({"created": True, "file": created.name, "local_only": True})
    )


@diagnostics_app.command("inspect")
def diagnostics_inspect(bundle: Path) -> None:
    """Inspect only the safe counts and consent state of a local diagnostic bundle."""
    try:
        summary = diagnostic_summary(bundle)
    except (OSError, ValueError) as error:
        console.print(f"[red]Invalid diagnostic bundle:[/red] {error}")
        raise typer.Exit(2) from error
    console.print_json(json.dumps(summary, ensure_ascii=False))


@diagnostics_app.command("prepare-share")
def diagnostics_prepare_share(
    bundle: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    consent: Annotated[
        bool, typer.Option("--consent", help="Record explicit consent for manual sharing.")
    ] = False,
) -> None:
    """Add a consent receipt to a local copy; this command never uploads anything."""
    try:
        shared = prepare_diagnostic_share(bundle, output, consent=consent)
    except (OSError, PermissionError, ValueError) as error:
        console.print(f"[red]Unable to prepare diagnostic share:[/red] {error}")
        raise typer.Exit(2) from error
    console.print_json(
        json.dumps({"prepared": True, "file": shared.name, "uploaded": False})
    )


@plugin_app.command("list")
def plugin_list(
    directory: Annotated[Path | None, typer.Option("--directory")] = None,
) -> None:
    """List reference or explicitly located plugin manifests without executing them."""
    plugins = discover_plugins(directory or reference_plugins_directory())
    console.print_json(json.dumps(plugins, ensure_ascii=False))


@plugin_app.command("inspect")
def plugin_inspect(plugin: Path) -> None:
    """Validate a plugin manifest and report API compatibility and requested permissions."""
    try:
        _, manifest = load_plugin_manifest(plugin)
        compatibility = check_plugin_compatibility(manifest, set(manifest.permissions))
    except (OSError, ValueError) as error:
        console.print(f"[red]Invalid plugin:[/red] {error}")
        raise typer.Exit(2) from error
    console.print_json(
        json.dumps(
            {
                "manifest": manifest.model_dump(mode="json"),
                "compatibility": compatibility.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )
    )


@plugin_app.command("run")
def plugin_run(
    plugin: Path,
    export: Annotated[Path, typer.Option("--export", help="Authorized empty export directory.")],
    repository: RepositoryOption = None,
    query: Annotated[str, typer.Option("--query")] = "",
    grant: Annotated[list[str] | None, typer.Option("--grant")] = None,
    confirm: Annotated[
        list[str] | None, typer.Option("--confirm", help="Confirmed node ID.")
    ] = None,
) -> None:
    """Execute a compatible plugin with explicit least-privilege grants."""
    try:
        report = run_plugin(
            plugin,
            _repository(repository),
            export,
            granted_permissions=set(grant or []),
            query=query,
            confirmed_node_ids=set(confirm or []),
        )
    except (OSError, PermissionError, RuntimeError, ValueError) as error:
        console.print(f"[red]Plugin execution failed:[/red] {error}")
        raise typer.Exit(2) from error
    console.print_json(json.dumps(report.model_dump(mode="json"), ensure_ascii=False))


@app.command("version")
def version() -> None:
    """Print the installed Octopus version."""
    console.print(__version__)


@app.command("compatibility")
def compatibility_command() -> None:
    """Print the current platform, schema, API, plugin, and upgrade support matrix."""
    console.print_json(
        json.dumps(compatibility_report().model_dump(mode="json"), ensure_ascii=False)
    )


@app.command("release-audit")
def release_audit_command(
    expected_version: Annotated[str, typer.Option("--expected-version")] = __version__,
    artifacts: Annotated[Path | None, typer.Option("--artifacts")] = None,
) -> None:
    """Audit frozen contracts, blockers, documentation, versions, and optional artifacts."""
    try:
        report = audit_release(
            Path(__file__).resolve().parents[2],
            expected_version,
            artifact_directory=artifacts,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        console.print(f"[red]Release audit failed:[/red] {error}")
        raise typer.Exit(4) from error
    console.print_json(json.dumps(report.model_dump(mode="json"), ensure_ascii=False))
    if not report.engineering_passed:
        raise typer.Exit(4)


@acceptance_app.command("export")
def acceptance_export(
    output: Annotated[Path, typer.Option("--output", "-o")],
    product_version: Annotated[str, typer.Option("--version")] = __version__,
) -> None:
    """Export only the current candidate's anonymous onboarding records."""
    try:
        exported = export_activation_records(output, product_version=product_version)
    except (OSError, ValueError) as error:
        console.print(f"[red]Unable to export acceptance records:[/red] {error}")
        raise typer.Exit(2) from error
    console.print_json(json.dumps(exported.model_dump(mode="json"), ensure_ascii=False))


@acceptance_app.command("summarize")
def acceptance_summarize(
    records: Annotated[list[Path], typer.Option("--records", exists=True)],
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """Summarize files or directories of one candidate's exports without uploading them."""
    try:
        paths: list[Path] = []
        for path in records:
            paths.extend(sorted(path.glob("*.json")) if path.is_dir() else [path])
        summary = summarize_activation_exports(paths)
    except (OSError, ValueError) as error:
        console.print(f"[red]Unable to summarize acceptance records:[/red] {error}")
        raise typer.Exit(2) from error
    if output:
        atomic_write_json(output, summary.model_dump(mode="json"))
    console.print_json(json.dumps(summary.model_dump(mode="json"), ensure_ascii=False))


@upgrade_app.command("check")
def upgrade_check(
    output_format: Annotated[str, typer.Option("--format", help="table or json")] = "table",
) -> None:
    """Check GitHub for the latest stable Octopus release without downloading it."""
    result = check_for_upgrade(force=True)
    if output_format == "json":
        console.print_json(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
    elif output_format == "table":
        table = Table("Field", "Value")
        table.add_row("Status", result.status.value)
        table.add_row("Current", result.current_version)
        table.add_row("Latest", result.latest_version or "unavailable")
        table.add_row("Release", result.release_url or "-")
        table.add_row("Notes", result.release_notes or "-")
        table.add_row("Error", result.error_code or "-")
        console.print(table)
    else:
        console.print("[red]--format must be table or json[/red]")
        raise typer.Exit(2)


@app.command("init")
def initialize(
    raw: Annotated[Path, typer.Option("--raw", help="Raw Repository directory.")],
    index: Annotated[Path, typer.Option("--index", help="Index Repository directory.")],
    name: Annotated[str | None, typer.Option("--name", help="Repository display name.")] = None,
    build: Annotated[
        bool, typer.Option("--build/--no-build", help="Build the first index immediately.")
    ] = True,
) -> None:
    """Register a Raw/Index pair and optionally build the initial index."""
    try:
        config = create_repository(raw, index, name)
        console.print(f"[green]Initialized[/green] {config.repository.repository_name}")
        console.print(f"Raw:   {config.repository.raw_repository_path}")
        console.print(f"Index: {config.repository.index_repository_path}")
        if build:
            stats = UpdateEngine(Path(config.repository.index_repository_path)).run(force_path="*")
            console.print_json(json.dumps(asdict(stats), ensure_ascii=False))
    except Exception as error:
        console.print(f"[red]Initialization failed:[/red] {error}")
        raise typer.Exit(2) from error


@repo_app.command("list")
def repository_list() -> None:
    """List registered repositories."""
    config = load_global_config()
    table = Table("Active", "ID", "Name", "Index Repository")
    for repo_id, repository in config.repositories.items():
        table.add_row(
            "*" if repo_id == config.active_repository_id else "",
            repo_id,
            repository.name,
            repository.index_repository_path,
        )
    console.print(table)


@repo_app.command("use")
def repository_use(repository: str) -> None:
    """Select the active repository by ID, name, or Index path."""
    index = _repository(repository)
    local = load_repository_config(index)
    with global_config_lock():
        global_config = load_global_config()
        global_config.active_repository_id = local.repository.raw_repo_id
        save_global_config(global_config)
    console.print(f"[green]Active repository:[/green] {local.repository.repository_name}")


@repo_app.command("show")
def repository_show(repository: RepositoryOption = None) -> None:
    """Show repository configuration and current state summary."""
    index = _repository(repository)
    config = load_repository_config(index)
    state = load_repository_state(index, config)
    counts: dict[str, int] = {}
    for node in state.nodes.values():
        counts[node.state.value] = counts.get(node.state.value, 0) + 1
    console.print_json(
        json.dumps(
            {
                "repository": config.repository.model_dump(mode="json"),
                "scan": state.scan.model_dump(mode="json"),
                "states": counts,
                "queues": state.queues.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )
    )


@app.command("doctor")
def doctor(
    repository: RepositoryOption = None,
    output_format: Annotated[str, typer.Option("--format", help="table or json")] = "table",
) -> None:
    """Check runtime, parser, provider and repository prerequisites."""
    checks: list[tuple[str, bool, str]] = []
    checks.append(("Python 3.12+", sys.version_info >= (3, 12), sys.version.split()[0]))
    for module, label in [
        ("pypdf", "PDF text parser"),
        ("pypdfium2", "PDF renderer"),
        ("docx", "DOCX parser"),
        ("openpyxl", "XLSX parser"),
        ("pptx", "PPTX parser"),
        ("PIL", "Image parser"),
        ("rapidocr", "RapidOCR"),
        ("openai", "DeepSeek client"),
        ("fastapi", "Local API framework"),
        ("uvicorn", "Local API server"),
    ]:
        available = importlib.util.find_spec(module) is not None
        checks.append((label, available, module))
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    checks.append(("Markmap runtime", bool(npx), npx or "npx not found"))
    if sys.platform == "win32":
        checks.append(
            (
                "Windows service runtime",
                importlib.util.find_spec("win32serviceutil") is not None,
                "pywin32",
            )
        )
    if repository is not None or load_global_config().active_repository_id:
        try:
            index = resolve_repository(repository)
            config = load_repository_config(index)
            raw = Path(config.repository.raw_repository_path)
            checks.append(("Raw readable", os.access(raw, os.R_OK), str(raw)))
            checks.append(("Index writable", os.access(index, os.W_OK), str(index)))
            checks.append(
                (
                    "Prompt version",
                    config.ai_policy.prompt_version == PROMPT_VERSION,
                    config.ai_policy.prompt_version,
                )
            )
            try:
                credential = resolve_ai_api_key(
                    config.repository.raw_repo_id,
                    config.ai_policy.provider,
                )
                checks.append(
                    (
                        "AI API credential",
                        bool(credential.api_key),
                        credential.source if credential.api_key else "not configured",
                    )
                )
            except CredentialStoreError:
                checks.append(("AI API credential", False, "credential store unavailable"))
            cost_configured = config.ai_policy.max_estimated_cost_per_run is None or (
                config.ai_policy.input_cost_per_million is not None
                and config.ai_policy.output_cost_per_million is not None
            )
            checks.append(
                (
                    "AI cost limit",
                    cost_configured,
                    "disabled or priced" if cost_configured else "configure both token prices",
                )
            )
        except Exception as error:
            checks.append(("Repository", False, str(error)))
    if output_format == "json":
        console.print_json(
            json.dumps(
                [
                    {"check": label, "status": "ok" if okay else "warning", "detail": detail}
                    for label, okay, detail in checks
                ],
                ensure_ascii=False,
            )
        )
    elif output_format == "table":
        table = Table("Check", "Status", "Detail")
        for label, okay, detail in checks:
            table.add_row(label, "OK" if okay else "WARN", detail)
        console.print(table)
    else:
        console.print("[red]--format must be table or json[/red]")
        raise typer.Exit(2)
    if any(
        not okay
        for label, okay, _ in checks
        if label not in {"AI API credential", "Markmap runtime"}
    ):
        raise typer.Exit(1)


@app.command("update")
def update(
    repository: RepositoryOption = None,
    once: Annotated[bool, typer.Option("--once", help="Run one complete update.")] = False,
    scan_only: Annotated[bool, typer.Option("--scan-only")] = False,
    leaf_only: Annotated[bool, typer.Option("--leaf-only")] = False,
    foldernode_only: Annotated[bool, typer.Option("--foldernode-only")] = False,
    retry: Annotated[bool, typer.Option("--retry")] = False,
    force: Annotated[str | None, typer.Option("--force", metavar="PATH")] = None,
    explain: Annotated[str | None, typer.Option("--explain", metavar="PATH")] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Plan an update without writing Index or Raw.")
    ] = False,
    output_format: Annotated[str, typer.Option("--format", help="table or json")] = "table",
) -> None:
    """Scan and incrementally update an Index Repository."""
    index = _repository(repository)
    if leaf_only and foldernode_only:
        console.print("[red]--leaf-only and --foldernode-only are mutually exclusive[/red]")
        raise typer.Exit(2)
    if explain:
        config = load_repository_config(index)
        state = load_repository_state(index, config)
        normalized = explain.replace("\\", "/").strip("/")
        node = next(
            (item for item in state.nodes.values() if item.raw_relative_path == normalized), None
        )
        if not node:
            console.print(f"[yellow]No manifest node for {normalized}[/yellow]")
            raise typer.Exit(1)
        console.print_json(json.dumps(node.model_dump(mode="json"), ensure_ascii=False))
        return
    if dry_run:
        plan = UpdateEngine(index).plan(force_path=force)
        if output_format == "json":
            console.print_json(json.dumps(plan.model_dump(mode="json"), ensure_ascii=False))
        elif output_format == "table":
            table = Table("Dry-run field", "Value")
            for key, value in plan.model_dump(mode="json").items():
                display = ", ".join(value) if isinstance(value, list) else str(value)
                table.add_row(key, display)
            console.print(table)
        else:
            console.print("[red]--format must be table or json[/red]")
            raise typer.Exit(2)
        return
    try:
        stats = UpdateEngine(index).run(
            scan_only=scan_only,
            leaf_only=leaf_only,
            foldernode_only=foldernode_only,
            retry_only=retry,
            force_path=force,
        )
        console.print_json(json.dumps(asdict(stats), ensure_ascii=False))
    except Exception as error:
        console.print(f"[red]Update failed:[/red] {error}")
        raise typer.Exit(3) from error


@app.command("rebuild-search")
def rebuild_search(repository: RepositoryOption = None) -> None:
    """Rebuild the disposable SQLite FTS5 cache from Markdown indexes."""
    count = SearchIndex(_repository(repository)).rebuild()
    console.print(f"[green]Indexed {count} Markdown documents.[/green]")


@app.command("evaluate-search")
def evaluate_search(
    dataset: Annotated[
        Path | None,
        typer.Option("--dataset", help="Versioned search evaluation dataset."),
    ] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    workspace: Annotated[
        Path | None,
        typer.Option("--workspace", help="Empty directory to preserve generated repositories."),
    ] = None,
    enforce: Annotated[
        bool,
        typer.Option("--enforce", help="Exit non-zero when engineering thresholds fail."),
    ] = False,
) -> None:
    """Run the deterministic offline v0.5 retrieval and explanation gate."""
    dataset_path = dataset or default_search_evaluation_dataset_path()
    try:
        definition = load_search_evaluation_dataset(dataset_path)
        if workspace is not None:
            report = evaluate_search_dataset(definition, workspace)
        else:
            with tempfile.TemporaryDirectory(prefix="octopus-search-evaluation-") as temporary:
                report = evaluate_search_dataset(definition, Path(temporary) / "workspace")
    except (OSError, RuntimeError, ValueError) as error:
        console.print(f"[red]Search evaluation failed:[/red] {error}")
        raise typer.Exit(2) from error
    payload = report.model_dump(mode="json")
    if output:
        atomic_write_json(output, payload)
    console.print_json(json.dumps(payload, ensure_ascii=False))
    if enforce and not report.meets_engineering_thresholds:
        raise typer.Exit(1)


@app.command("validate")
def validate_command(
    repository: RepositoryOption = None,
    output_format: Annotated[str, typer.Option("--format", help="table or json")] = "table",
) -> None:
    """Read-only validation of Index, Manifest, links and search cache."""
    report = validate_repository(_repository(repository))
    if output_format == "json":
        console.print_json(json.dumps(report.model_dump(mode="json"), ensure_ascii=False))
    elif output_format == "table":
        table = Table("Severity", "Code", "Path", "Message")
        for issue in report.issues:
            table.add_row(issue.severity.value, issue.code, issue.path, issue.message)
        console.print(table)
        console.print(
            f"Indexes: {report.markdown_indexes}; nodes: {report.manifest_nodes}; "
            f"search documents: {report.search_documents}"
        )
    else:
        console.print("[red]--format must be table or json[/red]")
        raise typer.Exit(2)
    if report.error_count:
        raise typer.Exit(2)
    if report.warning_count:
        raise typer.Exit(1)


@app.command("report")
def report_command(
    repository: RepositoryOption = None,
    last: Annotated[bool, typer.Option("--last", help="Show the most recent run.")] = False,
    run_id: Annotated[str | None, typer.Option("--run", metavar="ID")] = None,
    output_format: Annotated[str, typer.Option("--format", help="markdown or json")] = "markdown",
) -> None:
    """Show an immutable update run report."""
    if last and run_id:
        console.print("[red]--last and --run are mutually exclusive[/red]")
        raise typer.Exit(2)
    try:
        report = load_run_report(_repository(repository), run_id)
    except FileNotFoundError as error:
        console.print(f"[yellow]{error}[/yellow]")
        raise typer.Exit(1) from error
    if output_format == "json":
        console.print_json(json.dumps(report.model_dump(mode="json"), ensure_ascii=False))
    elif output_format == "markdown":
        lines = [
            f"# Octopus Run {report.run_id}",
            "",
            f"- 状态：{report.status}",
            f"- 开始：{report.started_at}",
            f"- 结束：{report.finished_at}",
            f"- 耗时：{report.duration_ms} ms",
            f"- AI 调用：{report.ai_usage.calls}",
            f"- AI token：{report.ai_usage.total_tokens}",
            f"- 提示词版本：{', '.join(report.ai_usage.prompt_versions) or '无 AI 调用'}",
            f"- 错误数：{len(report.errors)}",
        ]
        if report.recovery_actions:
            lines.extend(["", "## 恢复操作", ""])
            lines.extend(f"- {action}" for action in report.recovery_actions)
        console.print("\n".join(lines))
    else:
        console.print("[red]--format must be markdown or json[/red]")
        raise typer.Exit(2)


@app.command("migrate")
def migrate_command(
    repository: RepositoryOption = None,
    all_repositories: Annotated[
        bool, typer.Option("--all", help="Inspect every registered repository.")
    ] = False,
    apply: Annotated[
        bool, typer.Option("--apply", help="Back up and apply planned migrations.")
    ] = False,
    rollback: Annotated[
        str | None, typer.Option("--rollback", help="Restore an applied migration run ID.")
    ] = None,
    output_format: Annotated[str, typer.Option("--format", help="markdown or json")] = "markdown",
) -> None:
    """Plan or apply schema migrations; dry-run is the default."""
    if rollback is not None:
        if apply or all_repositories or repository is not None:
            console.print(
                "[red]--rollback cannot be combined with repository or apply options[/red]"
            )
            raise typer.Exit(2)
        try:
            rolled_back = rollback_migration(rollback)
        except (OSError, ValueError) as error:
            console.print(f"[red]Migration rollback failed:[/red] {error}")
            raise typer.Exit(3) from error
        if output_format == "json":
            console.print_json(json.dumps(rolled_back.model_dump(mode="json"), ensure_ascii=False))
        elif output_format == "markdown":
            console.print(migration_rollback_markdown(rolled_back))
        else:
            console.print("[red]--format must be markdown or json[/red]")
            raise typer.Exit(2)
        return
    if all_repositories and repository is not None:
        console.print("[red]--all and --repository are mutually exclusive[/red]")
        raise typer.Exit(2)
    if all_repositories:
        indexes = [
            Path(item.index_repository_path) for item in load_global_config().repositories.values()
        ]
    elif repository is not None:
        indexes = [_repository(repository)]
    else:
        indexes = []
    try:
        migration = plan_migrations(indexes)
        if apply and migration.required:
            migration = apply_migrations(migration)
    except (OSError, ValueError) as error:
        console.print(f"[red]Migration failed:[/red] {error}")
        raise typer.Exit(3) from error
    if output_format == "json":
        console.print_json(json.dumps(migration.model_dump(mode="json"), ensure_ascii=False))
    elif output_format == "markdown":
        console.print(migration_report_markdown(migration))
    else:
        console.print("[red]--format must be markdown or json[/red]")
        raise typer.Exit(2)


@api_app.command("start")
def api_start(
    host: Annotated[str | None, typer.Option("--host")] = None,
    port: Annotated[int | None, typer.Option("--port", min=1024, max=65535)] = None,
) -> None:
    """Start the Local API as a detached per-user process."""
    try:
        console.print_json(json.dumps(start_api_process(host, port), ensure_ascii=False))
    except (OSError, RuntimeError, ValueError) as error:
        console.print(f"[red]Unable to start API:[/red] {error}")
        raise typer.Exit(3) from error


@api_app.command("stop")
def api_stop() -> None:
    """Stop the detached per-user Local API process."""
    try:
        console.print_json(json.dumps(stop_api_process(), ensure_ascii=False))
    except (OSError, RuntimeError) as error:
        console.print(f"[red]Unable to stop API:[/red] {error}")
        raise typer.Exit(3) from error


@api_app.command("status")
def api_show_status() -> None:
    """Show Local API process and health status without exposing its token."""
    console.print_json(json.dumps(api_status(), ensure_ascii=False))


@api_app.command("token-path")
def api_show_token_path() -> None:
    """Show where a desktop client can read the Local API bearer token."""
    console.print(str(service_token_path()))


@api_app.command("run")
def api_run(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1024, max=65535)] = 8765,
) -> None:
    """Run the Local API in the foreground."""
    try:
        run_api_server(host, port)
    except (OSError, RuntimeError, ValueError) as error:
        console.print(f"[red]API failed:[/red] {error}")
        raise typer.Exit(3) from error


def _service_action(action: str) -> None:
    if sys.platform != "win32":
        console.print("[red]Windows SCM service control is available only on Windows.[/red]")
        raise typer.Exit(2)
    try:
        from .windows_service import (
            remove_service,
            service_status,
            start_service,
            stop_service,
        )

        actions = {
            "start": start_service,
            "stop": stop_service,
            "uninstall": remove_service,
        }
        if action == "status":
            console.print_json(json.dumps(service_status(), ensure_ascii=False))
        else:
            actions[action]()
            console.print(f"[green]Windows service {action} completed.[/green]")
    except Exception as error:
        console.print(f"[red]Windows service {action} failed:[/red] {error}")
        raise typer.Exit(3) from error


@service_app.command("install")
def service_install(
    username: Annotated[
        str | None,
        typer.Option(
            "--username",
            help="Windows account for access to this user's repositories.",
        ),
    ] = None,
) -> None:
    """Install the automatic Windows SCM service (administrator required)."""
    if sys.platform != "win32":
        console.print("[red]Windows SCM service control is available only on Windows.[/red]")
        raise typer.Exit(2)
    password = (
        typer.prompt("Windows service account password", hide_input=True) if username else None
    )
    try:
        from .windows_service import install_service

        install_service(username, password)
        console.print("[green]Windows service installed.[/green]")
    except Exception as error:
        console.print(f"[red]Windows service install failed:[/red] {error}")
        raise typer.Exit(3) from error


@service_app.command("start")
def service_start() -> None:
    _service_action("start")


@service_app.command("stop")
def service_stop() -> None:
    _service_action("stop")


@service_app.command("status")
def service_show_status() -> None:
    _service_action("status")


@service_app.command("uninstall")
def service_uninstall() -> None:
    """Remove the Windows SCM service (administrator required)."""
    _service_action("uninstall")


@app.command("search")
def search(
    query: str,
    repository: RepositoryOption = None,
    mode: Annotated[
        str, typer.Option("--mode", help="local or auto; auto uses AI when available.")
    ] = "local",
    full: Annotated[
        bool, typer.Option("--full", help="Compatibility alias for --mode auto.")
    ] = False,
    limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 20,
    output_format: Annotated[
        str, typer.Option("--format", help="markdown, json, or report-json")
    ] = "markdown",
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    markmap: Annotated[
        Path | None, typer.Option("--markmap", help="Render an offline HTML mindmap.")
    ] = None,
    open_result: Annotated[
        int | None,
        typer.Option("--open-result", min=1, help="Open the source or index at this rank."),
    ] = None,
) -> None:
    """Search Leaf and FolderNode indexes without reading non-text originals."""
    index = _repository(repository)
    search_index = SearchIndex(index)
    selected_mode = "auto" if full else mode.casefold()
    if selected_mode not in {"local", "auto"}:
        console.print("[red]--mode must be local or auto[/red]")
        raise typer.Exit(2)
    try:
        search_report = search_index.search_report(query, limit, selected_mode)  # type: ignore[arg-type]
        results = search_report.results
    except Exception as error:
        console.print(f"[red]Search failed:[/red] {error}")
        raise typer.Exit(2) from error
    if output_format == "json":
        value = json.dumps(
            [item.model_dump(mode="json") for item in results],
            ensure_ascii=False,
            indent=2,
        )
    elif output_format == "report-json":
        value = json.dumps(search_report.model_dump(mode="json"), ensure_ascii=False, indent=2)
    elif output_format == "markdown":
        value = search_report_markdown(search_report)
    else:
        console.print("[red]--format must be markdown, json, or report-json[/red]")
        raise typer.Exit(2)
    if output:
        atomic_write_text(output, value + ("\n" if not value.endswith("\n") else ""))
        console.print(f"Wrote {output}")
    else:
        console.print(value)
    if open_result is not None:
        if open_result > len(results):
            console.print(f"[red]Result rank {open_result} does not exist.[/red]")
            raise typer.Exit(2)
        target = results[open_result - 1].open_target_uri
        if not target or not webbrowser.open(target):
            console.print(f"[red]Unable to open result {open_result}.[/red]")
            raise typer.Exit(6)
        console.print(f"[green]Opened result {open_result}.[/green]")
    if markmap:
        markdown_path = (
            output if output_format == "markdown" and output else markmap.with_suffix(".md")
        )
        markdown = search_report_markdown(search_report)
        atomic_write_text(markdown_path, markdown)
        try:
            render_markmap(markdown_path, markmap)
            console.print(f"[green]Rendered Markmap:[/green] {markmap}")
        except Exception as error:
            console.print(
                f"[yellow]Markdown was preserved, but Markmap rendering failed:[/yellow] {error}"
            )
            raise typer.Exit(5) from error


@evaluation_app.command("retrieval")
def evaluate_retrieval_command(
    tasks: Annotated[Path, typer.Option("--tasks", exists=True, dir_okay=False)],
    judgments: Annotated[Path, typer.Option("--judgments", exists=True, dir_okay=False)],
    repository: RepositoryOption = None,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    enforce: Annotated[bool, typer.Option("--enforce")] = False,
) -> None:
    """Measure deterministic local Hit@1/5 and MRR against blind judgments."""
    result = evaluate_retrieval(_repository(repository), tasks, judgments)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if output:
        atomic_write_text(output, rendered)
        console.print(f"Wrote {output}")
    else:
        console.print(rendered, end="")
    if enforce and not result["passes_v05_gate"]:
        raise typer.Exit(1)


@evaluation_app.command("study")
def evaluate_study_command(
    tasks: Annotated[Path, typer.Option("--tasks", exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option("--output", "-o")],
    participant: Annotated[str, typer.Option("--participant")] = "",
    task_count: Annotated[int, typer.Option("--task-count", min=2)] = 12,
) -> None:
    """Run a local, counterbalanced timing session without storing queries or paths."""
    loaded = load_retrieval_tasks(tasks)
    participant_id, assignments = study_assignments(loaded, participant)
    assignments = assignments[: min(task_count, len(assignments))]
    session_id = uuid.uuid4().hex
    lines: list[str] = []
    for position, (task, condition) in enumerate(assignments, start=1):
        console.print(
            f"\n[bold]Task {position}/{len(assignments)}[/bold] · condition={condition}\n"
            f"{task.query}"
        )
        if not typer.confirm("Ready to start?", default=True):
            continue
        started = time.perf_counter()
        typer.prompt("Press Enter after opening the chosen result", default="", show_default=False)
        duration_ms = max(0, int((time.perf_counter() - started) * 1_000))
        success = typer.confirm("Was the correct target opened?", default=True)
        error_code = "" if success else typer.prompt("Failure code", default="not_found")
        record = StudyRecord(
            session_id=session_id,
            participant_id=participant_id,
            task_id=task.task_id,
            condition=condition,
            duration_ms=duration_ms,
            success=success,
            error_code=error_code,
        )
        lines.append(study_record_json(record))
        atomic_write_text(output, "\n".join(lines) + "\n")
    console.print(f"Wrote anonymous study records to {output}")


@evaluation_app.command("summarize")
def evaluate_study_summary_command(
    records: Annotated[Path, typer.Option("--records", exists=True, dir_okay=False)],
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    enforce: Annotated[bool, typer.Option("--enforce")] = False,
) -> None:
    """Aggregate one-version study records and enforce the 20-person/50% gate."""
    result = summarize_study(records)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if output:
        atomic_write_text(output, rendered)
        console.print(f"Wrote {output}")
    else:
        console.print(rendered, end="")
    if enforce and not result["passes_v05_gate"]:
        raise typer.Exit(1)


@watch_app.command("start")
def watch_start(repository: RepositoryOption = None) -> None:
    payload = start_watch(_repository(repository))
    console.print_json(json.dumps(payload, ensure_ascii=False))


@watch_app.command("stop")
def watch_stop(repository: RepositoryOption = None) -> None:
    payload = stop_watch(_repository(repository))
    console.print_json(json.dumps(payload, ensure_ascii=False))


@watch_app.command("status")
def watch_show_status(repository: RepositoryOption = None) -> None:
    index = _repository(repository)
    status = watch_status(index)
    config = load_repository_config(index)
    state = load_repository_state(index, config)
    status["queues"] = state.queues.model_dump(mode="json")
    status["scan"] = state.scan.model_dump(mode="json")
    console.print_json(json.dumps(status, ensure_ascii=False))


@app.command("_watch-run", hidden=True)
def internal_watch_run(
    repository: Annotated[Path, typer.Option("--repository")],
) -> None:
    run_watch_loop(repository.resolve())


@app.command("_api-run", hidden=True)
def internal_api_run(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1024, max=65535)] = 8765,
) -> None:
    run_api_server(host, port)


@app.command("_plugin-worker", hidden=True)
def internal_plugin_worker(
    plugin: Annotated[Path, typer.Option("--plugin")],
    request: Annotated[Path, typer.Option("--request")],
    response: Annotated[Path, typer.Option("--response")],
) -> None:
    from .plugin_worker import run_worker

    run_worker(plugin, request, response)
