# Octopus

Octopus is a local-first, link-centric file indexing CLI. It scans a user-selected Raw Repository
without modifying it, writes compact Markdown Leaf and FolderNode indexes to a separate Index
Repository, and lets people or agents search those indexes without repeatedly opening non-text
originals.

The current released version is `0.3.0`. v0.1 and v0.2 are internal milestones and have no
formal Git release tags.

## Product and release documentation

- [Product evolution specification](<docs/product/Octopus产品版本迭代总规范.md>)
- [Roadmap](docs/product/ROADMAP.md), [metrics](docs/product/METRICS.md), and
  [performance baseline](docs/product/PERFORMANCE_BASELINE.md)
- [Versioning, branches, and compatibility](docs/product/VERSIONING_AND_COMPATIBILITY.md)
- [Release plans and milestone records](docs/releases/) and [changelog](CHANGELOG.md)
- [Normative product and file-format specifications](docs/specs/README.md)

The `v1.0.1` under `docs/specs/` is a specification revision, not a software release version.

## v0.3 capabilities

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
```

The first automatic observation may leave recently changed files in `pending_stable`. `init` uses a
guarded initial force for files already outside the quiet window; later updates require the
configured consecutive stable observations. Strong editing signals such as Office `~$` lock files
are never bypassed.

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

The v0.3 development milestone does not include a committed GUI, installer, vector database,
audio/video transcription,
database-content analysis, native filesystem events, or a complete long-path mapping strategy.
Unsupported non-text formats receive a metadata Leaf with an explicit quality/error flag.
