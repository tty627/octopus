from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Literal

from .config import load_repository_config, load_repository_state, octopus_dir
from .models import (
    AIUsage,
    ExtractionEvidence,
    GeneratedSearchAnswer,
    SearchCitation,
    SearchDocument,
    SearchMatchEvidence,
    SearchReport,
    SearchResult,
)
from .providers import (
    HeuristicProvider,
    ProviderAuthError,
    ProviderBudgetError,
    ProviderError,
    ProviderOutputError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderTransientError,
    create_provider,
)
from .rendering import read_machine_header

LATIN_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
CITATION_MARKER = re.compile(r"\[S(\d+)\]")
SQLITE_ID_BATCH_SIZE = 1_000
SEARCH_SCHEMA_VERSION = "0.5"
SEARCH_ALGORITHM_VERSION = "octopus-0.5-local-v2-explain"
SEARCH_REPORT_SCHEMA_VERSION = "1.0"
SearchField = Literal["name", "path", "summary", "keywords", "evidence", "body"]
MATCH_FIELD_LOCATORS: dict[SearchField, str] = {
    "name": "summary_layer.name",
    "path": "source.raw_relative_path",
    "summary": "summary_layer.one_sentence_summary",
    "keywords": "summary_layer.tags_and_keywords",
    "evidence": "attachment_card_layer.extraction_evidence",
    "body": "markdown_index.body",
}

ALLOWED_STATUSES = (
    "clean",
    "indexed",
    "stale",
    "pending_edit",
    "pending_stable",
    "failed",
    "retry",
)
STATUS_PENALTIES = {
    "stale": 2.0,
    "pending_edit": 3.0,
    "pending_stable": 3.0,
    "failed": 4.0,
    "retry": 4.0,
}
REASON_LABELS = {
    "exact_name": "文件名与查询直接匹配",
    "exact_path": "文件路径与查询直接匹配",
    "exact_summary": "摘要与查询直接匹配",
    "name_term_match": "文件名包含查询词",
    "path_term_match": "路径包含查询词",
    "summary_term_match": "摘要包含查询词",
    "keyword_match": "主题词或标签匹配",
    "evidence_match": "解析证据包含查询词",
    "body_match": "索引正文包含查询词",
    "all_terms_matched": "全部查询词均有匹配",
    "direct_file_result": "结果直接对应文件",
}


def analyze_terms(text: str) -> list[str]:
    terms = {match.group(0).casefold() for match in LATIN_WORD.finditer(text)}
    for match in CJK_RUN.finditer(text):
        run = match.group(0)
        if len(run) == 1:
            terms.add(run)
            continue
        for size in (2, 3):
            for index in range(max(0, len(run) - size + 1)):
                terms.add(run[index : index + size])
    return sorted(terms)


def _evidence_text(evidence: list[ExtractionEvidence]) -> str:
    return " ".join(f"{item.locator} {item.kind} {item.text_excerpt}" for item in evidence)


def searchable_text(document: SearchDocument) -> str:
    values = [
        document.name,
        document.raw_relative_path,
        document.summary,
        document.description,
        " ".join(document.tags),
        " ".join(document.keywords),
        _evidence_text(document.evidence),
        document.body_excerpt,
    ]
    return " ".join(analyze_terms("\n".join(values)))


def _valid_node_ids(node_ids: list[str], allowed: dict[str, SearchResult]) -> list[str]:
    valid: list[str] = []
    for node_id in node_ids:
        if node_id in allowed and node_id not in valid:
            valid.append(node_id)
    return valid


