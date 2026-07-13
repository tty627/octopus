from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import (
    create_repository,
    load_global_config,
    load_repository_config,
    load_repository_state,
    resolve_repository,
    save_global_config,
)
from .engine import UpdateEngine
from .markmap import render_markmap
from .migrations import apply_migrations, migration_report_markdown, plan_migrations
from .prompts import PROMPT_VERSION
from .search import SearchIndex, results_markdown, search_report_markdown
from .service_control import (
    api_status,
    run_api_server,
    service_token_path,
    start_api_process,
    stop_api_process,
)
from .transactions import load_run_report
from .utils import atomic_write_text
from .validation import validate_repository
from .watcher import run_watch_loop, start_watch, stop_watch, watch_status

app = typer.Typer(
    name="octopus",
    help="Local-first, link-centric file indexing CLI.",
    no_args_is_help=True,
)
repo_app = typer.Typer(help="Manage registered Octopus repositories.")
watch_app = typer.Typer(help="Manage the polling watcher.")
api_app = typer.Typer(help="Manage the authenticated loopback Local API.")
service_app = typer.Typer(help="Manage the optional Windows SCM service.")
app.add_typer(repo_app, name="repo")
app.add_typer(watch_app, name="watch")
app.add_typer(api_app, name="api")
app.add_typer(service_app, name="service")
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


@app.command("version")
def version() -> None:
    """Print the installed Octopus version."""
    console.print(__version__)


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
    checks.append(
        (
            "DEEPSEEK_API_KEY",
            bool(os.environ.get("DEEPSEEK_API_KEY", "").strip()),
            "configured" if os.environ.get("DEEPSEEK_API_KEY") else "not configured",
        )
    )
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
        if label not in {"DEEPSEEK_API_KEY", "Markmap runtime"}
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
    output_format: Annotated[str, typer.Option("--format", help="markdown or json")] = "markdown",
) -> None:
    """Plan or apply schema migrations; dry-run is the default."""
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
    full: Annotated[
        bool, typer.Option("--full", help="Use DeepSeek to rerank candidates.")
    ] = False,
    limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 20,
    output_format: Annotated[
        str, typer.Option("--format", help="markdown, json, or report-json")
    ] = "markdown",
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    markmap: Annotated[
        Path | None, typer.Option("--markmap", help="Render an offline HTML mindmap.")
    ] = None,
) -> None:
    """Search Leaf and FolderNode indexes without reading non-text originals."""
    index = _repository(repository)
    search_index = SearchIndex(index)
    try:
        search_report = search_index.full_search_report(query, limit) if full else None
        results = search_report.results if search_report else search_index.search(query, limit)
    except Exception as error:
        console.print(f"[red]Search failed:[/red] {error}")
        raise typer.Exit(4 if full else 2) from error
    if output_format == "json":
        value = json.dumps(
            [
                item.model_dump(mode="json", exclude={"matched_terms", "match_reasons"})
                for item in results
            ],
            ensure_ascii=False,
            indent=2,
        )
    elif output_format == "report-json":
        if search_report is None:
            console.print("[red]--format report-json requires --full[/red]")
            raise typer.Exit(2)
        value = json.dumps(search_report.model_dump(mode="json"), ensure_ascii=False, indent=2)
    elif output_format == "markdown":
        value = (
            search_report_markdown(search_report)
            if search_report
            else results_markdown(query, results)
        )
    else:
        console.print("[red]--format must be markdown, json, or report-json[/red]")
        raise typer.Exit(2)
    if output:
        atomic_write_text(output, value + ("\n" if not value.endswith("\n") else ""))
        console.print(f"Wrote {output}")
    else:
        console.print(value)
    if markmap:
        markdown_path = (
            output if output_format == "markdown" and output else markmap.with_suffix(".md")
        )
        markdown = (
            search_report_markdown(search_report)
            if search_report
            else results_markdown(query, results)
        )
        atomic_write_text(markdown_path, markdown)
        try:
            render_markmap(markdown_path, markmap)
            console.print(f"[green]Rendered Markmap:[/green] {markmap}")
        except Exception as error:
            console.print(
                f"[yellow]Markdown was preserved, but Markmap rendering failed:[/yellow] {error}"
            )
            raise typer.Exit(5) from error


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
