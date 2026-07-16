from __future__ import annotations

import pytest

from octopus.citations import (
    CSL_STYLE_IDS,
    CitationRecord,
    bibtex_key,
    csl_style_id,
    normalize_citation_style,
    render_bibliography,
    render_bibtex,
    render_citation,
    unique_citations,
)


def test_citation_style_aliases_and_invalid_values() -> None:
    assert normalize_citation_style(None) == "gb-t-7714-2015"
    assert normalize_citation_style(" GB/T 7714-2015 ") == "gb-t-7714-2015"
    assert normalize_citation_style("gb/t7714-2015") == "gb-t-7714-2015"
    assert normalize_citation_style("gb7714") == "gb-t-7714-2015"
    assert normalize_citation_style("APA-7") == "apa"
    assert normalize_citation_style("apa7") == "apa"
    assert csl_style_id("apa") == CSL_STYLE_IDS["apa"]
    with pytest.raises(ValueError, match="Unsupported citation style"):
        normalize_citation_style("chicago")


def test_gb_t_and_apa_render_complete_article_and_book_records() -> None:
    article = CitationRecord(
        citation_id="paper-1",
        citation_type="article",
        title="  Evidence   First  ",
        authors=["Ada Lovelace", "Turing, Alan", "  "],
        year="2026",
        publication_title="Journal of Tests",
        volume="12",
        issue="3",
        pages="10-20",
        doi="10.1000/test",
        url="https://example.test/paper",
        accessed_at="2026-07-16",
    )
    gb = render_citation(article)
    assert "Ada Lovelace, Turing, Alan" in gb
    assert "Evidence First[J]." in gb
    assert "Journal of Tests, 2026, 12(3): 10-20." in gb
    assert "DOI: 10.1000/test." in gb
    assert "https://example.test/paper [2026-07-16]." in gb

    apa = render_citation(article, "apa")
    assert "Lovelace, A., & Turing, Alan (2026)." in apa
    assert "Journal of Tests, 12(3), 10-20." in apa
    assert apa.endswith("https://doi.org/10.1000/test")

    book = CitationRecord(
        citation_type="book",
        title="Research Methods",
        authors=["Plato"],
        year="2025",
        carrier="M/OL",
        place="Beijing",
        publisher="Octopus Press",
        edition="2",
        url="https://example.test/book",
    )
    assert "Research Methods[M/OL]." in render_citation(book)
    assert "Beijing: Octopus Press, 2025." in render_citation(book)
    assert "Plato (2025)." in render_citation(book, "apa")
    assert "Octopus Press." in render_citation(book, "apa")


def test_citation_defaults_author_lists_and_bibliography_ordering() -> None:
    no_author = CitationRecord(title="", citation_type="other")
    assert render_citation(no_author).startswith("未命名资料[Z].")
    assert render_citation(no_author, "apa") == "(n.d.). Untitled source."

    two_authors = CitationRecord(
        title="Beta",
        authors=["Grace Hopper", "Alan Turing"],
        year="1950",
        publisher="Publisher",
        url="https://example.test/beta",
    )
    three_authors = CitationRecord(
        title="Alpha",
        authors=["Ada Lovelace", "Grace Hopper", "Alan Turing"],
        year="1843",
    )
    assert "Hopper, G., & Turing, A." in render_citation(two_authors, "apa")
    assert "Lovelace, A., Hopper, G., & Turing, A." in render_citation(
        three_authors, "apa"
    )
    apa = render_bibliography([two_authors, three_authors], "apa")
    assert apa.splitlines()[0].startswith("Lovelace")
    numbered = render_bibliography([two_authors, three_authors])
    assert numbered.startswith("[1]")
    assert "\n[2]" in numbered


def test_unique_citations_use_id_doi_or_record_identity() -> None:
    by_id = CitationRecord(citation_id="Same", title="One")
    duplicate_id = CitationRecord(citation_id="same", title="Two")
    by_doi = CitationRecord(doi="10/X", title="Three")
    duplicate_doi = CitationRecord(doi="10/x", title="Four")
    by_record = CitationRecord(title="Title", year="2020", authors=["Author"])
    duplicate_record = CitationRecord(title=" title ", year="2020", authors=["author"])
    result = unique_citations(
        [by_id, duplicate_id, by_doi, duplicate_doi, by_record, duplicate_record]
    )
    assert result == [by_id, by_doi, by_record]


@pytest.mark.parametrize(
    ("citation_type", "entry_type"),
    [
        ("article", "article"),
        ("book", "book"),
        ("chapter", "inbook"),
        ("conference", "inproceedings"),
        ("thesis", "phdthesis"),
        ("report", "techreport"),
        ("web", "online"),
        ("dataset", "dataset"),
        ("software", "software"),
        ("other", "misc"),
    ],
)
def test_bibtex_supports_all_citation_types(citation_type: str, entry_type: str) -> None:
    record = CitationRecord(
        citation_type=citation_type,  # type: ignore[arg-type]
        title=r"A {Test} \ Record",
        authors=["Ada Lovelace"],
        year="2026",
        publication_title=(
            "Proceedings" if citation_type in {"article", "chapter", "conference"} else ""
        ),
        publisher="Publisher",
        place="London",
        volume="2",
        issue="4",
        pages="1--9",
        edition="Second",
        doi="10.1/example",
        url="https://example.test",
        accessed_at="2026-07-16",
    )
    rendered = render_bibtex([record])
    assert rendered.startswith(f"@{entry_type}{{AdaLovelace2026ATestRecord,")
    assert r"title = {A \{Test\} \textbackslash\{\} Record}" in rendered
    assert "author = {Ada Lovelace}" in rendered
    assert rendered.endswith("}\n")


def test_bibtex_keys_fallback_deduplicate_and_honor_explicit_ids() -> None:
    explicit = CitationRecord(citation_id=" Résumé / 2026 ", title="Ignored")
    assert bibtex_key(explicit) == "Resume2026"
    assert bibtex_key(CitationRecord(), 7) == "octopus7"

    first = CitationRecord(citation_id="same", title="One")
    second = CitationRecord(citation_id="same", title="One")
    distinct_collision = CitationRecord(citation_id="same!", title="Two")
    rendered = render_bibtex([first, second, distinct_collision])
    assert rendered.count("@misc") == 2
    assert "@misc{same," in rendered
    assert "@misc{same2," in rendered
    assert render_bibtex([]) == ""