def _evidence_excerpt(text: str, matched_terms: list[str], limit: int = 240) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    folded = compact.casefold()
    offsets = [folded.find(term.casefold()) for term in matched_terms]
    first = min((offset for offset in offsets if offset >= 0), default=0)
    start = max(0, first - limit // 3)
    end = min(len(compact), start + limit)
    start = max(0, end - limit)
    return ("…" if start else "") + compact[start:end] + ("…" if end < len(compact) else "")


def _evidence(payload: object) -> list[ExtractionEvidence]:
    if not isinstance(payload, list):
        return []
    values: list[ExtractionEvidence] = []
    for item in payload[:100]:
        try:
            values.append(ExtractionEvidence.model_validate(item))
        except (TypeError, ValueError):
            continue
    return values


class SearchIndex:
    def __init__(self, index_repository: Path) -> None:
        self.index = index_repository.resolve()
        self.database = octopus_dir(self.index) / "search.sqlite3"
        self.config = load_repository_config(self.index)

    @staticmethod
    def _connect_path(path: Path) -> sqlite3.Connection:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _connect(self) -> sqlite3.Connection:
        return self._connect_path(self.database)

    def _manifest_generation(self) -> str:
        state = load_repository_state(self.index, self.config)
        return str(state.scan.scan_generation)

    def _documents_from_path(self, path: Path) -> list[SearchDocument]:
        try:
            header, body = read_machine_header(path)
        except (OSError, ValueError, json.JSONDecodeError):
            return []
        index_type = header.get("schema", {}).get("index_type")
        if index_type not in {"leaf", "foldernode"}:
            return []
        layer = header.get("summary_layer", {})
        update = header.get("update_control", {})
        documents: list[SearchDocument] = []
        if index_type == "leaf":
            card = header.get("attachment_card_layer", {})
            source = card.get("source", {})
            metadata = card.get("metadata", {})
            extraction = header.get("extraction_policy", {})
            node_id = str(source.get("source_id", ""))
            if not node_id:
                return []
            documents.append(
                SearchDocument(
                    node_id=node_id,
                    index_type="leaf",
                    index_path=str(path.resolve()),
                    raw_relative_path=str(source.get("raw_relative_path", "")),
                    name=str(layer.get("name", path.stem)),
                    summary=str(layer.get("one_sentence_summary", "")),
                    description=str(layer.get("description", "")),
                    tags=list(layer.get("tag_rough", [])),
                    keywords=list(layer.get("topic_keywords", [])),
                    body_excerpt=body[:8_000],
                    status=str(update.get("index_status", "clean")),
                    source_uri=str(metadata.get("file_uri", "")),
                    evidence=_evidence(card.get("extraction_evidence", [])),
                    quality_flags=list(layer.get("quality_flags", [])),
                    truncated=bool(extraction.get("truncated", False)),
                )
            )
            return documents

        card = header.get("folder_card_layer", {})
        source = card.get("source", {})
        metadata = card.get("metadata", {})
        node_id = str(source.get("folder_id", ""))
        if not node_id:
            return []
        folder_evidence = [
            ExtractionEvidence(
                locator="summary_layer.one_sentence_summary",
                kind="index_summary",
                text_excerpt=str(layer.get("one_sentence_summary", ""))[:500],
            )
        ]
        documents.append(
            SearchDocument(
                node_id=node_id,
                index_type="foldernode",
                index_path=str(path.resolve()),
                raw_relative_path=str(source.get("raw_relative_path", "")),
                name=str(layer.get("name", path.stem)),
                summary=str(layer.get("one_sentence_summary", "")),
                description=str(layer.get("description", "")),
                tags=list(layer.get("tag_rough", [])),
                keywords=list(layer.get("topic_keywords", [])),
                body_excerpt=body[:8_000],
                status=str(update.get("index_status", "clean")),
                source_uri=str(metadata.get("folder_uri", "")),
                evidence=folder_evidence,
                quality_flags=list(layer.get("quality_flags", [])),
            )
        )
        raw_root = Path(self.config.repository.raw_repository_path)
        children = header.get("children_summary_layer", {}).get("direct_children", [])
        for child in children:
            if not isinstance(child, dict) or child.get("node_type") != "file":
                continue
            child_id = str(child.get("child_id", ""))
            relative = str(child.get("relative_name_or_path", ""))
            if not child_id or not relative:
                continue
            uri = str(child.get("source_uri", ""))
            if not uri:
                uri = (raw_root / Path(relative.replace("/", os.sep))).resolve().as_uri()
            child_summary = str(child.get("one_sentence_summary", ""))
            child_evidence = _evidence(child.get("extraction_evidence", []))
            if not child_evidence and child_summary:
                child_evidence = [
                    ExtractionEvidence(
                        locator="children_summary_layer.direct_children",
                        kind="index_summary",
                        text_excerpt=child_summary[:500],
                    )
                ]
            documents.append(
                SearchDocument(
                    node_id=child_id,
                    index_type="text",
                    index_path=str(path.resolve()),
                    raw_relative_path=relative,
                    name=str(child.get("name", Path(relative).name)),
                    summary=child_summary,
                    description=str(child.get("description", child_summary)),
                    tags=list(child.get("tag_rough", [])),
                    keywords=list(child.get("topic_keywords", [])),
                    status=str(child.get("index_status", "clean")),
                    source_uri=uri,
                    evidence=child_evidence,
                    quality_flags=list(child.get("quality_flags", [])),
                    truncated=bool(child.get("truncated", False)),
                )
            )
        return documents

    def _iter_documents(self) -> list[SearchDocument]:
        documents: list[SearchDocument] = []
        for path in self.index.rglob("*.md"):
            if octopus_dir(self.index) == path or octopus_dir(self.index) in path.parents:
                continue
            documents.extend(self._documents_from_path(path))
        return documents

    @staticmethod
    def _metadata(connection: sqlite3.Connection) -> dict[str, str]:
        try:
            rows = connection.execute("SELECT key, value FROM search_metadata").fetchall()
        except sqlite3.Error:
            return {}
        return {str(row["key"]): str(row["value"]) for row in rows}

    def _cache_structure_is_current(self) -> bool:
        if not self.database.exists():
            return False
        try:
            with closing(self._connect()) as connection:
                metadata = self._metadata(connection)
            return metadata.get("schema_version") == SEARCH_SCHEMA_VERSION and metadata.get(
                "algorithm_version"
            ) == SEARCH_ALGORITHM_VERSION
        except sqlite3.Error:
            return False

    def _cache_is_current(self) -> bool:
        if not self._cache_structure_is_current():
            return False
        try:
            with closing(self._connect()) as connection:
                metadata = self._metadata(connection)
            return metadata.get("manifest_generation") == self._manifest_generation()
        except (OSError, ValueError, sqlite3.Error):
            return False

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE search_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE documents (
                node_id TEXT PRIMARY KEY,
                index_type TEXT NOT NULL,
                index_path TEXT NOT NULL,
                raw_relative_path TEXT NOT NULL,
                name TEXT NOT NULL,
                summary TEXT NOT NULL,
                description TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                keywords_json TEXT NOT NULL,
                body_excerpt TEXT NOT NULL,
                status TEXT NOT NULL,
                source_uri TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                quality_flags_json TEXT NOT NULL,
                truncated INTEGER NOT NULL
            );
            CREATE INDEX documents_index_path ON documents(index_path);
            CREATE VIRTUAL TABLE document_fts USING fts5(
                node_id UNINDEXED,
                name_terms,
                path_terms,
                summary_terms,
                keyword_terms,
                evidence_terms,
                body_terms,
                tokenize='unicode61 remove_diacritics 2'
            );
            """
        )

    @staticmethod
    def _delete_node(connection: sqlite3.Connection, node_id: str) -> None:
        connection.execute("DELETE FROM document_fts WHERE node_id = ?", (node_id,))
        connection.execute("DELETE FROM documents WHERE node_id = ?", (node_id,))

    @classmethod
    def _upsert_document(cls, connection: sqlite3.Connection, document: SearchDocument) -> None:
        cls._delete_node(connection, document.node_id)
        connection.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                document.node_id,
                document.index_type,
                document.index_path,
                document.raw_relative_path,
                document.name,
                document.summary,
                document.description,
                json.dumps(document.tags, ensure_ascii=False),
                json.dumps(document.keywords, ensure_ascii=False),
                document.body_excerpt,
                document.status,
                document.source_uri,
                json.dumps(
                    [item.model_dump(mode="json", exclude_none=True) for item in document.evidence],
                    ensure_ascii=False,
                ),
                json.dumps(document.quality_flags, ensure_ascii=False),
                int(document.truncated),
            ),
        )
        connection.execute(
            "INSERT INTO document_fts VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                document.node_id,
                " ".join(analyze_terms(document.name)),
                " ".join(analyze_terms(document.raw_relative_path)),
                " ".join(analyze_terms(document.summary + " " + document.description)),
                " ".join(analyze_terms(" ".join(document.tags + document.keywords))),
                " ".join(analyze_terms(_evidence_text(document.evidence))),
                " ".join(analyze_terms(document.body_excerpt)),
            ),
        )

    @staticmethod
    def _set_metadata(connection: sqlite3.Connection, generation: str) -> None:
        values = {
            "schema_version": SEARCH_SCHEMA_VERSION,
            "algorithm_version": SEARCH_ALGORITHM_VERSION,
            "manifest_generation": generation,
        }
        connection.executemany(
            "INSERT OR REPLACE INTO search_metadata(key, value) VALUES (?, ?)", values.items()
        )

    def rebuild(self) -> int:
        documents = self._iter_documents()
        temporary = self.database.with_suffix(".sqlite3.rebuild")
        temporary.unlink(missing_ok=True)
        with closing(self._connect_path(temporary)) as connection:
            self._create_schema(connection)
            for document in documents:
                self._upsert_document(connection, document)
            self._set_metadata(connection, self._manifest_generation())
            connection.commit()
        os.replace(temporary, self.database)
        return len(documents)

    def refresh(
        self,
        changed_paths: list[Path],
        deleted_paths: list[Path],
        *,
        manifest_generation: str | None = None,
    ) -> dict[str, int | str]:
        if not self._cache_structure_is_current():
            count = self.rebuild()
            return {"mode": "rebuild", "upserted": count, "deleted": 0}
        changed = list(dict.fromkeys(str(path.resolve()) for path in changed_paths))
        deleted = list(dict.fromkeys(str(path.resolve()) for path in deleted_paths))
        removed = 0
        upserted = 0
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                for value in [*deleted, *changed]:
                    rows = connection.execute(
                        "SELECT node_id FROM documents WHERE index_path = ?", (value,)
                    ).fetchall()
                    for row in rows:
                        self._delete_node(connection, str(row["node_id"]))
                        if value in deleted:
                            removed += 1
                for value in changed:
                    path = Path(value)
                    if not path.exists():
                        continue
                    for document in self._documents_from_path(path):
                        self._upsert_document(connection, document)
                        upserted += 1
                self._set_metadata(
                    connection, manifest_generation or self._manifest_generation()
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {"mode": "incremental", "upserted": upserted, "deleted": removed}

    @staticmethod
    def _row_document(row: sqlite3.Row) -> SearchDocument:
        return SearchDocument(
            node_id=row["node_id"],
            index_type=row["index_type"],
            index_path=row["index_path"],
            raw_relative_path=row["raw_relative_path"],
            name=row["name"],
            summary=row["summary"],
            description=row["description"],
            tags=json.loads(row["tags_json"]),
            keywords=json.loads(row["keywords_json"]),
            body_excerpt=row["body_excerpt"],
            status=row["status"],
            source_uri=row["source_uri"],
            evidence=_evidence(json.loads(row["evidence_json"])),
            quality_flags=json.loads(row["quality_flags_json"]),
            truncated=bool(row["truncated"]),
        )

    def search(self, query: str, limit: int = 20, include_stale: bool = True) -> list[SearchResult]:
        if not self._cache_is_current():
            self.rebuild()
        terms = analyze_terms(query)
        if not terms:
            return []
        expression = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
        allowed = list(ALLOWED_STATUSES if include_stale else ("clean", "indexed"))
        placeholders = ",".join("?" for _ in allowed)
        sql = f"""
            SELECT d.*, bm25(document_fts, 0.0, 12.0, 8.0, 6.0, 4.0, 3.0, 1.0) AS fts_rank
            FROM document_fts
            JOIN documents d ON d.node_id = document_fts.node_id
            WHERE document_fts MATCH ? AND d.status IN ({placeholders})
            ORDER BY fts_rank
            LIMIT ?
        """
        candidate_limit = min(500, max(limit * 5, 50))
        with closing(self._connect()) as connection:
            rows = connection.execute(sql, (expression, *allowed, candidate_limit)).fetchall()
        query_terms = set(terms)
        normalized_query = query.casefold().strip()
        results: list[SearchResult] = []
        for row in rows:
            document = self._row_document(row)
            fields: dict[SearchField, str] = {
                "name": document.name,
                "path": document.raw_relative_path,
                "summary": document.summary + " " + document.description,
                "keywords": " ".join(document.tags + document.keywords),
                "evidence": _evidence_text(document.evidence),
                "body": document.body_excerpt,
            }
            field_terms = {name: set(analyze_terms(value)) for name, value in fields.items()}
            document_terms = set().union(*field_terms.values())
            matched = sorted(query_terms & document_terms)
            coverage = len(matched) / max(1, len(query_terms))
            reasons: list[str] = []
            matched_fields: list[str] = []
            match_evidence: list[SearchMatchEvidence] = []
            score = -float(row["fts_rank"]) + coverage * 4.0
            if normalized_query and normalized_query in document.name.casefold():
                reasons.append("exact_name")
                score += 12.0
            if normalized_query and normalized_query in document.raw_relative_path.casefold():
                reasons.append("exact_path")
                score += 9.0
            if normalized_query and normalized_query in document.summary.casefold():
                reasons.append("exact_summary")
                score += 5.0
            field_reasons: dict[SearchField, tuple[str, float]] = {
                "name": ("name_term_match", 5.0),
                "path": ("path_term_match", 3.5),
                "summary": ("summary_term_match", 3.0),
                "keywords": ("keyword_match", 2.5),
                "evidence": ("evidence_match", 2.0),
                "body": ("body_match", 1.0),
            }
            for field_name, (reason, weight) in field_reasons.items():
                field_matches = sorted(query_terms & field_terms[field_name])
                field_coverage = len(field_matches) / max(1, len(query_terms))
                if field_coverage:
                    matched_fields.append(field_name)
                    reasons.append(reason)
                    score += field_coverage * weight
                    match_evidence.append(
                        SearchMatchEvidence(
                            field=field_name,
                            locator=MATCH_FIELD_LOCATORS[field_name],
                            excerpt=_evidence_excerpt(fields[field_name], field_matches),
                            matched_terms=field_matches,
                        )
                    )
            if coverage == 1.0:
                reasons.append("all_terms_matched")
                score += 1.0
            if document.index_type in {"text", "leaf"}:
                reasons.append("direct_file_result")
                score += 5.0
            risk_flags: list[str] = []
            if document.status not in {"clean", "indexed"}:
                risk_flags.append(f"status:{document.status}")
                score -= STATUS_PENALTIES.get(document.status, 2.0)
            if document.status == "stale":
                risk_flags.append("stale_index")
            for flag in document.quality_flags:
                risk_flags.append(f"quality:{flag}")
            score -= min(2.0, len(document.quality_flags) * 0.5)
            if document.truncated:
                risk_flags.append("truncated")
                score -= 1.5
            if not document.source_uri:
                risk_flags.append("missing_source_uri")
            if document.index_type == "foldernode":
                risk_flags.append("folder_aggregate")
            unique_reasons = list(dict.fromkeys(reasons))
            explanation = "；".join(
                REASON_LABELS[reason]
                for reason in unique_reasons
                if reason in REASON_LABELS
            )
            recommended_open_target: Literal["index", "source"] = (
                "source" if document.index_type == "text" else "index"
            )
            open_target_uri = (
                document.source_uri
                if recommended_open_target == "source" and document.source_uri
                else Path(document.index_path).resolve().as_uri()
            )
            results.append(
                SearchResult(
                    **document.model_dump(),
                    score=score,
                    matched_terms=matched,
                    matched_fields=matched_fields,
                    match_reasons=unique_reasons,
                    match_evidence=match_evidence,
                    explanation=explanation,
                    risk_flags=risk_flags,
                    recommended_open_target=recommended_open_target,
                    open_target_uri=open_target_uri,
                )
            )
        results.sort(key=lambda item: (-item.score, item.name.casefold(), item.node_id))
        for rank, result in enumerate(results[:limit], start=1):
            result.rank = rank
        return results[:limit]

    def _expanded_results(self, query: str, limit: int) -> list[SearchResult]:
        results = self.search(query, limit=limit)
        child_ids: list[str] = []
        for result in results:
            if result.index_type != "foldernode":
                continue
            try:
                header, _ = read_machine_header(Path(result.index_path))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            for child in header.get("children_summary_layer", {}).get("direct_children", []):
                child_id = str(child.get("child_id", ""))
                if child_id:
                    child_ids.append(child_id)
        existing = {item.node_id for item in results}
        missing_ids = list(
            dict.fromkeys(node_id for node_id in child_ids if node_id not in existing)
        )
        if missing_ids and self.database.exists():
            rows: list[sqlite3.Row] = []
            with closing(self._connect()) as connection:
                for offset in range(0, len(missing_ids), SQLITE_ID_BATCH_SIZE):
                    batch = missing_ids[offset : offset + SQLITE_ID_BATCH_SIZE]
                    placeholders = ",".join("?" for _ in batch)
                    rows.extend(
                        connection.execute(
                            f"SELECT * FROM documents WHERE node_id IN ({placeholders}) "
                            f"AND status IN ({','.join('?' for _ in ALLOWED_STATUSES)})",
                            (*batch, *ALLOWED_STATUSES),
                        ).fetchall()
                    )
            for row in rows:
                document = self._row_document(row)
                recommended_open_target: Literal["index", "source"] = (
                    "source" if document.index_type == "text" else "index"
                )
                results.append(
                    SearchResult(
                        **document.model_dump(),
                        explanation="来自匹配文件夹的直接下级",
                        match_reasons=["folder_child"],
                        risk_flags=(
                            []
                            if document.status in {"clean", "indexed"}
                            else [f"status:{document.status}"]
                        ),
                        recommended_open_target=recommended_open_target,
                        open_target_uri=(
                            document.source_uri
                            if recommended_open_target == "source" and document.source_uri
                            else Path(document.index_path).resolve().as_uri()
                        ),
                    )
                )
        return results

    @staticmethod
    def _citations(results: list[SearchResult], node_ids: list[str]) -> list[SearchCitation]:
        allowed = {result.node_id: result for result in results}
        citations: list[SearchCitation] = []
        for node_id in node_ids:
            result = allowed.get(node_id)
            if result is None or not result.evidence:
                continue
            citations.append(
                SearchCitation(
                    citation_id=f"S{results.index(result) + 1}",
                    node_id=result.node_id,
                    name=result.name,
                    index_type=result.index_type,
                    index_path=result.index_path,
                    status=result.status,
                    summary=result.summary,
                    evidence=result.evidence[:3],
                )
            )
        return citations

    def _local_answer(
        self, query: str, results: list[SearchResult]
    ) -> tuple[GeneratedSearchAnswer, list[SearchCitation]]:
        if not results:
            return GeneratedSearchAnswer(summary="未找到匹配的索引。"), []
        cited_ids = [result.node_id for result in results if result.evidence][:5]
        citations = self._citations(results, cited_ids)
        if not citations:
            return (
                GeneratedSearchAnswer(
                    summary="找到了候选结果，但当前索引没有可验证的内部定位证据。",
                    recommended_node_ids=[result.node_id for result in results],
                    warnings=["结果仅用于候选定位，请打开索引后人工核验。"],
                ),
                [],
            )
        names = "、".join(result.name for result in results[:5])
        labels = " ".join(f"[{citation.citation_id}]" for citation in citations[:3])
        warnings = [
            "当前结果使用本地确定性排序，未调用联网模型。",
        ]
        if any(result.risk_flags for result in results[:5]):
            warnings.append("部分高位结果存在过期、状态或提取质量风险。")
        return (
            GeneratedSearchAnswer(
                summary=f"与“{query}”最相关的索引包括：{names}。依据：{labels}",
                recommended_node_ids=[result.node_id for result in results],
                cited_node_ids=[citation.node_id for citation in citations],
                warnings=warnings,
            ),
            citations,
        )

    @staticmethod
    def _degradation_reason(error: Exception) -> str:
        if isinstance(error, ProviderAuthError):
            return "ai_auth_failed"
        if isinstance(error, ProviderBudgetError):
            return "ai_budget_exhausted"
        if isinstance(error, ProviderQuotaError):
            return "ai_quota_exhausted"
        if isinstance(error, ProviderRateLimitError):
            return "ai_rate_limited"
        if isinstance(error, ProviderTransientError):
            return "ai_unavailable"
        if isinstance(error, ProviderOutputError):
            return "ai_invalid_output"
        return "ai_unavailable"

    def search_report(
        self,
        query: str,
        limit: int = 20,
        mode: Literal["local", "auto"] = "local",
    ) -> SearchReport:
        if mode not in {"local", "auto"}:
            raise ValueError("Search mode must be 'local' or 'auto'")
        started = time.perf_counter()
        candidate_limit = max(limit, self.config.ai_policy.max_search_candidates)
        local_results = self._expanded_results(query, candidate_limit)
        local_results = local_results[:candidate_limit]
        for rank, result in enumerate(local_results, start=1):
            result.rank = rank
        local_answer, local_citations = self._local_answer(query, local_results[:limit])

        def report(
            *,
            actual_mode: Literal["local", "ai", "degraded"],
            degradation_reason: str = "",
            results: list[SearchResult] | None = None,
            answer: GeneratedSearchAnswer | None = None,
            citations: list[SearchCitation] | None = None,
            usage: AIUsage | None = None,
        ) -> SearchReport:
            selected = (results or local_results)[:limit]
            for rank, result in enumerate(selected, start=1):
                result.rank = rank
            return SearchReport(
                report_schema_version=SEARCH_REPORT_SCHEMA_VERSION,
                search_algorithm_version=SEARCH_ALGORITHM_VERSION,
                query=query,
                requested_mode=mode,
                actual_mode=actual_mode,
                degradation_reason=degradation_reason,
                answer=answer or local_answer,
                results=selected,
                citations=local_citations if citations is None else citations,
                candidate_count=len(local_results),
                duration_ms=max(0, int((time.perf_counter() - started) * 1_000)),
                ai_usage=usage or AIUsage(),
            )

        if mode == "local":
            return report(actual_mode="local")

        provider = create_provider(self.config, require_network=False)
        if type(provider) is HeuristicProvider:
            reason = (
                "ai_disabled"
                if not self.config.ai_policy.enabled
                else "ai_key_not_configured"
            )
            local_answer.warnings.append(f"AI 自动降级：{reason}。")
            return report(
                actual_mode="degraded",
                degradation_reason=reason,
                usage=provider.usage,
            )
        try:
            reranked = provider.rerank_search(query, local_results)
            local_by_id = {result.node_id: result for result in local_results}
            ordered_ids = _valid_node_ids([result.node_id for result in reranked], local_by_id)
            ordered_ids.extend(
                result.node_id for result in local_results if result.node_id not in ordered_ids
            )
            ranked = [local_by_id[node_id] for node_id in ordered_ids][:candidate_limit]
            for rank, result in enumerate(ranked, start=1):
                result.rank = rank
            answer = provider.compose_search(query, ranked[:limit])
            allowed = {result.node_id: result for result in ranked[:limit]}
            answer.recommended_node_ids = _valid_node_ids(
                answer.recommended_node_ids, allowed
            ) or [result.node_id for result in ranked[:limit]]
            marker_node_ids: list[str] = []
            invalid_markers = False

            def replace_marker(match: re.Match[str]) -> str:
                nonlocal invalid_markers
                position = int(match.group(1))
                if 1 <= position <= min(limit, len(ranked)):
                    marker_node_ids.append(ranked[position - 1].node_id)
                    return match.group(0)
                invalid_markers = True
                return ""

            answer.summary = CITATION_MARKER.sub(replace_marker, answer.summary).strip()
            cited_ids = _valid_node_ids(answer.cited_node_ids + marker_node_ids, allowed)
            cited_ids = [node_id for node_id in cited_ids if allowed[node_id].evidence]
            citations = self._citations(ranked[:limit], cited_ids)
            if not citations:
                warning = (
                    "AI 返回的无效引用已被移除，已使用本地确定性结果。"
                    if invalid_markers
                    else "AI 未返回可验证证据，已使用本地确定性结果。"
                )
                local_answer.warnings.append(warning)
                return report(
                    actual_mode="degraded",
                    degradation_reason="ai_no_valid_evidence",
                    usage=provider.usage.model_copy(deep=True),
                )
            answer.cited_node_ids = [citation.node_id for citation in citations]
            if invalid_markers:
                answer.warnings.append("模型返回的无效引用标签已被移除。")
            if any(citation.status not in {"clean", "indexed"} for citation in citations):
                answer.warnings.append("部分引用索引存在状态或时效风险。")
            if not CITATION_MARKER.search(answer.summary):
                labels = " ".join(f"[{citation.citation_id}]" for citation in citations[:3])
                answer.summary = f"{answer.summary} 依据：{labels}"
            return report(
                actual_mode="ai",
                results=ranked,
                answer=answer,
                citations=citations,
                usage=provider.usage.model_copy(deep=True),
            )
        except (ProviderError, RuntimeError, ValueError) as error:
            reason = self._degradation_reason(error)
            local_answer.warnings.append(f"AI 自动降级：{reason}。")
            return report(
                actual_mode="degraded",
                degradation_reason=reason,
                usage=provider.usage.model_copy(deep=True),
            )

    def full_search_report(self, query: str, limit: int = 20) -> SearchReport:
        return self.search_report(query, limit, mode="auto")

    def full_search(self, query: str, limit: int = 20) -> list[SearchResult]:
        """Compatibility API returning only ranked results."""
        return self.full_search_report(query, limit).results


def results_markdown(
    query: str,
    results: list[SearchResult],
    answer: GeneratedSearchAnswer | None = None,
    citations: list[SearchCitation] | None = None,
) -> str:
    lines = [f"# Octopus 搜索：{query}", ""]
    if answer is not None:
        lines.extend(["## AI 任务摘要", "", answer.summary, ""])
        if answer.warnings:
            lines.extend(["### 风险提示", ""])
            lines.extend(f"- {warning}" for warning in answer.warnings)
            lines.append("")
        if citations:
            lines.extend(["### 可验证引用", ""])
            for citation in citations:
                uri = Path(citation.index_path).resolve().as_uri()
                lines.append(
                    f"- [{citation.citation_id}] [{citation.name}]({uri})"
                    f" · {citation.index_type} · 状态：{citation.status}"
                )
                for item in citation.evidence[:3]:
                    lines.append(f"  - `{item.locator}` · {item.text_excerpt or item.kind}")
            lines.append("")
        lines.extend(["## 推荐阅读顺序与索引链接", ""])
    if not results:
        lines.append("- 未找到匹配的索引。")
        return "\n".join(lines) + "\n"
    for result in results:
        index_uri = Path(result.index_path).resolve().as_uri()
        target_uri = result.open_target_uri or (
            result.source_uri
            if result.recommended_open_target == "source" and result.source_uri
            else index_uri
        )
        status = f" · 状态：{result.status}" if result.status != "clean" else ""
        lines.append(
            f"- {result.rank}. [{result.name}]({index_uri}){status} · [打开]({target_uri})"
        )
        if result.summary:
            lines.append(f"  - {result.summary}")
        if result.explanation:
            lines.append(f"  - 推荐原因：{result.explanation}")
        if result.evidence:
            lines.append(
                f"  - 证据定位：`{result.evidence[0].locator}` · "
                f"{result.evidence[0].text_excerpt or result.evidence[0].kind}"
            )
        if result.risk_flags:
            lines.append(f"  - 风险：{', '.join(result.risk_flags)}")
        lines.append(f"  - 类型：{result.index_type}")
        if result.match_evidence:
            evidence = result.match_evidence[0]
            lines.append(f"  - 命中证据：`{evidence.locator}` · {evidence.excerpt}")
    return "\n".join(lines) + "\n"


def search_report_markdown(report: SearchReport) -> str:
    markdown = results_markdown(report.query, report.results, report.answer, report.citations)
    usage = report.ai_usage
    return (
        markdown
        + "\n## 搜索执行信息\n\n"
        + f"- 算法版本：{report.search_algorithm_version}\n"
        + f"- 请求/实际模式：{report.requested_mode}/{report.actual_mode}\n"
        + f"- 降级原因：{report.degradation_reason or '无'}\n"
        + f"- 候选数：{report.candidate_count}\n"
        + f"- 总耗时：{report.duration_ms} ms\n"
        + "\n## AI 使用统计\n\n"
        + f"- 调用次数：{usage.calls}\n"
        + f"- 输入 token：{usage.input_tokens}\n"
        + f"- 输出 token：{usage.output_tokens}\n"
        + f"- 提示词版本：{', '.join(usage.prompt_versions) or '无记录'}\n"
        + (
            f"- 估算成本：{usage.estimated_cost:.6f}\n"
            if usage.estimated_cost is not None
            else "- 估算成本：未配置价格\n"
        )
        + f"- AI 耗时：{usage.duration_ms} ms\n"
    )
