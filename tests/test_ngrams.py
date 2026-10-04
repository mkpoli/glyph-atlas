import sqlite3
from pathlib import Path

from glyph_atlas import tables
from glyph_atlas.ngrams import Run, adjacent_ngrams, ngram_statements
from glyph_atlas.review.store import Store
from glyph_atlas.schema import Box, Line, Unit, UnitKind

MIGRATIONS = sorted((Path(__file__).resolve().parents[1] / "apps/cloudflare/migrations").glob("*.sql"))


def unit(seq, y=None, line="L1", **fields):
    y = seq * 40 if y is None else y
    return Unit(id=f"{line}:{seq}{fields.pop('suffix', '')}", line_id=line, seq=seq,
                box=Box(x=0, y=y, w=36, h=36), **fields)


def catalogue():
    """`units` and `corpus_units` with the columns the ngram migrations' triggers read."""
    db = sqlite3.connect(":memory:")
    columns = ("character TEXT, document TEXT, production TEXT NOT NULL DEFAULT 'unknown', style TEXT NOT NULL DEFAULT 'unassessed', "
               "style_order INTEGER NOT NULL DEFAULT 1, shuffle INTEGER NOT NULL DEFAULT 0")
    db.execute(f"CREATE TABLE units (id TEXT PRIMARY KEY, origin TEXT, {columns})")
    db.execute(f"CREATE TABLE corpus_units (id TEXT PRIMARY KEY, {columns})")
    return db


def site(units):
    """A D1 catalogue with `units` as (id, origin, character, document) rows and the ngram migrations applied."""
    db = catalogue()
    db.execute("CREATE TABLE unit_pairs (first TEXT PRIMARY KEY, second TEXT NOT NULL, text TEXT, document TEXT)")
    for migration in MIGRATIONS:
        if "ngram" in migration.name:
            db.executescript(migration.read_text())
    db.executemany("INSERT INTO units(id,origin,character,document) VALUES(?,?,?,?)", units)
    return db


def ids(runs):
    return sorted(run.units for run in runs)


def test_runs_follow_positions_on_a_line():
    units = [unit(0), unit(1), unit(2), unit(0, line="L2"), unit(1, line="L2")]
    assert ids(adjacent_ngrams(units)) == [
        ("L1:0", "L1:1"), ("L1:0", "L1:1", "L1:2"), ("L1:1", "L1:2"), ("L2:0", "L2:1")]


def test_a_position_without_one_unit_breaks_the_run():
    # Position 2 has no crop, and position 5 has two.
    units = [unit(0), unit(1), unit(3), unit(4), unit(5), unit(5, suffix="b"), unit(6), unit(7)]
    assert ids(adjacent_ngrams(units)) == [("L1:0", "L1:1"), ("L1:3", "L1:4"), ("L1:6", "L1:7")]


def test_marks_gaps_runs_and_retired_units_are_never_part_of_one():
    units = [unit(0), unit(1, kind=UnitKind.GAP), unit(2), unit(3, granularity="sequence"),
             unit(4), unit(5, active=False), unit(6), unit(7).model_copy(update={"box": None})]
    assert adjacent_ngrams(units) == []


def test_a_trigram_needs_both_neighbours_near():
    # 36-pixel boxes: centres 57 pixels apart are neighbours, 58 are not (1.6 × 36 = 57.6).
    assert ids(adjacent_ngrams([unit(0, y=0), unit(1, y=57)])) == [("L1:0", "L1:1")]
    assert adjacent_ngrams([unit(0, y=0), unit(1, y=58)]) == []
    # The third crop is too far from the second: the first two still pair, the last two do not.
    assert ids(adjacent_ngrams([unit(0, y=0), unit(1, y=40), unit(2, y=100)])) == [("L1:0", "L1:1")]
    # Near the second, though far from the first: a trigram.
    assert ("L1:0", "L1:1", "L1:2") in ids(adjacent_ngrams([unit(0, y=0), unit(1, y=57), unit(2, y=114)]))


def test_a_run_is_written_the_way_its_line_is():
    across = [Unit(id=f"H:{i}", line_id="H", seq=i, box=Box(x=i * 40, y=0, w=36, h=36)) for i in range(3)]
    runs = adjacent_ngrams([*across, unit(0), unit(1)], horizontal={"H"})
    assert sorted(runs) == [Run(("H:0", "H:1"), False), Run(("H:0", "H:1", "H:2"), False), Run(("H:1", "H:2"), False),
                            Run(("L1:0", "L1:1"), True)]
    # A line no one recorded as written across is read down the page, as `Line.vertical` defaults.
    assert {run.vertical for run in adjacent_ngrams(across)} == {True}


