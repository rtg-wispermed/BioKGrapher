"""Streaming MEDLINE/PubMed XML parsing (gzip + lxml.iterparse, flat memory)."""

from __future__ import annotations

import gzip
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from lxml import etree

_YEAR_RE = re.compile(r"\d{4}")


@dataclass(slots=True)
class Record:
    pmid: int
    version: int
    title: str
    abstract: str
    has_abstract: bool
    mesh: list[tuple[str, str]] = field(default_factory=list)  # (descriptor UI, name)
    year: int | None = None  # publication year (for time-trend analysis)

    def text(self) -> str:
        """Title + abstract, the document fed to MedCAT."""
        if self.title and self.abstract:
            return f"{self.title}. {self.abstract}"
        return self.abstract or self.title


def _text(elem: etree._Element | None) -> str:
    """Full text content of an element, including inline markup (MathML, <i>, ...)."""
    if elem is None:
        return ""
    return "".join(elem.itertext()).strip()


def _extract_abstract(article: etree._Element) -> tuple[str, bool]:
    abstract = article.find("Abstract")
    if abstract is None:
        return "", False
    parts: list[str] = []
    for at in abstract.findall("AbstractText"):
        body = _text(at)
        if not body:
            continue
        label = at.get("Label") or at.get("NlmCategory")
        parts.append(f"{label}: {body}" if label and label.upper() != "UNASSIGNED" else body)
    text = "\n".join(parts)
    return text, bool(text)


def _extract_mesh(citation: etree._Element) -> list[tuple[str, str]]:
    """MeSH descriptors ``(UI, name)`` from ``MeshHeadingList`` (drives fast presets)."""
    mhl = citation.find("MeshHeadingList")
    if mhl is None:
        return []
    out: list[tuple[str, str]] = []
    for heading in mhl.findall("MeshHeading"):
        descriptor = heading.find("DescriptorName")
        if descriptor is None:
            continue
        ui = descriptor.get("UI") or ""
        name = _text(descriptor)
        if ui and name:
            out.append((ui, name))
    return out


def _extract_year(citation: etree._Element, article: etree._Element) -> int | None:
    """Publication year from PubDate (Year or MedlineDate), falling back to DateCompleted."""
    journal = article.find("Journal")
    if journal is not None:
        pubdate = journal.find("JournalIssue/PubDate")
        if pubdate is not None:
            year = pubdate.findtext("Year")
            if year and year.isdigit():
                return int(year)
            medline = pubdate.findtext("MedlineDate")  # e.g. "1998 Dec-1999 Jan"
            if medline and (m := _YEAR_RE.search(medline)):
                return int(m.group())
    for tag in ("DateCompleted", "DateRevised"):
        el = citation.find(tag)
        if el is not None:
            year = el.findtext("Year")
            if year and year.isdigit():
                return int(year)
    return None


def _clear(elem: etree._Element) -> None:
    """Free the element and any already-processed previous siblings (flat memory)."""
    elem.clear()
    parent = elem.getparent()
    if parent is not None:
        while elem.getprevious() is not None:
            del parent[0]


def iter_records(path: str) -> Iterator[Record]:
    """Yield a :class:`Record` per ``PubmedArticle`` in a gzipped MEDLINE file."""
    with gzip.open(path, "rb") as fh:
        context = etree.iterparse(fh, events=("end",), tag="PubmedArticle")
        for _, article_el in context:
            citation = article_el.find("MedlineCitation")
            if citation is None:  # PubmedBookArticle etc. — skip
                _clear(article_el)
                continue
            pmid_el = citation.find("PMID")
            article = citation.find("Article")
            if pmid_el is None or article is None or pmid_el.text is None:
                _clear(article_el)
                continue
            pmid = int(pmid_el.text)
            version = int(pmid_el.get("Version", "1"))
            title = _text(article.find("ArticleTitle"))
            abstract, has_abstract = _extract_abstract(article)
            mesh = _extract_mesh(citation)
            year = _extract_year(citation, article)
            yield Record(pmid, version, title, abstract, has_abstract, mesh, year)
            _clear(article_el)


def iter_deletions(path: str) -> Iterator[int]:
    """Yield withdrawn PMIDs from ``DeleteCitation`` (present in update files)."""
    with gzip.open(path, "rb") as fh:
        context = etree.iterparse(fh, events=("end",), tag="DeleteCitation")
        for _, dc in context:
            for pmid_el in dc.findall("PMID"):
                if pmid_el.text:
                    yield int(pmid_el.text)
            _clear(dc)
