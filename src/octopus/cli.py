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
from .search import SearchIndex, results_markdown
from .utils import atomic_write_text
from .watcher import run_watch_loop, start_watch, stop_watch, watch_status

app = typer.Typer(
    name="octopus",
    help="Local-first, link-centric file indexing CLI.",
    no_args_is_help=True,
)
repo_app = typer.Typer(help="Manage registered Octopus repositories.")
watch_app = typer.Typer(help="Manage the polling watcher.")
app.add_typer(repo_app, name="repo")
app.add_typer(watch_app, name="watch")
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
def doctor(repository: RepositoryOption = None) -> None:
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
    if repository is not None or load_global_config().active_repository_id:
        try:
            index = resolve_repository(repository)
            config = load_repository_config(index)
            raw = Path(config.repository.raw_repository_path)
            checks.append(("Raw readable", os.access(raw, os.R_OK), str(raw)))
            checks.append(("Index writable", os.access(index, os.W_OK), str(index)))
        except Exception as error:
            checks.append(("Repository", False, str(error)))
    table = Table("Check", "Status", "Detail")
    for label, okay, detail in checks:
        table.add_row(label, "OK" if okay else "WARN", detail)
    console.print(table)
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


@app.command("search")
def search(
    query: str,
    repository: RepositoryOption = None,
    full: Annotated[
        bool, typer.Option("--full", help="Use DeepSeek to rerank candidates.")
    ] = False,
    limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 20,
    output_format: Annotated[str, typer.Option("--format", help="markdown or json")] = "markdown",
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    markmap: Annotated[
        Path | None, typer.Option("--markmap", help="Render an offline HTML mindmap.")
    ] = None,
) -> None:
    """Search Leaf and FolderNode indexes without reading non-text originals."""
    index = _repository(repository)
    search_index = SearchIndex(index)
    try:
        results = (
            search_index.full_search(query, limit) if full else search_index.search(query, limit)
        )
    except Exception as error:
        console.print(f"[red]Search failed:[/red] {error}")
        raise typer.Exit(4 if full else 2) from error
    if output_format == "json":
        value = json.dumps(
            [item.model_dump(mode="json") for item in results], ensure_ascii=False, indent=2
        )
    elif output_format == "markdown":
        value = results_markdown(query, results)
    else:
        console.print("[red]--format must be markdown or json[/red]")
        raise typer.Exit(2)
    if output:
        atomic_write_text(output, value + ("\n" if not value.endswith("\n") else ""))
        console.print(f"Wrote {output}")
    else:
        console.print(value)
    if markmap:
        markdown_path = (
            output if output and output_format == "markdown" else markmap.with_suffix(".md")
        )
        atomic_write_text(markdown_path, results_markdown(query, results))
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
