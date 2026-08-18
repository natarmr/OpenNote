"""Unit tests for the Docling fallback page-attribution helpers.

These test the pure page-mapping logic with fake items, so no slow Docling
conversion is required.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from opennote.ingest.parsers.pdf_docling import (
    _section_heading,
    _section_pages,
    heading_page_map,
)


@dataclass
class FakeProv:
    page_no: int


@dataclass
class FakeItem:
    text: str
    label: str = "paragraph"
    pages: list = field(default_factory=list)

    @property
    def prov(self):
        return [FakeProv(p) for p in self.pages]


def test_heading_page_map_spans_pages():
    items = [
        FakeItem("Title", label="title", pages=[1]),
        FakeItem("Intro body", label="paragraph", pages=[1]),
        FakeItem("Section Two", label="section_heading", pages=[2]),
        FakeItem("body on page 2", label="paragraph", pages=[2]),
        FakeItem("body continuing on page 3", label="paragraph", pages=[3]),
        FakeItem("Section Three", label="section_heading", pages=[3]),
    ]
    hmap = heading_page_map(items)
    assert hmap["Title"] == (1, 1)
    assert hmap["Section Two"] == (2, 3), "section spanning pages 2-3"
    assert hmap["Section Three"] == (3, 3)


def test_heading_page_map_records_heading_own_page():
    items = [
        FakeItem("Only Heading", label="section_heading", pages=[5]),
    ]
    assert heading_page_map(items) == {"Only Heading": (5, 5)}


def test_section_heading_extraction():
    assert _section_heading("# My Title\n\nbody") == "My Title"
    assert _section_heading("## Sub Heading\nbody") == "Sub Heading"
    assert _section_heading("no heading here") is None


def test_section_pages_resolution():
    hmap = {"My Title": (2, 3)}
    assert _section_pages("# My Title\n\nbody", hmap) == (2, 3)
    assert _section_pages("no heading", hmap) == (1, 1)
    assert _section_pages("# Unknown Title", hmap) == (1, 1)