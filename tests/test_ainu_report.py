"""Tests for `scripts/ainu_columns.py`, the report over the Ainu line-box derivation.

The rule itself lives in `glyph_atlas.ainu` and is tested in `tests/test_ainu.py`; this file holds
the script to its own job, which is to turn the module's per-page answer into the summary a report
quotes — the exact and within-25% counts, the evidence gate, and the per-witness spread. Everything
here is synthetic: no detector, no page image, no network.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from glyph_atlas import ainu
from glyph_atlas.schema import Box, Line, Page

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("ainu_columns", ROOT / "scripts" / "ainu_columns.py")
assert _spec is not None and _spec.loader is not None
ainu_columns = importlib.util.module_from_spec(_spec)
sys.modules["ainu_columns"] = ainu_columns
_spec.loader.exec_module(ainu_columns)


def column(x: int, ys: tuple[int, ...] = (100, 140, 180, 220), w: int = 30, h: int = 40) -> list[Box]:
    """One vertical line of four characters, centred on `x`."""
    return [Box(x=x - w // 2, y=y, w=w, h=h) for y in ys]


def row(page_id: str, document: str, columns: int, lines: int, *, body: int = 1,
        paired: int = 0) -> dict:
    """A measured page, as `page_row` writes one."""
    return {
        "page_id": page_id, "document_id": document, "width": 1350, "height": 1000,
        "characters": columns * 4, "columns": columns, "regions": 1, "region_columns": str(columns),
        "largest_region_columns": columns, "lines": lines, "text_lines": lines * 4,
        "text_characters": lines * 4, "body": body, "title": "t",
        "detections_per_character": 1.0, "paired": paired, "reason": "paired" if paired else "-",
    }


@pytest.fixture
def args() -> object:
    """The script's own options, with the defaults the census quotes."""
    return type("Args", (), {"body_lines": ainu.BODY_LINES,
                             "min_per_character": ainu.MIN_DETECTIONS_PER_CHARACTER})


def test_the_summary_counts_exact_and_within_a_quarter(args: object) -> None:
    """A page counts as exact when its column count is its line count, and within 25% around that."""
    rows = [
        row("p1", "w1", 10, 10, paired=1),   # exact
        row("p2", "w1", 11, 10),             # within 25%
        row("p3", "w1", 20, 10),             # neither
        row("p4", "w2", 4, 5),               # within 25%
        row("p5", "w2", 3, 1, body=0),       # a title, not evidence
    ]
    summary = ainu_columns.summarize(rows, args)
    assert "pages 5  witnesses 2" in summary
    assert "body transcription (>= 4 lines) 4, title-only or empty 1" in summary
    assert "all pages: exact 1/5, within 25% 3/5" in summary
    assert "body pages: exact 1/4, within 25% 3/4" in summary
    assert "pages the derivation would pair 1" in summary
    assert "count match 1" in summary


def test_the_summary_separates_the_witnesses(args: object) -> None:
    """The per-witness spread is why the census is a filter and not a verdict."""
    rows = [row("p1", "good", 10, 10), row("p2", "good", 9, 10),
            row("p3", "poor", 20, 10), row("p4", "poor", 30, 10)]
    summary = ainu_columns.summarize(rows, args)
    assert "good  1/2/2" in summary
    assert "poor  0/0/2" in summary


def test_a_page_becomes_a_row_the_report_can_quote() -> None:
    """The row holds the counts, the evidence and whether the page was paired."""
    boxes = column(100) + column(150)
    lines = [Line(id="p:L0", page_id="p", seq=0, text_raw="ああああ", text="ああああ"),
             Line(id="p:L1", page_id="p", seq=1, text_raw="いいいい", text="いいいい")]
    page = Page(id="p", document_id="d", seq=0, canvas="c", image="i", width=1350, height=1000)
    derivation = ainu.derive_page(page, lines, boxes, body_lines=2)
    measured = ainu.page_row(derivation, page, lines)
    assert measured["columns"] == 2 and measured["lines"] == 2
    assert measured["characters"] == 8 and measured["detections_per_character"] == 1.0
    assert measured["paired"] == 1 and measured["reason"] == "paired"
    assert list(measured) == list(ainu.COLUMNS_FIELDS), "the row matches the TSV's columns"
