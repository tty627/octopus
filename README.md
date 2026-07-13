# Octopus

Octopus is a local-first, link-centric file indexer. It scans a user-selected Raw Repository
without modifying it, writes compact Markdown Leaf and FolderNode indexes to a separate Index
Repository, and lets people or agents search those indexes without repeatedly opening non-text
originals.

The current released version is `0.3.0`; active development is `0.4.0.dev0` on the `v0.4`
branch. v0.1 and v0.2 are internal milestones and have no formal Git release tags.

## Product and release documentation

- [Product evolution specification](<docs/product/Octopus产品版本迭代总规范.md>)
- [Roadmap](docs/product/ROADMAP.md), [metrics](docs/product/METRICS.md), and
  [performance baseline](docs/product/PERFORMANCE_BASELINE.md)
- [Versioning, branches, and compatibility](docs/product/VERSIONING_AND_COMPATIBILITY.md)
- [Release plans and milestone records](docs/releases/) and [changelog](CHANGELOG.md)
- [Windows installation](docs/user/WINDOWS_INSTALLATION.md) and
  [troubleshooting](docs/user/TROUBLESHOOTING.md)
- [Normative product and file-format specifications](docs/specs/README.md)

The `v1.0.1` under `docs/specs/` is a specification revision, not a software release version.

## v0.4 development capabilities

- Simplified-Chinese Tkinter first-run wizard with a deterministic six-format sample repository.
- Read-only file, format, time, disk and AI-call preflight; wizard repositories always start with
  AI disabled.
- Monotonic indexing progress, safe pre-commit cancellation, immutable cancelled RunReports and
  one-click retry without changing Raw files.
- Local top-five search with actions to open the generated index or original source.
- Cached, non-blocking GitHub stable-release checks through GUI and `octopus upgrade check`.
- PyInstaller 6.21 shared onedir build plus a per-user Inno Setup 6.7.3 offline-installer pipeline.

The signed installer and `0.4.0` release remain gated on protected-CI signing, clean Windows 11 VM
validation and the alpha/RC user acceptance cohorts in the [v0.4 plan](docs/releases/v0.4.md).

## Core capabilities inherited from v0.3

- Strict Raw/Index separation and a guarded read-only Raw access layer.
- Incremental manifest with the v1.0.1 state set, stability checks, Office lock detection,
  fingerprints, queues, move hints, failures and orphan tracking.
- PDF, DOCX, XLSX, PPTX and image extraction; scanned pages use local RapidOCR/ONNX Runtime.
- Parser evidence locators and bounded extraction diagnostics for pages, headings, sheets, slides,
  tables and OCR output.
- Deterministically rendered Leaf and bottom-up FolderNode Markdown with protected user regions.
- Rebuildable, automatically migrated SQLite FTS5 cache with field weighting, exact-name boosts,
  query-term coverage and mixed Chinese/English terms.
- Optional DeepSeek generation and full-search reranking with candidate-validated citations.
- Versioned prompts plus per-run call, input-token, output-token and configured-price cost limits.
- Manifest-last Index transactions with automatic rollback or derived-cache recovery.
- Immutable per-run reports with aggregated, secret-free AI usage and error telemetry.
- Read-only dry-run and repository validation for automation and future desktop clients.
- Offline Markmap HTML rendering through `markmap-cli`.
- Windows polling watcher, repository lock and update logs.

## Requirements

The Windows 11 x64 offline installer bundles Python, OCR and document parsers. End users do not
need Python, Node.js or an API key for the first-run workflow.

Source/development installations require:

- Python 3.12+
- Node.js with `npx` only when HTML Markmap rendering is needed
- A `DEEPSEEK_API_KEY` for AI summaries and `search --full`

## Install for development

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
octopus doctor
```

The API key is never written to repository configuration:

```powershell
$env:DEEPSEEK_API_KEY = Read-Host "DeepSeek API Key" -MaskInput
```

## Quick start

For a source checkout, start the first-run wizard with:

```powershell
octopus-gui
```

Advanced CLI examples:

```powershell
octopus init --raw "D:\MyFiles" --index "D:\MyFiles-Octopus-Index" --name "MyFiles"
octopus update --once
octopus update --dry-run --format json
octopus validate --format json
octopus report --last --format markdown
octopus search "项目需求"
octopus search --full "找到最重要的项目需求和相关材料" --format report-json
octopus search --full "找到最重要的项目需求和相关材料" --markmap result.html
octopus watch start
octopus watch status
octopus watch stop
octopus upgrade check --format json
```

The first automatic observation may leave recently changed files in `pending_stable`. Explicit
`--force`/initialization bypasses the quiet-time delay, while strong editing signals such as Office
`~$` lock files are never bypassed. Later automatic updates require the configured consecutive
stable observations.

## Generated repository metadata

All operational files are under `<Index Repository>/.octopus/`:

- `repository-config.json` and `repository-state.json`
- `search.sqlite3` (disposable; rebuild with `octopus rebuild-search`)
- `update-log.md` and `update-events.jsonl`
- `transactions/<run_id>/record.json` and immutable `runs/<run_id>.json` reports
- `update.lock` and `watch.pid`

Raw files and folders are represented by `.url` shortcuts on Windows. Non-text links in normal
search results point to their Leaf index, not directly to the original file.

## Scope boundaries

v0.4 does not include automatic installation of updates, the full v0.6 desktop search UI, tray or
startup behavior, automatic Windows service installation, macOS/Linux installers, or ARM64
artifacts. Unsupported non-text formats receive a metadata Leaf with an explicit quality/error
flag.
