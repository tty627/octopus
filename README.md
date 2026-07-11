# Octopus

Octopus is a local-first, link-centric file indexing CLI. It scans a user-selected Raw Repository
without modifying it, writes compact Markdown Leaf and FolderNode indexes to a separate Index
Repository, and lets people or agents search those indexes without repeatedly opening non-text
originals.

The normative v1.0.1 product and file-format specifications live in
[`docs/specs/v1.0.1`](docs/specs/v1.0.1).

## MVP capabilities

- Strict Raw/Index separation and a guarded read-only Raw access layer.
- Incremental manifest with the v1.0.1 state set, stability checks, Office lock detection,
  fingerprints, queues, move hints, failures and orphan tracking.
- PDF, DOCX, XLSX, PPTX and image extraction; scanned pages use local RapidOCR/ONNX Runtime.
- Deterministically rendered Leaf and bottom-up FolderNode Markdown with protected user regions.
- Rebuildable SQLite FTS5 search cache with mixed Chinese/English terms.
- Optional DeepSeek generation and full-search reranking.
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
$env:DEEPSEEK_API_KEY = "your-key"
```

## Quick start

```powershell
octopus init --raw "D:\MyFiles" --index "D:\MyFiles-Octopus-Index" --name "MyFiles"
octopus update --once
octopus search "项目需求"
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
- `update.lock`, `watch.pid`, and transaction records while relevant

Raw files and folders are represented by `.url` shortcuts on Windows. Non-text links in normal
search results point to their Leaf index, not directly to the original file.

## Scope boundaries

The v0.1 release does not include a GUI, installer, vector database, audio/video transcription,
database-content analysis, native filesystem events, or a complete long-path mapping strategy.
Unsupported non-text formats receive a metadata Leaf with an explicit quality/error flag.
