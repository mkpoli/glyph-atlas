import json
import sqlite3
import sys
from pathlib import Path

from glyph_atlas import tables, withdrawn
from glyph_atlas.corpus.sources import Corpus
from glyph_atlas.ngrams import (
    Glyph,
    Place,
    Run,
    adjacent_ngrams,
    corpus_ngram_statements,
    id_ranges,
    in_reading_order,
)
from glyph_atlas.schema import Box, Line, Unit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import export_corpus_ngrams as export

SCHEMA = "\n".join(p.read_text() for p in sorted((ROOT / "apps/cloudflare/migrations").glob("*.sql")))


def glyph(i, seq, y, line="L", x=0):
    return Glyph(f"{line}:{i}", line, seq, "char", "char", Place(x, y, 36, 36), True)


def test_an_aligned_line_is_read_by_its_boxes_not_its_numbers():
    # Numbered 0,1,2 but standing 2,0,1 down the column: the boxes say 1 follows 0 and 2 follows 1.
    scrambled = [glyph("a", 0, 80), glyph("b", 1, 0), glyph("c", 2, 40)]
    placed = in_reading_order(scrambled)
    assert sorted((g.id, g.seq) for g in placed) == [("L:a", 2), ("L:b", 0), ("L:c", 1)]
    assert sorted(run.units for run in adjacent_ngrams(placed)) == [("L:b", "L:c"), ("L:b", "L:c", "L:a"), ("L:c", "L:a")]


def test_glyphs_sharing_a_box_share_its_place_and_a_glyph_without_one_has_none():
    shared = [glyph("a", 0, 0), glyph("b", 1, 40), glyph("c", 2, 40), glyph("d", 3, 80),
              Glyph("L:e", "L", 4, "char", "char", None, True)]
    placed = in_reading_order(shared)
    assert sorted((g.id, g.seq) for g in placed) == [("L:a", 0), ("L:b", 1), ("L:c", 1), ("L:d", 2)]
    assert adjacent_ngrams(placed) == []


def test_a_line_written_across_is_read_left_to_right():
    across = [glyph("a", 0, 0, x=80), glyph("b", 1, 0, x=0), glyph("c", 2, 0, x=40)]
    assert [g.id for g in sorted(in_reading_order(across, {"L"}), key=lambda g: g.seq)] == ["L:b", "L:c", "L:a"]


def site():
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    return db


def publish(db, glyphs, document="book"):
    db.executemany("INSERT INTO corpus_units(id,character,shuffle,object,offset,size,document) VALUES(?,?,0,'pack',0,1,?)",
                   [(i, c, document) for i, c in glyphs])


def apply(db, statements):
    db.executescript("\n".join(statements))


def test_a_run_is_recorded_with_its_glyphs_characters_as_the_site_holds_them():
    db = site()
    publish(db, [("c:0", "申"), ("c:1", "上"), ("c:2", "候")])
    # A round named c:1 and its reviewer read it as 下.
    db.execute("INSERT INTO units(id,origin,character,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual)"
               " VALUES('c:1','corpus','下','unknown','han','checked',1,0,1,0,'{}','{}','{}','{}')")
    runs = [Run(("c:0", "c:1"), True), Run(("c:1", "c:2"), False), Run(("c:0", "c:1", "c:2"), True)]
    apply(db, corpus_ngram_statements(id_ranges(["c:0", "c:1", "c:2"], 2), runs))
    assert db.execute("SELECT first,size,second,third,text,document,vertical FROM unit_ngrams ORDER BY first,size").fetchall() == [
        ("c:0", 2, "c:1", None, "申下", "book", 1), ("c:0", 3, "c:1", "c:2", "申下候", "book", 1), ("c:1", 2, "c:2", None, "下候", "book", 0)]


def test_a_run_with_a_glyph_the_site_does_not_hold_is_not_recorded():
    db = site()
    publish(db, [("c:0", "申"), ("c:2", "候")])
    apply(db, corpus_ngram_statements(id_ranges(["c:0", "c:1", "c:2"], 10),
                                      [Run(("c:0", "c:1"), True), Run(("c:1", "c:2"), True), Run(("c:0", "c:1", "c:2"), True)]))
    assert db.execute("SELECT count(*) FROM unit_ngrams").fetchone() == (0,)


