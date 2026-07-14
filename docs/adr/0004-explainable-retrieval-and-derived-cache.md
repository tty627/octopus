# ADR 0004: Explainable retrieval contract and generation-bound derived cache

## Status

Accepted for v0.5 engineering work; release remains gated by v0.4 and external v0.5 acceptance.

## Context

The v0.3 search API returned different shapes for local and AI search, did not expose plain-text
files as independent results, and rebuilt the complete SQLite cache after every committed update.
An unavailable model could also turn an otherwise valid local search into a failed operation.

## Decision

All search modes return one versioned `SearchReport`. Results expose stable reason codes, a human
explanation, parser evidence, quality/status risks and a recommended open target. Plain-text files
are indexed from their compact signals in the parent FolderNode; search does not reopen Raw files.

The local ranker is versioned independently from the disposable SQLite Schema. AI may only reorder
local candidates and cite their existing evidence. Missing credentials, provider failures, budgets,
invalid output or ungrounded citations produce a local report with a stable degradation reason.

The cache stores its Schema, algorithm version and committed Manifest generation. After Manifest
commit, the updater atomically applies Markdown writes/deletes from the Index transaction. A
FolderNode refresh replaces its derived plain-text child rows. Any version/generation mismatch or
incomplete derived-state recovery causes a full rebuild from Markdown.

## Consequences

- Local API clients can consume one response shape before the v0.6 desktop client stabilizes it.
- Search-cache changes never rewrite Raw and require no persistent repository migration.
- A committed Index remains valid if cache refresh fails; the next search or update rebuilds it.
- Ranking changes require a new algorithm version and a versioned retrieval report.
