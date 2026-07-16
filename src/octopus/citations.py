from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Literal

from pydantic import Field

from .models import OctopusModel

CitationStyle = Literal["gb-t-7714-2015", "apa"]
CitationType = Literal[
    "article",
    "book",
    "chapter",
    "conference",
    "thesis",
    "report",
    "web",
    "dataset",
    "software",
    "other",
]

DEFAULT_CITATION_STYLE: CitationStyle = "gb-t-7714-2015"
CSL_STYLE_IDS: dict[CitationStyle, str] = {
    "gb-t-7714-2015": "china-national-standard-gb-t-7714-2015-numeric",
    "apa": "apa",
}


class CitationRecord(OctopusModel):
    citation_id: str = ""
    citation_type: CitationType = "other"
    title: str = Field(default="", max_length=1_000)
    authors: list[str] = Field(default_factory=list)
    year: str = Field(default="", max_length=32)
    carrier: str = Field(default="", max_length=64)
    publication_title: str = Field(default="", max_length=500)
    place: str = Field(default="", max_length=200)
    publisher: str = Field(default="", max_length=300)
    volume: str = Field(default="", max_length=64)
    issue: str = Field(default="", max_length=64)
    pages: str = Field(default="", max_length=100)
    edition: str = Field(default="", max_length=100)
    doi: str = Field(default="", max_length=500)
    url: str = Field(default="", max_length=2_000)
    accessed_at: str = Field(default="", max_length=64)
    language: str = Field(default="", max_length=64)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


def normalize_citation_style(value: str | None) -> CitationStyle:
    normalized = (value or DEFAULT_CITATION_STYLE).strip().casefold()
    aliases = {
        "gb/t 7714-2015": "gb-t-7714-2015",
        "gb/t7714-2015": "gb-t-7714-2015",
        "gb7714": "gb-t-7714-2015",
        "gb-t-7714-2015": "gb-t-7714-2015",
        "apa": "apa",
        "apa-7": "apa",
        "apa7": "apa",
    }
    try:
        return aliases[normalized]  # type: ignore[return-value]
    except KeyError as error:
        raise ValueError(f"Unsupported citation style: {value}") from error


def csl_style_id(style: CitationStyle | str) -> str:
    return CSL_STYLE_IDS[normalize_citation_style(style)]


def _clean(value: str) -> str:
    return " ".join(value.strip().split())


def _terminal(value: str) -> str:
    value = _clean(value)
    if not value:
        return ""
    return value if value.endswith((".", "。", "?", "？", "!", "！")) else f"{value}."


def _authors(record: CitationRecord) -> list[str]:
    return [_clean(author) for author in record.authors if _clean(author)]


def _gb_carrier(record: CitationRecord) -> str:
    if record.carrier.strip():
        carrier = record.carrier.strip().strip("[]")
        return f"[{carrier}]"
    values = {
        "article": "J",
        "book": "M",
        "chapter": "M",
        "conference": "C",
        "thesis": "D",
        "report": "R",
        "web": "EB/OL",
        "dataset": "DS",
        "software": "CP",
        "other": "Z",
    }
    return f"[{values[record.citation_type]}]"


def _gb_t_7714(record: CitationRecord) -> str:
    authors = ", ".join(_authors(record))
    title = _clean(record.title) or "未命名资料"
    lead = f"{authors}. " if authors else ""
    lead += f"{title}{_gb_carrier(record)}."

    publication = _clean(record.publication_title)
    year = _clean(record.year)
    volume = _clean(record.volume)
    issue = _clean(record.issue)
    pages = _clean(record.pages)
    if record.citation_type == "article" or publication:
        details = publication
        if year:
            details += (", " if details else "") + year
        if volume:
            details += (", " if details else "") + volume
        if issue:
            details += f"({issue})"
        if pages:
            details += (": " if details else "") + pages
        body = _terminal(details)
    else:
        publication_parts = []
        if record.place.strip():
            publication_parts.append(_clean(record.place))
        if record.publisher.strip():
            publisher = _clean(record.publisher)
            if publication_parts:
                publication_parts[-1] = f"{publication_parts[-1]}: {publisher}"
            else:
                publication_parts.append(publisher)
        if year:
            publication_parts.append(year)
        body = _terminal(", ".join(publication_parts))

    identifiers: list[str] = []
    if record.doi.strip():
        identifiers.append(f"DOI: {_clean(record.doi)}.")
    if record.url.strip():
        accessed = f" [{_clean(record.accessed_at)}]" if record.accessed_at.strip() else ""
        identifiers.append(f"{_clean(record.url)}{accessed}.")
    return " ".join(part for part in [lead, body, *identifiers] if part).strip()


def _apa_author(author: str) -> str:
    value = _clean(author)
    if not value:
        return ""
    if "," in value:
        return value
    parts = value.split()
    if len(parts) == 1:
        return value
    family, given = parts[-1], parts[:-1]
    initials = " ".join(f"{part[0].upper()}." for part in given if part)
    return f"{family}, {initials}" if initials else family


