"""Test fixtures: synthetic PDF generators."""
from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle


def make_long_pdf(path: Path, sentences: int = 400) -> Path:
    """Generate a PDF with one very long paragraph spanning many pages."""
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    style = getSampleStyleSheet()["BodyText"]
    text = " ".join(
        f"Calibration sentence number {i} describing the fixture source document."
        for i in range(sentences)
    )
    doc.build([Paragraph(text, style)])
    return path


def make_table_pdf(path: Path) -> Path:
    """Generate a PDF containing a single bordered table."""
    doc = SimpleDocTemplate(str(path))
    data = [["Header A", "Header B", "Header C"]]
    data += [[f"row{i}a", f"row{i}b", f"row{i}c"] for i in range(6)]
    table = Table(data)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ]
        )
    )
    doc.build(
        [
            Paragraph(
                "Intro paragraph before the table.", getSampleStyleSheet()["BodyText"]
            ),
            table,
        ]
    )
    return path


def make_text_pdf(path: Path, body: str) -> Path:
    """Generate a PDF from arbitrary text."""
    doc = SimpleDocTemplate(str(path))
    doc.build([Paragraph(body, getSampleStyleSheet()["BodyText"])])
    return path