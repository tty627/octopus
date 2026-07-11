from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from .config import load_repository_config, octopus_dir
from .models import SearchDocument, SearchResult
from .providers import create_provider
from .rendering import read_machine_header

LATIN_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
CJK_RUN = re.compile(r"[\u3400-\u9fff]+")


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

    def rebuild(self) -> int:
        documents = self._iter_documents()
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                DROP TABLE IF EXISTS document_fts;
                DROP TABLE IF EXISTS documents;
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
                    search_text,
                    tokenize='unicode61 remove_diacritics 2'
                );
                """
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
                    "INSERT INTO document_fts(node_id, search_text) VALUES (?, ?)",
                    (document.node_id, searchable_text(document)),
                )
            connection.commit()
        return len(documents)

    def search(self, query: str, limit: int = 20, include_stale: bool = True) -> list[SearchResult]:
        if not self.database.exists():
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
            SELECT d.*, bm25(document_fts) AS rank
            FROM document_fts
            JOIN documents d ON d.node_id = document_fts.node_id
            WHERE document_fts MATCH ? AND d.status IN ({placeholders})
            ORDER BY rank
            LIMIT ?
        """
        with closing(self._connect()) as connection:
            rows = connection.execute(sql, (expression, *allowed, limit)).fetchall()
        return [
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
                score=-float(row["rank"]),
            )
            for row in rows
        ]

    def full_search(self, query: str, limit: int = 20) -> list[SearchResult]:
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
        missing_ids = [node_id for node_id in child_ids if node_id not in existing]
        if missing_ids and self.database.exists():
            placeholders = ",".join("?" for _ in missing_ids)
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    f"SELECT * FROM documents WHERE node_id IN ({placeholders}) "
                    "AND status IN ('clean', 'indexed', 'stale')",
                    missing_ids,
                ).fetchall()
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
        provider = create_provider(self.config, require_network=True)
        return provider.rerank_search(query, results)[:limit]


def results_markdown(query: str, results: list[SearchResult]) -> str:
    lines = [f"# Octopus 搜索：{query}", ""]
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
