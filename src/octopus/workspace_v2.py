from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
import unicodedata
import uuid
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field

from .config import (
    load_global_config,
    load_repository_config,
    save_global_config,
    workspace_storage_path,
)
from .credentials import CredentialStoreError, resolve_ai_api_key
from .models import AIConfig, GlobalWorkspace, OctopusModel, utc_now
from .utils import sha256_file

WORKSPACE_SCHEMA_VERSION = "2.0"
READABLE_THRESHOLD = 0.72
PARTIAL_THRESHOLD = 0.45
TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".csv",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".log",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".sql",
}
IGNORED_DIRECTORY_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".octopus",
    "node_modules",
    "__pycache__",
}


class WorkspaceEvidence(OctopusModel):
    page_number: int | None = None
    heading: str = ""
    excerpt: str
    reason: str
    quality_score: float = Field(ge=0.0, le=1.0)


class WorkspaceSearchResult(OctopusModel):
    document_id: str
    name: str
    relative_path: str
    extension: str
    content_hash: str
    size_bytes: int = Field(ge=0)
    modified_at: str
    page_count: int = Field(ge=0)
    readability: Literal["readable", "partial", "low"]
    readability_score: float = Field(ge=0.0, le=1.0)
    source_uri: str
    overview: str = ""
    best_evidence: WorkspaceEvidence
    additional_evidence: list[WorkspaceEvidence] = Field(default_factory=list)
    rank: int = Field(ge=1)


class WorkspaceSearchReport(OctopusModel):
    query: str
    requested_mode: Literal["local", "assisted"] = "local"
    actual_mode: Literal["local", "assisted", "degraded"] = "local"
    degradation_reason: str = ""
    answer: str = ""
    results: list[WorkspaceSearchResult] = Field(default_factory=list)
    candidate_count: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)


class AssistedSearchOrder(OctopusModel):
    ordered_document_ids: list[str] = Field(default_factory=list)
    answer: str = ""


class WorkspaceHealth(OctopusModel):
    document_count: int = 0
    readable_count: int = 0
    partial_count: int = 0
    low_quality_count: int = 0
    metadata_only_count: int = 0
    failed_count: int = 0
    last_sync_at: str = ""


class WorkspacePayload(OctopusModel):
    workspace_id: str
    name: str
    raw_path: str
    available: bool
    enabled: bool
    vision_enabled: bool
    legacy_index_present: bool
    health: WorkspaceHealth = Field(default_factory=WorkspaceHealth)


class WorkspaceDocument(OctopusModel):
    document_id: str
    name: str
    relative_path: str
    extension: str
    content_hash: str
    size_bytes: int = Field(ge=0)
    modified_at: str
    title: str
    overview: str = ""
    page_count: int = Field(ge=0)
    readability: Literal["readable", "partial", "low"]
    readability_score: float = Field(ge=0.0, le=1.0)
    indexing_state: Literal["indexed", "metadata_only", "failed"]
    error: str = ""
    source_uri: str


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int | None
    text: str
    extraction_method: str
    quality_score: float

    @property
    def readability(self) -> Literal["readable", "partial", "low"]:
        return readability_label(self.quality_score)


@dataclass(frozen=True)
class ExtractedSource:
    title: str
    pages: list[ExtractedPage]
    page_count: int
    status: str = "indexed"
    error: str = ""


def _script_name(character: str) -> str:
    if character.isascii():
        return "LATIN" if character.isalpha() else "COMMON"
    name = unicodedata.name(character, "")
    for script in (
        "CJK",
        "HIRAGANA",
        "KATAKANA",
        "HANGUL",
        "GREEK",
        "CYRILLIC",
        "ARABIC",
        "HEBREW",
        "DEVANAGARI",
        "THAI",
        "BENGALI",
        "TIBETAN",
        "SINHALA",
        "ETHIOPIC",
        "GEORGIAN",
        "ARMENIAN",
        "LAO",
        "MYANMAR",
        "KHMER",
    ):
        if script in name:
            return script
    return "COMMON" if not character.isalpha() else "OTHER"