def test_applying_again_replaces_the_runs_of_the_glyphs_and_leaves_the_collections():
    db = site()
    publish(db, [("c:0", "申"), ("c:1", "上"), ("c:2", "候")])
    db.execute("INSERT INTO unit_ngrams(first,size,second,text,document) VALUES('ex:0',2,'ex:1','申候','local-book')")
    apply(db, corpus_ngram_statements(id_ranges(["c:0", "c:1", "c:2"], 2), [Run(("c:0", "c:1"), True), Run(("c:1", "c:2"), True)]))
    # The line was cut anew: c:1 is now followed by nothing, c:0 by c:2.
    again = corpus_ngram_statements(id_ranges(["c:0", "c:1", "c:2"], 2), [Run(("c:0", "c:2"), True)])
    apply(db, again)
    apply(db, again)
    assert db.execute("SELECT first,second,text FROM unit_ngrams ORDER BY first").fetchall() == [
        ("c:0", "c:2", "申候"), ("ex:0", "ex:1", "申候")]


def corpus(tmp_path, units, lines=()):
    tables.write(tmp_path / "units.parquet", units, Unit)
    if lines:
        tables.write(tmp_path / "lines.parquet", list(lines), Line)
    return Corpus(name="test", directory=tmp_path, has_units=True)


def unit(document, line, seq, y, method="import"):
    return Unit(id=f"{line}:{seq}", document_id=document, line_id=line, seq=seq, box=Box(x=0, y=y, w=36, h=36), method=method)


def test_a_corpus_runs_its_lines_and_leaves_withdrawn_documents_out(tmp_path, monkeypatch):
    monkeypatch.setattr(withdrawn, "documents", lambda: frozenset({"gone"}))
    units = [unit("kept", "K", 0, 0), unit("kept", "K", 1, 40),
             unit("kept", "A", 0, 40, "detect-align"), unit("kept", "A", 1, 0, "detect-align"),
             unit("gone", "G", 0, 0), unit("gone", "G", 1, 40)]
    lines = [Line(id=i, page_id="P", seq=n, text_raw="ab", text="ab") for n, i in enumerate("KAG")]
    runs, placed = export.corpus_runs(corpus(tmp_path, units, lines))
    assert sorted(run.units for run in runs) == [("A:1", "A:0"), ("K:0", "K:1")]
    assert sorted(placed) == ["A:0", "A:1", "K:0", "K:1"]


def test_the_parts_record_the_runs_and_can_be_applied_again(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    found = corpus(data, [unit("kept", "K", 0, 0), unit("kept", "K", 1, 40), unit("kept", "K", 2, 80)])
    monkeypatch.setattr(export, "unit_corpora", lambda names=None: [found])
    monkeypatch.setattr(sys, "argv", ["export_corpus_ngrams.py", str(tmp_path / "out")])
    export.main()
    db = site()
    publish(db, [("K:0", "申"), ("K:1", "上"), ("K:2", "候")])
    for _ in range(2):
        for part in sorted((tmp_path / "out" / "sql").glob("part-*.sql")):
            db.executescript(part.read_text())
    assert db.execute("SELECT first,size,text FROM unit_ngrams ORDER BY first,size").fetchall() == [
        ("K:0", 2, "申上"), ("K:0", 3, "申上候"), ("K:1", 2, "上候")]
    assert db.execute("SELECT count(*) FROM metadata WHERE key='units_refreshed_at'").fetchone() == (1,)


def test_the_summary_counts_each_corpus_by_length_and_direction():
    from collections import Counter

    assert export.summary(Counter({(2, True): 3, (2, False): 1, (3, True): 2})) == {
        "pairs": 4, "trigrams": 2, "horizontal_pairs": 1, "horizontal_trigrams": 0}
    assert json.dumps(export.summary(Counter()))



def test_a_codh_unit_takes_its_line_and_place_from_its_id():
    from glyph_atlas.importers.codh_all import reading_place

    page = "codh:100241706:100241706_00004_2"
    assert reading_place(f"{page}:B0001:C0012", page) == (f"{page}:B0001", 12)
    assert reading_place(f"{page}:report:3", page) is None
    assert reading_place("hi:34000001", None) is None


def test_codh_units_run_in_the_annotators_order(tmp_path):
    page = "codh:b:b_00001_1"
    # C1, C2 down one column and C3, C4 down the next, far from C2: the step between them breaks the run.
    boxes = {1: (500, 0), 2: (500, 40), 3: (400, 0), 4: (400, 40)}
    units = [Unit(id=f"{page}:B0001:C{n:04}", document_id="codh:b", page_id=page, box=Box(x=x, y=y + 1000 * (n == 3 or n == 4), w=36, h=36))
             for n, (x, y) in boxes.items()]
    runs, placed = export.corpus_runs(corpus(tmp_path, units))
    assert sorted(run.units for run in runs) == [(f"{page}:B0001:C0001", f"{page}:B0001:C0002"), (f"{page}:B0001:C0003", f"{page}:B0001:C0004")]
    assert len(placed) == 4
