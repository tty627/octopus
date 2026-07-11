# ADR 0001: Octopus v0.1 architecture

## Status

Accepted for v0.1.

## Decision

Octopus is a Python 3.12+ CLI. Markdown Leaf and FolderNode files are the source of truth.
The SQLite FTS5 database is a rebuildable cache. Raw repositories are accessed through a
read-only boundary, while all configuration, logs, temporary files and indexes are written only
under the Index Repository or the user's global Octopus configuration directory.

The MVP is Windows-first. It uses polling instead of native filesystem events, `.url` shortcuts
instead of privileged symbolic links, and an AI provider abstraction with DeepSeek as the only
implemented network provider.

## Consequences

- Search remains usable offline and the search database can be deleted and rebuilt.
- Non-text originals are read only while generating or updating a Leaf.
- UI, installers, vector search, native filesystem watching, and full long-path compatibility are
  deliberately outside v0.1.