def test_d1_runs_take_the_site_labels_and_follow_them():
    db = site([("a", "local", "申", "book"), ("b", "local", "候", "book"), ("c", "local", "也", "book"),
               ("d", "corpus", "之", None)])
    # A run whose crop the site does not hold as its own is left out; one already recorded is kept.
    runs = [Run(("a", "b"), True), Run(("a", "b", "c"), False), Run(("b", "c"), True), Run(("b", "c", "d"), True),
            Run(("x", "a"), True)]
    for statement in ngram_statements(["a", "b", "x"], runs) * 2:
        db.execute(statement)
    assert db.execute("SELECT first,size,second,third,text,document,vertical FROM unit_ngrams ORDER BY first,size").fetchall() == [
        ("a", 2, "b", None, "申候", "book", 1), ("a", 3, "b", "c", "申候也", "book", 0), ("b", 2, "c", None, "候也", "book", 1)]
    db.execute("UPDATE units SET character='中' WHERE id='a'")
    db.execute("UPDATE units SET character='條', document='other' WHERE id='b'")
    db.execute("UPDATE units SET character='乎' WHERE id='c'")
    assert db.execute("SELECT text,document FROM unit_ngrams ORDER BY first,size").fetchall() == [
        ("中條", "book"), ("中條乎", "book"), ("條乎", "other")]
    db.execute("DELETE FROM units WHERE id='c'")
    assert db.execute("SELECT first,size FROM unit_ngrams").fetchall() == [("a", 2)]
    db.execute("DELETE FROM units WHERE id='b'")
    assert db.execute("SELECT count(*) FROM unit_ngrams").fetchone() == (0,)


def test_a_later_publication_replaces_a_units_runs():
    db = site([(i, "local", c, "book") for i, c in zip("abcd", "申候也之")])
    for statement in ngram_statements("abc", [Run(("a", "b"), True), Run(("a", "b", "c"), True), Run(("b", "c"), True)]):
        db.execute(statement)
    # Resegmented: a is now followed by c, and b has no successor.
    for statement in ngram_statements("abc", [Run(("a", "c"), True), Run(("a", "c", "d"), True)]):
        db.execute(statement)
    assert db.execute("SELECT first,second,third,text FROM unit_ngrams ORDER BY first,size").fetchall() == [
        ("a", "c", None, "申也"), ("a", "c", "d", "申也之")]


def test_the_pairs_recorded_before_carry_over():
    db = catalogue()
    for migration in MIGRATIONS:
        if migration.name.startswith("0028"):
            db.executescript(migration.read_text())
    db.execute("INSERT INTO unit_pairs VALUES('a','b','申候','book')")
    for migration in MIGRATIONS:
        if "ngram" in migration.name:
            db.executescript(migration.read_text())
    assert db.execute("SELECT first,size,second,third,text,document,vertical FROM unit_ngrams").fetchall() == [
        ("a", 2, "b", None, "申候", "book", 1)]
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'unit_pair%'").fetchall()


def test_the_review_store_names_the_lines_written_across(tmp_path):
    tables.write(tmp_path / "lines.parquet", [Line(id="H", page_id="P", seq=0, text_raw="ab", text="ab", vertical=False),
                                              Line(id="V", page_id="P", seq=1, text_raw="cd", text="cd")], Line)
    assert Store(tmp_path).horizontal_lines() == {"H"}


def test_a_run_is_placed_by_how_its_first_letterforms_were_made():
    db = site([])
    rows = [("w", "printed/woodblock", "unassessed"), ("c", "unknown", "cursive"), ("u", "unknown", "unassessed"),
            ("m", "mixed", "regular"), ("t", "printed/type/wood", "running"), ("s", "inscribed/stone", "unassessed"),
            ("p", "printed", "unassessed"), ("d", "printed/digital", "unassessed")]
    db.executemany("INSERT INTO units(id,origin,character,production,style) VALUES(?,'local','字',?,?)", rows)
    db.executemany("INSERT INTO unit_ngrams(first,size,second,text) VALUES(?,2,'x','字字')", [(i,) for i, *_ in rows])
    placed = lambda: dict(db.execute("SELECT first,hand_order FROM unit_ngrams"))
    assert placed() == {"w": 0, "c": 0, "u": 1, "m": 1, "t": 2, "s": 0, "p": 0, "d": 2}
    # A publication rewrites a crop's production in place; a corpus glyph without a row follows its published one.
    db.execute("UPDATE units SET production='printed/type' WHERE id='w'")
    db.execute("INSERT INTO corpus_units(id,character,production) VALUES('k','字','handwritten')")
    db.execute("INSERT INTO unit_ngrams(first,size,second,text) VALUES('k',2,'x','字字')")
    db.execute("UPDATE corpus_units SET production='printed/type' WHERE id='k'")
    assert (placed()["w"], placed()["k"]) == (2, 2)
