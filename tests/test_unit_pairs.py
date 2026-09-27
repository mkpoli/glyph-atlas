import sqlite3
from pathlib import Path

from glyph_atlas.schema import Box, Unit, UnitKind
from glyph_atlas.unit_pairs import adjacent_pairs, pair_inserts

MIGRATION = Path(__file__).resolve().parents[1] / "apps/cloudflare/migrations/0028_unit_pairs.sql"


def unit(seq, y=None, line="L1", **fields):
    y = seq * 40 if y is None else y
    return Unit(id=f"{line}:{seq}{fields.pop('suffix', '')}", line_id=line, seq=seq,
                box=Box(x=0, y=y, w=36, h=36), **fields)


def test_pairs_follow_positions_on_a_line():
    units = [unit(0), unit(1), unit(2), unit(0, line="L2"), unit(1, line="L2")]
    assert sorted(adjacent_pairs(units)) == [("L1:0", "L1:1"), ("L1:1", "L1:2"), ("L2:0", "L2:1")]


def test_a_position_without_one_unit_breaks_the_run():
    # Position 2 has no crop, and position 5 has two.
    units = [unit(0), unit(1), unit(3), unit(4), unit(5), unit(5, suffix="b"), unit(6)]
    assert adjacent_pairs(units) == [("L1:0", "L1:1"), ("L1:3", "L1:4")]


def test_marks_gaps_runs_and_retired_units_are_never_paired():
    units = [unit(0), unit(1, kind=UnitKind.GAP), unit(2), unit(3, granularity="sequence"),
             unit(4), unit(5, active=False), unit(6), unit(7).model_copy(update={"box": None})]
    assert adjacent_pairs(units) == []


def test_units_too_far_apart_on_the_page_are_not_neighbours():
    # 36-pixel boxes: centres 57 pixels apart are neighbours, 58 are not (1.6 × 36 = 57.6).
    assert adjacent_pairs([unit(0, y=0), unit(1, y=57)]) == [("L1:0", "L1:1")]
    assert adjacent_pairs([unit(0, y=0), unit(1, y=58)]) == []


def test_d1_pairs_take_the_site_labels_and_follow_them():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE units (id TEXT PRIMARY KEY, origin TEXT, character TEXT, document TEXT)")
    db.executescript(MIGRATION.read_text())
    db.executemany("INSERT INTO units VALUES(?,?,?,?)", [
        ("a", "local", "申", "book"), ("b", "local", "候", "book"), ("c", "corpus", "也", None)])
    # A pair whose crop the site does not hold as its own is left out; one already recorded is kept.
    for statement in pair_inserts([("a", "b"), ("b", "c"), ("x", "a")]) * 2:
        db.execute(statement)
    assert db.execute("SELECT * FROM unit_pairs").fetchall() == [("a", "b", "申候", "book")]
    db.execute("UPDATE units SET character='中' WHERE id='a'")
    db.execute("UPDATE units SET character='條', document='other' WHERE id='b'")
    assert db.execute("SELECT text,document FROM unit_pairs").fetchall() == [("中條", "book")]
    db.execute("UPDATE units SET document='other' WHERE id='a'")
    assert db.execute("SELECT document FROM unit_pairs").fetchall() == [("other",)]
    db.execute("DELETE FROM units WHERE id='b'")
    assert db.execute("SELECT count(*) FROM unit_pairs").fetchone() == (0,)