def readability_score(text: str) -> float:
    compact = [character for character in text if not character.isspace()]
    if not compact:
        return 0.0
    total = len(compact)
    printable_ratio = sum(character.isprintable() for character in compact) / total
    control_ratio = (
        sum(unicodedata.category(character).startswith("C") for character in compact) / total
    )
    replacement_ratio = sum(character in {"\ufffd", "\x00"} for character in compact) / total
    semantic_ratio = (
        sum(
            character.isalnum() or "\u4e00" <= character <= "\u9fff"
            for character in compact
        )
        / total
    )
    scripts = Counter(
        _script_name(character)
        for character in compact
        if character.isalpha() or "\u4e00" <= character <= "\u9fff"
    )
    meaningful_scripts = [
        name for name, count in scripts.items() if name != "COMMON" and count >= 2
    ]
    script_penalty = max(0, len(meaningful_scripts) - 3) * 0.14
    dominant_ratio = max(scripts.values(), default=total) / max(1, sum(scripts.values()))
    fragmented_script_penalty = 0.0
    if len(meaningful_scripts) >= 4 and dominant_ratio < 0.72:
        fragmented_script_penalty = 0.22
    repeated_ratio = max(Counter(compact).values()) / total
    repeated_penalty = max(0.0, repeated_ratio - 0.32) * 0.8
    repeated_ngram_ratio = 0.0
    if total >= 8 and len(set(compact)) / total < 0.45:
        repeated_ngram_ratio = max(
            (
                max(
                    Counter(
                        tuple(compact[index : index + size])
                        for index in range(total - size + 1)
                    ).values()
                )
                * size
                / total
            )
            for size in (2, 3, 4)
        )
    repeated_ngram_penalty = max(0.0, repeated_ngram_ratio - 0.40) * 0.75
    short_penalty = 0.12 if total < 20 else 0.0
    score = (
        0.42 * printable_ratio
        + 0.38 * min(1.0, semantic_ratio / 0.55)
        + 0.20 * min(1.0, total / 120)
        - control_ratio * 4.0
        - replacement_ratio * 5.0
        - script_penalty
        - fragmented_script_penalty
        - repeated_penalty
        - repeated_ngram_penalty
        - short_penalty
    )
    return round(max(0.0, min(1.0, score)), 4)


def readability_label(score: float) -> Literal["readable", "partial", "low"]:
    if score >= READABLE_THRESHOLD:
        return "readable"
    if score >= PARTIAL_THRESHOLD:
        return "partial"
    return "low"


def _readability_value(value: object) -> Literal["readable", "partial", "low"]:
    text = str(value)
    if text not in {"readable", "partial", "low"}:
        return "low"
    return cast(Literal["readable", "partial", "low"], text)


def _indexing_state_value(value: object) -> Literal["indexed", "metadata_only", "failed"]:
    text = str(value)
    if text not in {"indexed", "metadata_only", "failed"}:
        return "failed"
    return cast(Literal["indexed", "metadata_only", "failed"], text)


def normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[\w\u4e00-\u9fff]+", normalized, flags=re.UNICODE))


def search_terms(value: str) -> list[str]:
    normalized = normalize_search_text(value)
    terms = set(normalized.split())
    for block in re.findall(r"[\u4e00-\u9fff]+", normalized):
        for size in (2, 3):
            terms.update(block[index : index + size] for index in range(len(block) - size + 1))
    return sorted(term for term in terms if term)


def _fts_text(value: str) -> str:
    normalized = normalize_search_text(value)
    tokens = search_terms(value)
    return " ".join([normalized, *tokens]).strip()


def _excerpt(text: str, query: str, limit: int = 240) -> str:
    compact = " ".join(text.split())
    if not compact:
        return "正文识别质量较低，可按文件名查找。"
    normalized = compact.casefold()
    position = normalized.find(query.casefold())
    if position < 0:
        for term in search_terms(query):
            position = normalized.find(term)
            if position >= 0:
                break
    start = max(0, position - 70) if position >= 0 else 0
    value = compact[start : start + limit]
    if start:
        value = "…" + value
    if start + limit < len(compact):
        value += "…"
    return value


@lru_cache(maxsize=1)
def _ocr_engine() -> Any:
    from rapidocr import RapidOCR

    return RapidOCR()


def _ocr_text(image: Any) -> str:
    try:
        result = _ocr_engine()(image)
    except Exception:
        return ""
    if hasattr(result, "txts"):
        return "\n".join(str(item) for item in (result.txts or []))
    if isinstance(result, tuple) and result:
        rows = result[0] or []
    elif isinstance(result, list):
        rows = result
    else:
        rows = []
    texts: list[str] = []
    for row in rows:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            value = row[1]
            texts.append(str(value[0] if isinstance(value, (list, tuple)) else value))
    return "\n".join(texts)


def _extract_pdf(path: Path) -> ExtractedSource:
    import pypdfium2 as pdfium  # type: ignore[import-untyped]
    from pypdf import PdfReader

    document = pdfium.PdfDocument(str(path))
    reader: PdfReader | None = None
    pages: list[ExtractedPage] = []
    try:
        for page_index in range(len(document)):
            page = document[page_index]
            text_page = page.get_textpage()
            try:
                pdfium_text = text_page.get_text_range().strip()
            finally:
                text_page.close()
            selected_text = pdfium_text
            method = "pdfium"
            score = readability_score(selected_text)
            if score < READABLE_THRESHOLD:
                try:
                    reader = reader or PdfReader(str(path), strict=False)
                    pypdf_text = (
                        (reader.pages[page_index].extract_text() or "").strip()
                        if page_index < len(reader.pages)
                        else ""
                    )
                except Exception:
                    pypdf_text = ""
                pypdf_score = readability_score(pypdf_text)
                if pypdf_score > score:
                    selected_text = pypdf_text
                    score = pypdf_score
                    method = "pypdf"
            if score < READABLE_THRESHOLD:
                bitmap = page.render(scale=2.0)
                try:
                    ocr_text = _ocr_text(bitmap.to_pil()).strip()
                finally:
                    bitmap.close()
                ocr_score = readability_score(ocr_text)
                if ocr_text and (ocr_score >= score + 0.05 or score < PARTIAL_THRESHOLD):
                    selected_text = ocr_text
                    score = ocr_score
                    method = "ocr"
            pages.append(
                ExtractedPage(
                    page_number=page_index + 1,
                    text=selected_text,
                    extraction_method=method,
                    quality_score=score,
                )
            )
            page.close()
    finally:
        document.close()
    metadata: Any = reader.metadata if reader is not None else None
    metadata = metadata or {}
    title = str(metadata.get("/Title", "")).strip() or path.stem
    return ExtractedSource(title=title, pages=pages, page_count=len(pages))


