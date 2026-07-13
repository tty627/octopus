# ADR 0002: Recoverable Index transactions and run reports

## Status

Accepted for v0.2.

## Context

Leaf, FolderNode and Manifest files are the durable source of truth. Updating them independently
could expose a partially updated hierarchy after a process interruption, while SQLite and logs can
always be derived again. DeepSeek failures also need to remain observable without storing source
content or credentials.

## Decision

Each update uses `.octopus/transactions/<run_id>` to stage generated files and retain backups.
Regular files are atomically replaced first and `repository-state.json` is committed last. A
persisted rollback intent is written before each destination is touched.

An interruption before the Manifest commit is rolled back. Once the Manifest is committed, the
next update treats Markdown and Manifest as authoritative, rebuilds SQLite, and then marks the
transaction complete. Completed transactions retain only their record; staged payloads and
backups are removed.

Every attempted update writes a new `.octopus/runs/<run_id>.json` using create-only semantics.
The report contains counts, timings, recovery actions, sanitized errors, and aggregated AI usage.
It never contains API keys or document excerpts.

## Consequences

- Readers see either the previous committed Manifest or the next committed Manifest.
- Search-cache failure does not invalidate Markdown indexes and is repaired on the next run.
- Crash recovery is deterministic without treating SQLite as a source of truth.
- Diagnostics can be consumed by the CLI now and by a desktop API later.
