# Octopus

Octopus is a local-first, link-centric file indexer. It scans a user-selected Raw Repository
without modifying it, writes compact Markdown Leaf and FolderNode indexes to a separate Index
Repository, and lets people or agents search those indexes without repeatedly opening non-text
originals.

The current formally tagged version is `0.3.0`; v0.4–v0.6 are closed engineering milestones,
the active candidate is `0.6.0a1`, and development now targets v0.7. v0.1 and v0.2 are internal
milestones and have no formal Git release tags.

## Product and release documentation

- [Product evolution specification](<docs/product/Octopus产品版本迭代总规范.md>)
- [Roadmap](docs/product/ROADMAP.md), [metrics](docs/product/METRICS.md), and
  [performance](docs/product/PERFORMANCE_BASELINE.md) / [search](docs/product/SEARCH_EVALUATION_BASELINE.md)
  baselines
- [Versioning, branches, and compatibility](docs/product/VERSIONING_AND_COMPATIBILITY.md)
- [Release plans and milestone records](docs/releases/) and [changelog](CHANGELOG.md)
- [Windows installation](docs/user/WINDOWS_INSTALLATION.md) and
  [troubleshooting](docs/user/TROUBLESHOOTING.md)
- [Normative product and file-format specifications](docs/specs/README.md)

The `v1.0.1` under `docs/specs/` is a specification revision, not a software release version.

## v0.6 engineering milestone

- Service-backed Tkinter desktop with repository list/create, update/retry, validation, search-cache
  repair, status center, local/AI-degraded search and source/index opening.
- Stable loopback [Local API v1 contract](docs/api/LOCAL_API_V1.md) with contract-version handshake,
  async jobs and repository creation.
- Actionable service, lock, migration and AI-degradation states; keyboard shortcuts and DPI scaling.
- The desktop layer contains workflow/presentation logic only; repository mutations remain in the
  shared API/Engine boundary.

## v0.5 engineering milestone

- Versioned offline Chinese/English DOCX/XLSX search tasks covering duplicate names and stale data.
- Field-level match excerpts, extraction evidence, source-relative paths, risk flags and stable open
  targets in CLI and Local API JSON.
- `octopus evaluate-search --enforce` for Top-5, MRR, task-failure, inspection-step and explanation
  contract gates without an API key.
- `octopus search --open-result N` for opening the selected source or index target.
- Plain-text files as independent results plus Manifest-generation-bound incremental cache refresh.
- The 60-task `octopus-retrieval-v1` suite reaches 54/60 Hit@5 (90.0%); the focused 10-task
  explanation suite reaches 100% Top-5 and MRR 1.00 with no contract failures.
- Search schema `0.5` automatically rebuilds old disposable caches.

## v0.4 capabilities

- Simplified-Chinese Tkinter first-run wizard with a deterministic six-format sample repository.
- Read-only file, format, time, disk and AI-call preflight; wizard repositories always start with
  AI disabled.
- Monotonic indexing progress, safe pre-commit cancellation, immutable cancelled RunReports and
  one-click retry without changing Raw files.
- Local top-five search with actions to open the generated index or original source.
- Cached, non-blocking GitHub stable-release checks through GUI and `octopus upgrade check`.
- PyInstaller 6.21 shared onedir build plus a per-user Inno Setup 6.7.1 offline-installer pipeline.

The v0.4 milestone was closed without Authenticode, clean-VM/Defender validation or human cohorts;
none of those checks are claimed as completed in the [v0.4 record](docs/releases/v0.4.md).

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
- A `DEEPSEEK_API_KEY` only for AI summaries and optional `search --mode auto`; local search and
  automatic degradation require no key

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
octopus search "项目需求" --format json --open-result 1
octopus search --mode auto "找到最重要的项目需求和相关材料" --format report-json
octopus search --full "找到最重要的项目需求和相关材料" --format report-json
octopus search --full "找到最重要的项目需求和相关材料" --markmap result.html
octopus evaluate-search --output .octopus-dev\benchmarks\search-value.json --enforce
octopus watch start
octopus watch status
octopus watch stop
octopus upgrade check --format json
octopus evaluate retrieval --tasks benchmarks/retrieval/v1/tasks.jsonl --judgments benchmarks/retrieval/v1/judgments.jsonl --enforce
octopus evaluate study --tasks benchmarks/retrieval/v1/tasks.jsonl --output study.jsonl
octopus evaluate summarize --records study.jsonl --output study-summary.json
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

Raw files and folders are represented by `.url` shortcuts on Windows. v0.5 search results expose
both the generated index path and a stable open target for the original source.

## Scope boundaries

v0.4 does not include automatic installation of updates, the full v0.6 desktop search UI, tray or
startup behavior, automatic Windows service installation, macOS/Linux installers, or ARM64
artifacts. Unsupported non-text formats receive a metadata Leaf with an explicit quality/error
flag.