def _decode_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
        if best is not None:
            return str(best)
    except ImportError:
        pass
    return data.decode("utf-8", errors="replace")


def _extract_text(path: Path) -> ExtractedSource:
    text = _decode_text(path)
    return ExtractedSource(
        title=path.stem,
        pages=[
            ExtractedPage(
                page_number=None,
                text=text,
                extraction_method="text",
                quality_score=readability_score(text),
            )
        ],
        page_count=0,
    )


def extract_source(path: Path) -> ExtractedSource:
    suffix = path.suffix.casefold()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix in TEXT_EXTENSIONS:
        return _extract_text(path)
    return ExtractedSource(title=path.stem, pages=[], page_count=0, status="metadata_only")


def _iter_source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for current, directories, names in os.walk(root):
        directories[:] = [
            name
            for name in directories
            if name not in IGNORED_DIRECTORY_NAMES and not name.startswith(".")
        ]
        base = Path(current)
        for name in names:
            path = base / name
            if path.is_symlink() or name.startswith("~$"):
                continue
            files.append(path)
    return sorted(files, key=lambda item: item.as_posix().casefold())


def _modified_at(stat: os.stat_result) -> str:
    return datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()


def _passage_chunks(text: str, *, target: int = 1_600, overlap: int = 180) -> list[str]:
    compact = text.strip()
    if not compact:
        return []
    if len(compact) <= target:
        return [compact]
    chunks: list[str] = []
    start = 0
    while start < len(compact):
        desired_end = min(len(compact), start + target)
        end = desired_end
        if desired_end < len(compact):
            candidates = [
                compact.rfind(marker, start + target // 2, desired_end)
                for marker in ("\n\n", "\n", "。", ". ", "；", "; ")
            ]
            boundary = max(candidates, default=-1)
            if boundary > start:
                end = boundary + 1
        chunk = compact[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(compact):
            break
        start = max(start + 1, end - overlap)
    return chunks


def _apply_assisted_order(
    results: list[WorkspaceSearchResult],
    ordered_document_ids: list[str],
    exact_document_ids: list[str],
) -> list[WorkspaceSearchResult]:
    by_id = {result.document_id: result for result in results}
    allowed = set(by_id)
    exact = [document_id for document_id in exact_document_ids if document_id in allowed]
    requested: list[str] = []
    for document_id in ordered_document_ids:
        if document_id in allowed and document_id not in exact and document_id not in requested:
            requested.append(document_id)
    remaining = [
        result.document_id
        for result in results
        if result.document_id not in exact and result.document_id not in requested
    ]
    final_ids = [*exact, *requested, *remaining]
    return [
        by_id[document_id].model_copy(update={"rank": rank})
        for rank, document_id in enumerate(final_ids, start=1)
    ]


def assisted_rerank(
    workspace: GlobalWorkspace,
    query: str,
    results: list[WorkspaceSearchResult],
) -> tuple[list[WorkspaceSearchResult], str]:
    if not workspace.ai_policy.enabled or not results:
        raise RuntimeError("assisted_search_not_configured")
    try:
        credential = resolve_ai_api_key(workspace.workspace_id, workspace.ai_policy.provider)
    except CredentialStoreError as error:
        raise RuntimeError("credential_store_unavailable") from error
    if not credential.api_key:
        raise RuntimeError("ai_key_not_configured")
    from openai import OpenAI

    candidates = [
        {
            "document_id": result.document_id,
            "name": result.name,
            "relative_path": result.relative_path,
            "readability": result.readability,
            "reason": result.best_evidence.reason,
            "page_number": result.best_evidence.page_number,
            "excerpt": result.best_evidence.excerpt,
        }
        for result in results
    ]
    client = OpenAI(
        api_key=credential.api_key,
        base_url=workspace.ai_policy.base_url,
        max_retries=0,
        timeout=60.0,
    )
    response = client.chat.completions.create(
        model=workspace.ai_policy.model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Reorder only the supplied document_id values for relevance. "
                    "Do not invent IDs or evidence. Return JSON with ordered_document_ids "
                    "and a concise Chinese answer grounded only in the candidates."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"query": query, "candidates": candidates}, ensure_ascii=False
                ),
            },
        ],
        response_format={"type": "json_object"},
        max_tokens=min(800, workspace.ai_policy.max_output_tokens_per_request),
        stream=False,
    )
    choices = getattr(response, "choices", None) or []
    content = getattr(getattr(choices[0], "message", None), "content", "") if choices else ""
    if not content:
        raise RuntimeError("ai_invalid_output")
    try:
        output = AssistedSearchOrder.model_validate_json(content)
    except ValueError as error:
        raise RuntimeError("ai_invalid_output") from error
    normalized_query = normalize_search_text(query)
    exact_ids = [
        result.document_id
        for result in results
        if normalized_query
        in {
            normalize_search_text(result.name),
            normalize_search_text(Path(result.name).stem),
        }
    ]
    return (
        _apply_assisted_order(results, output.ordered_document_ids, exact_ids),
        output.answer.strip(),
    )


