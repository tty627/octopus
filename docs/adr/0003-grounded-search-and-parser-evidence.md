# ADR 0003: Grounded search, parser evidence and AI budgets

## Status

Accepted for v0.3.

## Context

The v0.2 search cache treated all searchable fields uniformly, and a generated full-search answer
could name a node without a deterministic citation artifact. Parser output also exposed structure
and quality flags but not stable, machine-readable evidence locators. AI call limits did not bound
token volume or configured-price cost.

## Decision

Parsers emit bounded evidence records with a locator, kind, extraction method and short excerpt.
Leaf machine headers and Markdown expose those records without copying large source passages.
Office parsers additionally record bounded structural diagnostics such as sampled formulas, hidden
sheets, media, shapes and notes.

The disposable SQLite cache uses separate FTS fields for names, summaries, keywords and body text.
BM25 field weights are combined with exact-name, exact-summary and query-term coverage boosts. A
cache schema marker causes old or incomplete databases to rebuild automatically from Markdown.

Full-search answers may cite only node IDs present in the ranked candidate set. Octopus converts
those IDs into deterministic `S<n>` citations linked to Leaf or FolderNode Markdown, removes
invalid citation labels, and adds stale-index warnings independently of model output.

Prompts have an explicit version. The provider records that version and enforces configured limits
for calls, estimated input tokens, output tokens and estimated cost. Cost caps require user-supplied
token prices; Octopus does not embed provider prices.

## Consequences

- Search-cache migrations remain disposable and do not change schema `0.2` Markdown compatibility.
- Citations are inspectable without reading non-text Raw files.
- Parser evidence improves diagnosis and future answer grounding while remaining intentionally
  compact.
- Token and cost ceilings can stop AI work while preserving already committed local indexes.