def _apa_authors(record: CitationRecord) -> str:
    authors = [_apa_author(author) for author in _authors(record)]
    authors = [author for author in authors if author]
    if len(authors) <= 1:
        return authors[0] if authors else ""
    if len(authors) == 2:
        return f"{authors[0]}, & {authors[1]}"
    return f"{', '.join(authors[:-1])}, & {authors[-1]}"


def _apa(record: CitationRecord) -> str:
    author = _apa_authors(record)
    year = _clean(record.year) or "n.d."
    title = _terminal(record.title or "Untitled source")
    parts = [f"{author} ({year})." if author else f"({year}).", title]
    publication = _clean(record.publication_title)
    if publication:
        journal = publication
        if record.volume.strip():
            journal += f", {_clean(record.volume)}"
        if record.issue.strip():
            journal += f"({_clean(record.issue)})"
        if record.pages.strip():
            journal += f", {_clean(record.pages)}"
        parts.append(_terminal(journal))
    else:
        publisher = _clean(record.publisher)
        if publisher:
            parts.append(_terminal(publisher))
    if record.doi.strip():
        doi = _clean(record.doi)
        parts.append(doi if doi.startswith("http") else f"https://doi.org/{doi}")
    elif record.url.strip():
        parts.append(_clean(record.url))
    return " ".join(part for part in parts if part).strip()


def render_citation(
    record: CitationRecord, style: CitationStyle | str = DEFAULT_CITATION_STYLE
) -> str:
    selected = normalize_citation_style(style)
    return _gb_t_7714(record) if selected == "gb-t-7714-2015" else _apa(record)


def _citation_identity(record: CitationRecord) -> tuple[str, ...]:
    if record.citation_id.strip():
        return ("id", record.citation_id.strip().casefold())
    if record.doi.strip():
        return ("doi", record.doi.strip().casefold())
    return (
        "record",
        record.title.strip().casefold(),
        record.year.strip().casefold(),
        *(author.strip().casefold() for author in record.authors),
    )


def unique_citations(records: Iterable[CitationRecord]) -> list[CitationRecord]:
    result: list[CitationRecord] = []
    seen: set[tuple[str, ...]] = set()
    for record in records:
        identity = _citation_identity(record)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(record)
    return result


def render_bibliography(
    records: Iterable[CitationRecord],
    style: CitationStyle | str = DEFAULT_CITATION_STYLE,
) -> str:
    selected = normalize_citation_style(style)
    citations = unique_citations(records)
    if selected == "apa":
        citations.sort(
            key=lambda item: (
                _authors(item)[0].casefold() if _authors(item) else "",
                item.year,
                item.title.casefold(),
            )
        )
        return "\n".join(render_citation(record, selected) for record in citations)
    return "\n".join(
        f"[{index}] {render_citation(record, selected)}"
        for index, record in enumerate(citations, start=1)
    )


def _ascii_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]+", "", ascii_value)


def bibtex_key(record: CitationRecord, index: int = 1) -> str:
    if record.citation_id.strip():
        explicit = _ascii_slug(record.citation_id)
        if explicit:
            return explicit[:80]
    authors = _authors(record)
    author = _ascii_slug(authors[0].split(",", 1)[0] if authors else "")
    year = re.sub(r"\D", "", record.year)[:4]
    title = _ascii_slug(record.title)[:24]
    return (author + year + title or f"octopus{index}")[:80]


def _bibtex_escape(value: str) -> str:
    return _clean(value).replace("\\", "\\textbackslash{}").replace("{", "\\{").replace("}", "\\}")


def render_bibtex(records: Iterable[CitationRecord]) -> str:
    entry_types: dict[CitationType, str] = {
        "article": "article",
        "book": "book",
        "chapter": "inbook",
        "conference": "inproceedings",
        "thesis": "phdthesis",
        "report": "techreport",
        "web": "online",
        "dataset": "dataset",
        "software": "software",
        "other": "misc",
    }
    rendered: list[str] = []
    used_keys: set[str] = set()
    for index, record in enumerate(unique_citations(records), start=1):
        base_key = bibtex_key(record, index)
        key = base_key
        suffix = 2
        while key.casefold() in used_keys:
            key = f"{base_key}{suffix}"
            suffix += 1
        used_keys.add(key.casefold())
        fields: list[tuple[str, str]] = []
        if record.title.strip():
            fields.append(("title", record.title))
        if _authors(record):
            fields.append(("author", " and ".join(_authors(record))))
        for name, value in (
            ("year", record.year),
            ("journal", record.publication_title if record.citation_type == "article" else ""),
            (
                "booktitle",
                record.publication_title
                if record.citation_type in {"chapter", "conference"}
                else "",
            ),
            ("publisher", record.publisher),
            ("address", record.place),
            ("volume", record.volume),
            ("number", record.issue),
            ("pages", record.pages),
            ("edition", record.edition),
            ("doi", record.doi),
            ("url", record.url),
            ("urldate", record.accessed_at),
        ):
            if value.strip():
                fields.append((name, value))
        body = ",\n".join(f"  {name} = {{{_bibtex_escape(value)}}}" for name, value in fields)
        rendered.append(f"@{entry_types[record.citation_type]}{{{key},\n{body}\n}}")
    return "\n\n".join(rendered) + ("\n" if rendered else "")