def ensure_v2_workspaces() -> dict[str, GlobalWorkspace]:
    config = load_global_config()
    changed = False
    for repository_id, repository in config.repositories.items():
        if repository_id in config.workspaces:
            continue
        legacy_path = Path(repository.index_repository_path)
        try:
            legacy_config = load_repository_config(legacy_path)
        except (OSError, ValueError):
            continue
        workspace = GlobalWorkspace(
            workspace_id=repository_id,
            name=repository.name,
            raw_path=legacy_config.repository.raw_repository_path,
            storage_path=str(workspace_storage_path(repository_id)),
            legacy_index_path=str(legacy_path),
            enabled=repository.enabled,
            ai_policy=legacy_config.ai_policy.model_copy(deep=True),
        )
        config.workspaces[repository_id] = workspace
        changed = True
    if not config.active_workspace_id and config.active_repository_id in config.workspaces:
        config.active_workspace_id = config.active_repository_id
        changed = True
    if changed:
        config.schema_version = WORKSPACE_SCHEMA_VERSION
        save_global_config(config)
    return config.workspaces


def create_workspace(raw_path: Path, name: str | None = None) -> GlobalWorkspace:
    raw = raw_path.expanduser().resolve()
    if not raw.is_dir():
        raise ValueError(f"资料文件夹不存在或不可访问: {raw}")
    config = load_global_config()
    ensure_v2_workspaces()
    config = load_global_config()
    for workspace in config.workspaces.values():
        if Path(workspace.raw_path).resolve() == raw:
            config.active_workspace_id = workspace.workspace_id
            save_global_config(config)
            return workspace
    workspace_id = str(uuid.uuid4())
    workspace = GlobalWorkspace(
        workspace_id=workspace_id,
        name=(name or raw.name or "资料空间").strip(),
        raw_path=str(raw),
        storage_path=str(workspace_storage_path(workspace_id)),
        ai_policy=AIConfig(enabled=False),
    )
    config.schema_version = WORKSPACE_SCHEMA_VERSION
    config.workspaces[workspace_id] = workspace
    config.active_workspace_id = workspace_id
    save_global_config(config)
    return workspace


def get_workspace(workspace_id: str) -> GlobalWorkspace:
    ensure_v2_workspaces()
    workspace = load_global_config().workspaces.get(workspace_id)
    if workspace is None:
        raise KeyError(workspace_id)
    return workspace


