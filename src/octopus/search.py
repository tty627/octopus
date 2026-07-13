from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from .config import load_repository_config, octopus_dir
from .models import (
    GeneratedSearchAnswer,
    SearchCitation,
    SearchDocument,
    SearchReport,
    SearchResult,
)
from .providers import create_provider
from .rendering import read_machine_header

LATIN_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
CITATION_MARKER = re.compile(r"\[S(\d+)\]")
SQLITE_ID_BATCH_SIZE = 1_000
SEARCH_SCHEMA_VERSION = "0.3"


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


def searchable_text(document: SearchDocument) -> str:
    values = [
        document.name,
        document.summary,
        document.description,
        " ".join(document.tags),
        " ".join(document.keywords),
        document.body_excerpt,
    ]
    return " ".join(analyze_terms("\n".join(values)))


def _valid_node_ids(node_ids: list[str], allowed: dict[str, SearchResult]) -> list[str]:
    valid: list[str] = []
    for node_id in node_ids:
        if node_id in allowed and node_id not in valid:
            valid.append(node_id)
    return valid


class SearchIndex:
    def __init__(self, index_repository: Path) -> None:
        self.index = index_repository.resolve()
        self.database = octopus_dir(self.index) / "search.sqlite3"
        self.config = load_repository_config(self.index)

    def _connect(self) -> sqlite3.Connection:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def _iter_documents(self) -> list[SearchDocument]:
        documents: list[SearchDocument] = []
        for path in self.index.rglob("*.md"):
            if octopus_dir(self.index) == path or octopus_dir(self.index) in path.parents:
                continue
            try:
                header, body = read_machine_header(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            index_type = header.get("schema", {}).get("index_type")
            if index_type not in {"leaf", "foldernode"}:
                continue
            layer = header.get("summary_layer", {})
            update = header.get("update_control", {})
            if index_type == "leaf":
                card = header.get("attachment_card_layer", {})
                source = card.get("source", {})
                metadata = card.get("metadata", {})
                node_id = source.get("source_id", "")
                source_uri = metadata.get("file_uri", "")
            else:
                card = header.get("folder_card_layer", {})
                source = card.get("source", {})
                node_id = source.get("folder_id", "")
                source_uri = card.get("metadata", {}).get("folder_uri", "")
            if not node_id:
                continue
            documents.append(
                SearchDocument(
                    node_id=node_id,
                    index_type=index_type,
                    index_path=str(path.resolve()),
                    name=layer.get("name", path.stem),
                    summary=layer.get("one_sentence_summary", ""),
                    description=layer.get("description", ""),
                    tags=layer.get("tag_rough", []),
                    keywords=layer.get("topic_keywords", []),
                    body_excerpt=body[:8_000],
                    status=update.get("index_status", "clean"),
                    source_uri=source_uri,
                )
            )
        return documents

    def _cache_is_current(self) -> bool:
        if not self.database.exists():
            return False
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT value FROM search_metadata WHERE key = 'schema_version'"
                ).fetchone()
            return bool(row and row["value"] == SEARCH_SCHEMA_VERSION)
        except sqlite3.Error:
            return False

    def rebuild(self) -> int:
        documents = self._iter_documents()
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                DROP TABLE IF EXISTS document_fts;
                DROP TABLE IF EXISTS documents;
                DROP TABLE IF EXISTS search_metadata;
                CREATE TABLE search_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE documents (
                    node_id TEXT PRIMARY KEY,
                    index_type TEXT NOT NULL,
                    index_path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    description TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    keywords_json TEXT NOT NULL,
                    body_excerpt TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_uri TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE document_fts USING fts5(
                    node_id UNINDEXED,
                    name_terms,
                    summary_terms,
                    keyword_terms,
                    body_terms,
                    tokenize='unicode61 remove_diacritics 2'
                );
                """
            )
            connection.execute(
                "INSERT INTO search_metadata(key, value) VALUES ('schema_version', ?)",
                (SEARCH_SCHEMA_VERSION,),
            )
            for document in documents:
                connection.execute(
                    "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        document.node_id,
                        document.index_type,
                        document.index_path,
                        document.name,
                        document.summary,
                        document.description,
                        json.dumps(document.tags, ensure_ascii=False),
                        json.dumps(document.keywords, ensure_ascii=False),
                        document.body_excerpt,
                        document.status,
                        document.source_uri,
                    ),
                )
                connection.execute(
                    "INSERT INTO document_fts VALUES (?, ?, ?, ?, ?)",
                    (
                        document.node_id,
                        " ".join(analyze_terms(document.name)),
                        " ".join(analyze_terms(document.summary + " " + document.description)),
                        " ".join(analyze_terms(" ".join(document.tags + document.keywords))),
                        " ".join(analyze_terms(document.body_excerpt)),
                    ),
                )
            connection.commit()
        return len(documents)

    def search(self, query: str, limit: int = 20, include_stale: bool = True) -> list[SearchResult]:
        if not self._cache_is_current():
            self.rebuild()
        terms = analyze_terms(query)
        if not terms:
            return []
        expression = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
        allowed = ["clean", "indexed"]
        if include_stale:
            allowed.append("stale")
        placeholders = ",".join("?" for _ in allowed)
        sql = f"""
            SELECT d.*, bm25(document_fts, 0.0, 10.0, 5.0, 3.0, 1.0) AS rank
            FROM document_fts
            JOIN documents d ON d.node_id = document_fts.node_id
            WHERE document_fts MATCH ? AND d.status IN ({placeholders})
            ORDER BY rank
            LIMIT ?
        """
        candidate_limit = min(500, max(limit * 5, 50))
        with closing(self._connect()) as connection:
            rows = connection.execute(sql, (expression, *allowed, candidate_limit)).fetchall()
        query_terms = set(terms)
        normalized_query = query.casefold().strip()
        results: list[SearchResult] = []
        for row in rows:
            combined = " ".join(
                [
                    row["name"],
                    row["summary"],
                    row["description"],
                    " ".join(json.loads(row["tags_json"])),
                    " ".join(json.loads(row["keywords_json"])),
                    row["body_excerpt"],
                ]
            )
            document_terms = set(analyze_terms(combined))
            matched = sorted(query_terms & document_terms)
            coverage = len(matched) / max(1, len(query_terms))
            reasons: list[str] = []
            boost = coverage * 3.0
            if normalized_query and normalized_query in row["name"].casefold():
                reasons.append("exact_name")
                boost += 8.0
            if normalized_query and normalized_query in row["summary"].casefold():
                reasons.append("exact_summary")
                boost += 5.0
            name_terms = set(analyze_terms(row["name"]))
            name_coverage = len(query_terms & name_terms) / max(1, len(query_terms))
            if name_coverage:
                reasons.append("name_term_match")
                boost += name_coverage * 4.0
            if coverage == 1.0:
                reasons.append("all_terms_matched")
            results.append(
                SearchResult(
                    node_id=row["node_id"],
                    index_type=row["index_type"],
                    index_path=row["index_path"],
                    name=row["name"],
                    summary=row["summary"],
                    description=row["description"],
                    tags=json.loads(row["tags_json"]),
                    keywords=json.loads(row["keywords_json"]),
                    body_excerpt=row["body_excerpt"],
                    status=row["status"],
                    source_uri=row["source_uri"],
                    score=-float(row["rank"]) + boost,
                    matched_terms=matched,
                    match_reasons=reasons,
                )
            )
        results.sort(key=lambda item: (-item.score, item.name.casefold(), item.node_id))
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
                            "AND status IN ('clean', 'indexed', 'stale')",
                            batch,
                        ).fetchall()
                    )
            for row in rows:
                results.append(
                    SearchResult(
                        node_id=row["node_id"],
                        index_type=row["index_type"],
                        index_path=row["index_path"],
                        name=row["name"],
                        summary=row["summary"],
                        description=row["description"],
                        tags=json.loads(row["tags_json"]),
                        keywords=json.loads(row["keywords_json"]),
                        body_excerpt=row["body_excerpt"],
                        status=row["status"],
                        source_uri=row["source_uri"],
                        score=0.0,
                    )
                )
        return results

    def full_search_report(self, query: str, limit: int = 20) -> SearchReport:
        candidate_limit = max(limit, self.config.ai_policy.max_search_candidates)
        results = self._expanded_results(query, candidate_limit)
        provider = create_provider(self.config, require_network=True)
        ranked = provider.rerank_search(query, results)[:limit]
        answer = provider.compose_search(query, ranked)
        allowed = {result.node_id: result for result in ranked}
        answer.recommended_node_ids = _valid_node_ids(answer.recommended_node_ids, allowed) or [
            result.node_id for result in ranked
        ]
        invalid_markers = False
        marker_node_ids: list[str] = []

        def replace_marker(match: re.Match[str]) -> str:
            nonlocal invalid_markers
            position = int(match.group(1))
            if 1 <= position <= len(ranked):
                marker_node_ids.append(ranked[position - 1].node_id)
                return match.group(0)
            invalid_markers = True
            return ""

        answer.summary = CITATION_MARKER.sub(replace_marker, answer.summary).strip()
        cited_ids = _valid_node_ids(answer.cited_node_ids + marker_node_ids, allowed)
        if not cited_ids:
            cited_ids = answer.recommended_node_ids[:5]
        answer.cited_node_ids = cited_ids
        citations = [
            SearchCitation(
                citation_id=f"S{ranked.index(result) + 1}",
                node_id=result.node_id,
                name=result.name,
                index_type=result.index_type,
                index_path=result.index_path,
                status=result.status,
                summary=result.summary,
            )
            for node_id in cited_ids
            if (result := allowed.get(node_id)) is not None
        ]
        if any(citation.status == "stale" for citation in citations):
            warning = "部分引用索引处于 stale 状态，结论可能落后于原始资料。"
            if warning not in answer.warnings:
                answer.warnings.append(warning)
        if invalid_markers:
            answer.warnings.append("模型返回的无效引用标签已被移除。")
        if citations and not CITATION_MARKER.search(answer.summary):
            labels = " ".join(f"[{citation.citation_id}]" for citation in citations[:3])
            answer.summary = f"{answer.summary} 依据：{labels}"
        return SearchReport(
            query=query,
            answer=answer,
            results=ranked,
            citations=citations,
            ai_usage=provider.usage.model_copy(deep=True),
        )

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
            lines.append("")
        lines.extend(["## 推荐阅读顺序与索引链接", ""])
    if not results:
        lines.append("- 未找到匹配的索引。")
        return "\n".join(lines) + "\n"
    for result in results:
        index_uri = Path(result.index_path).resolve().as_uri()
        status = f" · 状态：{result.status}" if result.status != "clean" else ""
        lines.append(f"- [{result.name}]({index_uri}){status}")
        if result.summary:
            lines.append(f"  - {result.summary}")
        lines.append(f"  - 类型：{result.index_type}")
    return "\n".join(lines) + "\n"


def search_report_markdown(report: SearchReport) -> str:
    markdown = results_markdown(report.query, report.results, report.answer, report.citations)
    usage = report.ai_usage
    return (
        markdown
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
        + f"- 总耗时：{usage.duration_ms} ms\n"
    )
