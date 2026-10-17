"""Tests for the crops whose character differs from their source text."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from glyph_atlas import tables
from glyph_atlas.review import sense_candidates
from glyph_atlas.schema import ReviewState, Ruby, Unit


def _unit(ident: str, text: str, unicode: str, review: ReviewState = ReviewState.REVIEWED, **fields) -> Unit:
    return Unit(id=ident, document_id="d1", page_id="p1", text_source=text, unicode=unicode, review=review, **fields)


def _store(directory: Path, units: list[Unit]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(directory / "review.sqlite") as conn:
        conn.execute("CREATE TABLE units (id TEXT PRIMARY KEY, document_id TEXT, page_id TEXT, line_id TEXT, seq INTEGER, "
                     "active INTEGER NOT NULL, data TEXT NOT NULL)")
        conn.executemany("INSERT INTO units VALUES (?,?,?,?,?,?,?)",
                         [(u.id, u.document_id, u.page_id, u.line_id, u.seq, int(u.active), u.model_dump_json()) for u in units])
    return directory


def test_a_reviewed_crop_filed_under_another_han_character_is_a_candidate(tmp_path):
    # A directory name a URI would read as a fragment or a query.
    directory = _store(tmp_path / "work#1?x", [
        _unit("u1", "鯡", "U+29E49", line_id="l1", method="detect-align"),
        _unit("u2", "鯡", "U+29E49", meta={"ainu_records": {"origin": "ocr", "context": "此島の鯡"}}),
        # Same character, a machine label, a kana, a retired and a rejected unit are not candidates.
        _unit("u3", "鯡", "U+9BE1"),
        _unit("u4", "鯡", "U+29E49", review=ReviewState.MACHINE),
        _unit("u5", "に", "U+29E49"),
        _unit("u6", "鯡", "U+29E49", active=False),
        _unit("u7", "鯡", "U+29E49", review=ReviewState.REJECTED),
    ])
    tables.write(directory / "rubies.parquet", [Ruby(id="l1:ruby0:right", page_id="p1", line_id="l1", base="鯡",
                                                     base_unit_ids=["u1"], side="right", text="にしん", reading="にしん",
                                                     span="mono", source="legacy")], Ruby)
    found = sense_candidates.candidates(directory)
    assert [(row.unit, row.basis, row.rubies, row.context) for row in found] == [
        ("u1", "transcription", "鯡: にしん", ""), ("u2", "ocr", "", "此島の鯡")]
    assert {row.relation for row in found} == {"specialized-semantic,variant"}
    assert sense_candidates.pairs(found) == [("𩹉", "鯡", 2, 1)]
    assert sense_candidates.candidates(directory, characters=["鮑"]) == []
    out = tmp_path / "out.tsv"
    sense_candidates.write(found, out)
    assert out.read_text(encoding="utf-8").splitlines()[0].split("\t") == list(sense_candidates.COLUMNS)