class WorkspaceStore:
    def __init__(self, workspace: GlobalWorkspace) -> None:
        self.workspace = workspace
        self.raw = Path(workspace.raw_path).expanduser().resolve()
        self.storage = Path(workspace.storage_path).expanduser().resolve()
        self.database = self.storage / "workspace.sqlite3"
        self.previews = self.storage / "previews"

    def _connect(self) -> sqlite3.Connection:
        self.storage.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        self._ensure_schema(connection)
        return connection

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS workspace_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                relative_path TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                extension TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL DEFAULT 0,
                modified_at TEXT NOT NULL,
                title TEXT NOT NULL,
                overview TEXT NOT NULL,
                page_count INTEGER NOT NULL,
                readability TEXT NOT NULL,
                readability_score REAL NOT NULL,
                indexing_state TEXT NOT NULL,
                error TEXT NOT NULL,
                indexed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS documents_content_hash ON documents(content_hash);
            CREATE TABLE IF NOT EXISTS pages (
                page_id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                page_number INTEGER,
                text TEXT NOT NULL,
                extraction_method TEXT NOT NULL,
                quality_score REAL NOT NULL,
                readability TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS passages (
                passage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                page_number INTEGER,
                ordinal INTEGER NOT NULL,
                heading TEXT NOT NULL,
                text TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
                document_id UNINDEXED,
                page_number UNINDEXED,
                name,
                path,
                heading,
                body,
                tokens,
                tokenize='unicode61 remove_diacritics 2'
            );
            """
        )
        document_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(documents)").fetchall()
        }
        if "mtime_ns" not in document_columns:
            connection.execute(
                "ALTER TABLE documents ADD COLUMN mtime_ns INTEGER NOT NULL DEFAULT 0"
            )

    def _source_path(self, relative_path: str) -> Path:
        path = (self.raw / relative_path).resolve()
        if path != self.raw and self.raw not in path.parents:
            raise ValueError("Source path escapes the workspace")
        return path

    def sync(self) -> dict[str, Any]:
        if not self.raw.is_dir():
            raise FileNotFoundError(f"资料文件夹不可访问: {self.raw}")
        files = _iter_source_files(self.raw)
        discovered_paths = {path.relative_to(self.raw).as_posix() for path in files}
        seen: set[str] = set()
        indexed = 0
        unchanged = 0
        failed = 0
        with closing(self._connect()) as connection:
            existing = {
                str(row["relative_path"]): row
                for row in connection.execute("SELECT * FROM documents").fetchall()
            }
            movable_by_hash: dict[str, list[sqlite3.Row]] = {}
            for relative_path, row in existing.items():
                if relative_path not in discovered_paths:
                    movable_by_hash.setdefault(str(row["content_hash"]), []).append(row)
            for path in files:
                relative = path.relative_to(self.raw).as_posix()
                seen.add(relative)
                stat = path.stat()
                current = existing.get(relative)
                if (
                    current is not None
                    and int(current["size_bytes"]) == stat.st_size
                    and int(current["mtime_ns"]) == stat.st_mtime_ns
                ):
                    unchanged += 1
                    continue
                content_hash = sha256_file(path)
                document_id = str(current["document_id"]) if current else ""
                if not document_id:
                    moved_candidates = movable_by_hash.get(content_hash, [])
                    moved = moved_candidates.pop(0) if moved_candidates else None
                    document_id = str(moved["document_id"]) if moved else str(uuid.uuid4())
                try:
                    extracted = extract_source(path)
                    connection.execute("SAVEPOINT replace_document")
                    self._replace_document(
                        connection,
                        document_id=document_id,
                        relative_path=relative,
                        path=path,
                        content_hash=content_hash,
                        size_bytes=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                        modified_at=_modified_at(stat),
                        extracted=extracted,
                    )
                    connection.execute("RELEASE SAVEPOINT replace_document")
                    indexed += 1
                except Exception as error:
                    try:
                        connection.execute("ROLLBACK TO SAVEPOINT replace_document")
                        connection.execute("RELEASE SAVEPOINT replace_document")
                    except sqlite3.OperationalError:
                        pass
                    self._replace_failed_document(
                        connection,
                        document_id=document_id,
                        relative_path=relative,
                        path=path,
                        content_hash=content_hash,
                        size_bytes=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                        modified_at=_modified_at(stat),
                        error=error,
                    )
                    failed += 1
            removed = sorted(set(existing) - seen)
            for relative in removed:
                document_id = str(existing[relative]["document_id"])
                current_path = connection.execute(
                    "SELECT relative_path FROM documents WHERE document_id = ?", (document_id,)
                ).fetchone()
                if current_path is not None and str(current_path["relative_path"]) != relative:
                    continue
                self._delete_document(connection, document_id)
            now = utc_now()
            connection.execute(
                "INSERT INTO workspace_metadata(key, value) VALUES('last_sync_at', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (now,),
            )
            connection.execute(
                "INSERT INTO workspace_metadata(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (WORKSPACE_SCHEMA_VERSION,),
            )
            connection.commit()
        return {
            "workspace_id": self.workspace.workspace_id,
            "discovered": len(files),
            "indexed": indexed,
            "unchanged": unchanged,
            "removed": len(removed),
            "failed": failed,
            "health": self.health().model_dump(mode="json"),
        }

    def _delete_document(self, connection: sqlite3.Connection, document_id: str) -> None:
        rows = connection.execute(
            "SELECT passage_id FROM passages WHERE document_id = ?", (document_id,)
        ).fetchall()
        for row in rows:
            connection.execute("DELETE FROM passages_fts WHERE rowid = ?", (int(row[0]),))
        connection.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))

    def _replace_document(
        self,
        connection: sqlite3.Connection,
        *,
        document_id: str,
        relative_path: str,
        path: Path,
        content_hash: str,
        size_bytes: int,
        mtime_ns: int,
        modified_at: str,
        extracted: ExtractedSource,
    ) -> None:
        if connection.execute(
            "SELECT 1 FROM documents WHERE document_id = ?", (document_id,)
        ).fetchone():
            self._delete_document(connection, document_id)
        readable_pages = [
            page for page in extracted.pages if page.quality_score >= READABLE_THRESHOLD
        ]
        scores = [page.quality_score for page in extracted.pages]
        document_score = sum(scores) / len(scores) if scores else 0.0
        readability = readability_label(document_score)
        if extracted.status == "metadata_only":
            readability = "low"
        overview = ""
        if extracted.pages and len(readable_pages) / len(extracted.pages) >= 0.70:
            overview = _excerpt(readable_pages[0].text, "", 260)
        connection.execute(
            """
            INSERT INTO documents(
                document_id, relative_path, name, extension, content_hash, size_bytes, mtime_ns,
                modified_at, title, overview, page_count, readability, readability_score,
                indexing_state, error, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                relative_path,
                path.name,
                path.suffix.casefold(),
                content_hash,
                size_bytes,
                mtime_ns,
                modified_at,
                extracted.title,
                overview,
                extracted.page_count,
                readability,
                round(document_score, 4),
                extracted.status,
                extracted.error,
                utc_now(),
            ),
        )
        for page in extracted.pages:
            connection.execute(
                "INSERT INTO pages("
                "document_id, page_number, text, extraction_method, quality_score, readability"
                ") "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    page.page_number,
                    page.text,
                    page.extraction_method,
                    page.quality_score,
                    page.readability,
                ),
            )
            if page.quality_score < PARTIAL_THRESHOLD or not page.text.strip():
                continue
            heading = next(
                (line.strip() for line in page.text.splitlines() if line.strip()), ""
            )[:200]
            for ordinal, chunk in enumerate(_passage_chunks(page.text)):
                passage = connection.execute(
                    "INSERT INTO passages(document_id, page_number, ordinal, heading, text) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (document_id, page.page_number, ordinal, heading, chunk),
                )
                if passage.lastrowid is None:
                    raise RuntimeError("Unable to allocate passage ID")
                passage_id = passage.lastrowid
                connection.execute(
                    "INSERT INTO passages_fts("
                    "rowid, document_id, page_number, name, path, heading, body, tokens"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        passage_id,
                        document_id,
                        page.page_number,
                        path.name,
                        relative_path,
                        heading,
                        chunk,
                        _fts_text(" ".join([path.name, relative_path, heading, chunk])),
                    ),
                )

    def _replace_failed_document(
        self,
        connection: sqlite3.Connection,
        *,
        document_id: str,
        relative_path: str,
        path: Path,
        content_hash: str,
        size_bytes: int,
        mtime_ns: int,
        modified_at: str,
        error: Exception,
    ) -> None:
        if connection.execute(
            "SELECT 1 FROM documents WHERE document_id = ?", (document_id,)
        ).fetchone():
            self._delete_document(connection, document_id)
        connection.execute(
            """
            INSERT INTO documents(
                document_id, relative_path, name, extension, content_hash, size_bytes, mtime_ns,
                modified_at, title, overview, page_count, readability, readability_score,
                indexing_state, error, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', 0, 'low', 0, 'failed', ?, ?)
            """,
            (
                document_id,
                relative_path,
                path.name,
                path.suffix.casefold(),
                content_hash,
                size_bytes,
                mtime_ns,
                modified_at,
                path.stem,
                f"{type(error).__name__}: {error}"[:500],
                utc_now(),
            ),
        )

    def health(self) -> WorkspaceHealth:
        if not self.database.exists():
            return WorkspaceHealth()
        with closing(self._connect()) as connection:
            counts = {
                str(row["readability"]): int(row["count"])
                for row in connection.execute(
                    "SELECT readability, COUNT(*) AS count FROM documents GROUP BY readability"
                ).fetchall()
            }
            metadata_only = int(
                connection.execute(
                    "SELECT COUNT(*) FROM documents WHERE indexing_state = 'metadata_only'"
                ).fetchone()[0]
            )
            failed = int(
                connection.execute(
                    "SELECT COUNT(*) FROM documents WHERE indexing_state = 'failed'"
                ).fetchone()[0]
            )
            last_sync = connection.execute(
                "SELECT value FROM workspace_metadata WHERE key = 'last_sync_at'"
            ).fetchone()
            return WorkspaceHealth(
                document_count=sum(counts.values()),
                readable_count=counts.get("readable", 0),
                partial_count=counts.get("partial", 0),
                low_quality_count=counts.get("low", 0),
                metadata_only_count=metadata_only,
                failed_count=failed,
                last_sync_at=str(last_sync[0]) if last_sync else "",
            )

    def payload(self) -> WorkspacePayload:
        return WorkspacePayload(
            workspace_id=self.workspace.workspace_id,
            name=self.workspace.name,
            raw_path=str(self.raw),
            available=self.raw.is_dir(),
            enabled=self.workspace.enabled,
            vision_enabled=self.workspace.vision_enabled,
            legacy_index_present=bool(
                self.workspace.legacy_index_path and Path(self.workspace.legacy_index_path).exists()
            ),
            health=self.health(),
        )

    def list_documents(self) -> list[WorkspaceDocument]:
        if not self.database.exists():
            return []
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM documents ORDER BY relative_path COLLATE NOCASE"
            ).fetchall()
        return [self._document_payload(row) for row in rows]

    def get_document(self, document_id: str) -> WorkspaceDocument:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        if row is None:
            raise FileNotFoundError("Document not found")
        return self._document_payload(row)

    def _document_payload(self, row: sqlite3.Row) -> WorkspaceDocument:
        source = self._source_path(str(row["relative_path"]))
        return WorkspaceDocument(
            document_id=str(row["document_id"]),
            name=str(row["name"]),
            relative_path=str(row["relative_path"]),
            extension=str(row["extension"]),
            content_hash=str(row["content_hash"]),
            size_bytes=int(row["size_bytes"]),
            modified_at=str(row["modified_at"]),
            title=str(row["title"]),
            overview=str(row["overview"]),
            page_count=int(row["page_count"]),
            readability=_readability_value(row["readability"]),
            readability_score=float(row["readability_score"]),
            indexing_state=_indexing_state_value(row["indexing_state"]),
            error=str(row["error"]),
            source_uri=source.as_uri(),
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 30,
        mode: Literal["local", "assisted"] = "local",
        path_prefix: str = "",
        extensions: list[str] | None = None,
    ) -> WorkspaceSearchReport:
        started = time.perf_counter()
        value = query.strip()
        if not value:
            raise ValueError("Search query cannot be empty")
        normalized_query = normalize_search_text(value)
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT * FROM documents").fetchall()
            documents = {str(row["document_id"]): row for row in rows}
            candidates: dict[str, tuple[int, float, list[WorkspaceEvidence]]] = {}
            allowed_extensions = {item.casefold() for item in (extensions or [])}
            for document_id, row in documents.items():
                relative_path = str(row["relative_path"])
                extension = str(row["extension"])
                if path_prefix and not relative_path.casefold().startswith(path_prefix.casefold()):
                    continue
                if allowed_extensions and extension not in allowed_extensions:
                    continue
                filename = normalize_search_text(str(row["name"]))
                stem = normalize_search_text(Path(str(row["name"])).stem)
                title = normalize_search_text(str(row["title"]))
                path_text = normalize_search_text(relative_path)
                tier: int | None = None
                reason = ""
                if normalized_query in {filename, stem}:
                    tier, reason = 0, "文件名与查询完全一致"
                    metadata_score = 0.0
                elif normalized_query in filename:
                    tier, reason = 1, "文件名包含查询内容"
                    metadata_score = max(0.0, (len(stem) - len(normalized_query)) / 100.0)
                elif normalized_query and SequenceMatcher(
                    None, normalized_query, stem
                ).ratio() >= 0.72:
                    tier, reason = 1, "文件名与查询内容相近"
                    metadata_score = 1.0 - SequenceMatcher(
                        None, normalized_query, stem
                    ).ratio()
                elif normalized_query in title:
                    tier, reason = 2, "文档标题包含查询内容"
                    metadata_score = 0.0
                elif normalized_query in path_text:
                    tier, reason = 2, "文件路径包含查询内容"
                    metadata_score = 0.2
                if tier is not None:
                    evidence = WorkspaceEvidence(
                        page_number=None,
                        excerpt=str(row["overview"]) or str(row["name"]),
                        reason=reason,
                        quality_score=float(row["readability_score"]),
                    )
                    candidates[document_id] = (tier, metadata_score, [evidence])
            tokens = search_terms(value)
            if tokens:
                quoted_primary = [
                    f'"{term.replace(chr(34), chr(34) * 2)}"'
                    for term in normalized_query.split()
                ]
                fallback_expression = " OR ".join(
                    f'"{term.replace(chr(34), chr(34) * 2)}"' for term in tokens
                )
                expressions = [" AND ".join(quoted_primary)] if quoted_primary else []
                if fallback_expression not in expressions:
                    expressions.append(fallback_expression)
                matches: list[sqlite3.Row] = []
                for expression in expressions:
                    try:
                        matches = connection.execute(
                            "SELECT rowid, document_id, page_number, heading, body, "
                            "bm25(passages_fts) AS score "
                            "FROM passages_fts WHERE passages_fts MATCH ? "
                            "ORDER BY score LIMIT 250",
                            (expression,),
                        ).fetchall()
                    except sqlite3.OperationalError:
                        matches = []
                    if matches:
                        break
                for match in matches:
                    document_id = str(match["document_id"])
                    if document_id not in documents:
                        continue
                    row = documents[document_id]
                    relative_path = str(row["relative_path"])
                    extension = str(row["extension"])
                    if path_prefix and not relative_path.casefold().startswith(
                        path_prefix.casefold()
                    ):
                        continue
                    if allowed_extensions and extension not in allowed_extensions:
                        continue
                    heading = str(match["heading"])
                    body = str(match["body"])
                    heading_match = normalized_query and normalized_query in normalize_search_text(
                        heading
                    )
                    tier = 2 if heading_match else 3
                    reason = "章节标题包含查询内容" if heading_match else "正文包含查询内容"
                    evidence = WorkspaceEvidence(
                        page_number=(
                            int(match["page_number"])
                            if match["page_number"] is not None
                            else None
                        ),
                        heading=heading if heading_match else "",
                        excerpt=_excerpt(body, value),
                        reason=reason,
                        quality_score=float(row["readability_score"]),
                    )
                    score = float(match["score"])
                    current = candidates.get(document_id)
                    if current is None:
                        candidates[document_id] = (tier, score, [evidence])
                    else:
                        best_tier, best_score, evidence_items = current
                        evidence_items.append(evidence)
                        if tier < best_tier:
                            merged_tier, merged_score = tier, score
                        elif tier == best_tier:
                            merged_tier, merged_score = best_tier, min(best_score, score)
                        else:
                            merged_tier, merged_score = best_tier, best_score
                        candidates[document_id] = (
                            merged_tier,
                            merged_score,
                            evidence_items,
                        )
            ordered = sorted(
                candidates.items(),
                key=lambda item: (
                    item[1][0],
                    item[1][1],
                    str(documents[item[0]]["name"]).casefold(),
                ),
            )[:limit]
            results: list[WorkspaceSearchResult] = []
            for rank, (document_id, (candidate_tier, _, evidence_items)) in enumerate(
                ordered, start=1
            ):
                row = documents[document_id]
                unique: list[WorkspaceEvidence] = []
                seen_evidence: set[tuple[int | None, str]] = set()
                for evidence in evidence_items:
                    key = (evidence.page_number, evidence.excerpt)
                    if key in seen_evidence:
                        continue
                    seen_evidence.add(key)
                    unique.append(evidence)
                best = unique[0]
                additional = unique[1:3]
                paged_evidence = next(
                    (evidence for evidence in unique if evidence.page_number is not None), None
                )
                if candidate_tier <= 1 and best.page_number is None and paged_evidence is not None:
                    best = paged_evidence.model_copy(
                        update={"reason": f"{best.reason}；{paged_evidence.reason}"}
                    )
                    additional = [
                        evidence
                        for evidence in unique
                        if evidence is not paged_evidence and evidence.page_number is not None
                    ][:2]
                source = self._source_path(str(row["relative_path"]))
                results.append(
                    WorkspaceSearchResult(
                        document_id=document_id,
                        name=str(row["name"]),
                        relative_path=str(row["relative_path"]),
                        extension=str(row["extension"]),
                        content_hash=str(row["content_hash"]),
                        size_bytes=int(row["size_bytes"]),
                        modified_at=str(row["modified_at"]),
                        page_count=int(row["page_count"]),
                        readability=_readability_value(row["readability"]),
                        readability_score=float(row["readability_score"]),
                        source_uri=source.as_uri(),
                        overview=str(row["overview"]),
                        best_evidence=best,
                        additional_evidence=additional,
                        rank=rank,
                    )
                )
        actual_mode: Literal["local", "assisted", "degraded"] = "local"
        degradation_reason = ""
        answer = (
            f"找到 {len(results)} 份相关资料。"
            if results
            else "当前资料空间没有找到匹配内容。"
        )
        if mode == "assisted":
            try:
                results, assisted_answer = assisted_rerank(self.workspace, value, results)
                actual_mode = "assisted"
                if assisted_answer:
                    answer = assisted_answer
            except RuntimeError as error:
                actual_mode = "degraded"
                degradation_reason = str(error) or "ai_unavailable"
            except Exception:
                actual_mode = "degraded"
                degradation_reason = "ai_unavailable"
        duration_ms = max(0, math.ceil((time.perf_counter() - started) * 1000))
        return WorkspaceSearchReport(
            query=value,
            requested_mode=mode,
            actual_mode=actual_mode,
            degradation_reason=degradation_reason,
            answer=answer,
            results=results,
            candidate_count=len(candidates),
            duration_ms=duration_ms,
        )

    def preview_path(self, document_id: str, page_number: int) -> Path:
        if page_number < 1:
            raise ValueError("Page number must be positive")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT relative_path, content_hash, extension, page_count FROM documents "
                "WHERE document_id = ?",
                (document_id,),
            ).fetchone()
        if row is None:
            raise FileNotFoundError("Document not found")
        if str(row["extension"]) != ".pdf":
            raise ValueError("Page preview is available only for PDF documents")
        if page_number > int(row["page_count"]):
            raise FileNotFoundError("Page not found")
        destination = self.previews / str(row["content_hash"]) / f"{page_number}.png"
        if destination.exists():
            return destination
        import pypdfium2 as pdfium

        source = self._source_path(str(row["relative_path"]))
        document = pdfium.PdfDocument(str(source))
        try:
            page = document[page_number - 1]
            bitmap = page.render(scale=1.8)
            try:
                image = bitmap.to_pil()
                destination.parent.mkdir(parents=True, exist_ok=True)
                image.save(destination, format="PNG", optimize=True)
            finally:
                bitmap.close()
                page.close()
        finally:
            document.close()
        return destination

    def reprocess_document(self, document_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT relative_path FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            if row is None:
                raise FileNotFoundError("Document not found")
            relative_path = str(row["relative_path"])
            connection.execute(
                "UPDATE documents SET modified_at = '' WHERE document_id = ?", (document_id,)
            )
            connection.commit()
        result = self.sync()
        result["reprocessed_document_id"] = document_id
        result["relative_path"] = relative_path
        return result


def list_workspace_payloads() -> list[WorkspacePayload]:
    workspaces = ensure_v2_workspaces()
    return [WorkspaceStore(item).payload() for item in workspaces.values()]
