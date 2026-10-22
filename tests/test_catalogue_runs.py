import sqlite3
import sys
from pathlib import Path

import pytest

from glyph_atlas import ngrams, tables, withdrawn
from glyph_atlas.corpus.sources import Corpus
from glyph_atlas.ngrams import Glyph, Place, Run, Written, adjacent_ngrams, in_reading_order
from glyph_atlas.schema import Box, Line, Unit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import catalogue
import export_cloudflare_corpus as export


def glyph(i, seq, y, line="L", x=0):
    return Glyph(f"{line}:{i}", line, seq, "char", "char", Place(x, y, 36, 36), True)


def test_an_aligned_line_is_read_by_its_boxes_not_its_numbers():
    # Numbered 0,1,2 but standing 2,0,1 down the column: the boxes say 1 follows 0 and 2 follows 1.
    scrambled = [glyph("a", 0, 80), glyph("b", 1, 0), glyph("c", 2, 40)]
    placed = in_reading_order(scrambled)
    assert sorted((g.id, g.seq) for g in placed) == [("L:a", 2), ("L:b", 0), ("L:c", 1)]
    assert sorted(run.units for run in adjacent_ngrams(placed)) == [("L:b", "L:c"), ("L:b", "L:c", "L:a"), ("L:c", "L:a")]


def test_a_line_numbered_in_box_order_keeps_its_gaps():
    # 大, its reading rejected (no unit at 1), then 小: the gap at 1 keeps them from making a run.
    gapped = [glyph("a", 0, 0), glyph("c", 2, 50)]
    placed = in_reading_order(gapped)
    assert sorted((g.id, g.seq) for g in placed) == [("L:a", 0), ("L:c", 2)]
    assert adjacent_ngrams(placed) == []


def test_a_space_the_aligner_kept_without_a_box_does_not_break_a_run():
    spaced = [glyph("a", 1, 0), Glyph("L:space", "L", 2, "char", "char", None, True), glyph("c", 3, 40)]
    assert [run.units for run in adjacent_ngrams(in_reading_order(spaced))] == [("L:a", "L:c")]


def test_a_shared_number_reads_the_same_whatever_order_the_glyphs_come_in():
    a, b, c = glyph("a", 0, 0), glyph("b", 0, 40), glyph("c", 2, 80)
    assert sorted(in_reading_order([a, b, c])) == sorted(in_reading_order([b, a, c]))


def test_glyphs_sharing_a_box_share_its_place_and_a_glyph_without_one_has_none():
    shared = [glyph("a", 0, 0), glyph("b", 1, 40), glyph("c", 2, 40), glyph("d", 3, 80),
              Glyph("L:e", "L", 4, "char", "char", None, True)]
    placed = in_reading_order(shared)
    assert sorted((g.id, g.seq) for g in placed) == [("L:a", 0), ("L:b", 1), ("L:c", 1), ("L:d", 2)]
    assert adjacent_ngrams(placed) == []


def test_a_line_written_across_is_read_left_to_right():
    across = [glyph("a", 0, 0, x=80), glyph("b", 1, 0, x=0), glyph("c", 2, 0, x=40)]
    assert [g.id for g in sorted(in_reading_order(across, {"L"}), key=lambda g: g.seq)] == ["L:b", "L:c", "L:a"]


def export_db():
    db = sqlite3.connect(":memory:")
    catalogue.schema(db)
    return db


def held(db, glyphs, document="book", production="unknown"):
    db.executemany("INSERT INTO corpus_units(id,character,shuffle,object,offset,size,document,production) VALUES(?,?,7,'pack',0,1,?,?)",
                   [(i, c, document, production) for i, c in glyphs])


def runs_of(db):
    return db.execute("SELECT first,size,second,third,text,document,vertical,hand_order,shuffle,graphemes"
                      " FROM unit_ngrams ORDER BY first,size").fetchall()


def written(db, table="corpus_units"):
    return {i: Written(*rest) for i, *rest in db.execute(
        f"SELECT id,coalesce(character,{'label' if table == 'corpus_units' else 'NULL'}),document,hand_order,shuffle FROM {table}")}


def test_a_run_reads_its_glyphs_characters_and_its_first_glyphs_document_order_and_shuffle():
    db = export_db()
    held(db, [("c:0", "申"), ("c:1", "上"), ("c:2", "候")], production="handwritten")
    runs = [Run(("c:0", "c:1"), True), Run(("c:1", "c:2"), False), Run(("c:0", "c:1", "c:2"), True)]
    assert ngrams.record_runs(db, runs, written(db)) == 3
    assert runs_of(db) == [("c:0", 2, "c:1", None, "申上", "book", 1, 0, 7, "申上"),
                           ("c:0", 3, "c:1", "c:2", "申上候", "book", 1, 0, 7, "申上候"),
                           ("c:1", 2, "c:2", None, "上候", "book", 0, 0, 7, "上候")]


def test_a_runs_graphemes_fold_each_character_to_its_family_head():
    db = export_db()
    held(db, [("c:0", "ん"), ("c:1", "𛁅"), ("c:2", "ケ")])
    ngrams.record_runs(db, [Run(("c:0", "c:1", "c:2"), True)], written(db))
    assert db.execute("SELECT text,graphemes FROM unit_ngrams").fetchone() == ("ん𛁅ケ", "んしけ")


def test_a_run_with_a_glyph_the_catalogue_does_not_hold_is_not_recorded():
    db = export_db()
    held(db, [("c:0", "申"), ("c:2", "候")])
    runs = [Run(("c:0", "c:1"), True), Run(("c:1", "c:2"), True), Run(("c:0", "c:1", "c:2"), True)]
    assert ngrams.record_runs(db, runs, written(db)) == 0


def test_a_glyph_with_no_character_gives_its_runs_no_text():
    db = export_db()
    held(db, [("c:0", "申"), ("c:1", None)])
    ngrams.record_runs(db, [Run(("c:0", "c:1"), True)], written(db))
    assert db.execute("SELECT text,graphemes FROM unit_ngrams").fetchone() == (None, None)


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
    runs, placed, _ = export.corpus_runs(corpus(tmp_path, units, lines))
    assert sorted(run.units for run in runs) == [("A:1", "A:0"), ("K:0", "K:1")]
    assert sorted(placed) == ["A:0", "A:1", "K:0", "K:1"]


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
    runs, placed, _ = export.corpus_runs(corpus(tmp_path, units))
    assert sorted(run.units for run in runs) == [(f"{page}:B0001:C0001", f"{page}:B0001:C0002"), (f"{page}:B0001:C0003", f"{page}:B0001:C0004")]
    assert len(placed) == 4


@pytest.fixture
def labelled(tmp_path):
    units = [unit("kept", "K", 0, 0), unit("kept", "K", 1, 40), unit("kept", "K", 2, 80), unit("kept", "L", 0, 0)]
    units = [units[0].model_copy(update={"unicode": "U+3093", "text_source": "ん"}), units[1].model_copy(update={"text_source": "し"}),
             units[2].model_copy(update={"text_source": "候也"}), units[3].model_copy(update={"text_source": "孤"})]
    return corpus(tmp_path, units)


def test_an_export_labels_the_glyphs_its_runs_place_and_records_their_runs(labelled):
    db = export_db()
    held(db, [("K:0", None), ("K:1", None), ("K:2", None), ("L:0", None)], document="kept")
    for _ in range(2):
        export.record_corpus_runs(db, labelled)
    # A transcription of two characters stands for no crop, and a glyph in no run needs no label.
    assert db.execute("SELECT first,size,text,graphemes FROM unit_ngrams ORDER BY first,size").fetchall() == [
        ("K:0", 2, "んし", "んし"), ("K:0", 3, None, None), ("K:1", 2, None, None)]
    assert dict(db.execute("SELECT id,label FROM corpus_units")) == {"K:0": "ん", "K:1": "し", "K:2": None, "L:0": None}


def test_an_export_keeps_a_written_character_over_the_label(labelled):
    db = export_db()
    held(db, [("K:0", "ゑ"), ("K:1", None), ("K:2", "候")], document="kept")
    export.record_corpus_runs(db, labelled)
    assert dict(db.execute("SELECT id,label FROM corpus_units")) == {"K:0": None, "K:1": "し", "K:2": None}
    assert db.execute("SELECT text FROM unit_ngrams WHERE size=2 ORDER BY first").fetchall() == [("ゑし",), ("し候",)]


def test_an_export_reads_a_glyphs_review_overlay_over_its_published_row(tmp_path):
    db = export_db()
    held(db, [("K:0", "申"), ("K:1", "上"), ("K:2", None)], document="kept")
    # K:0 reviewed to 中 in a cursive hand; K:2 reviewed with no character keeps its label.
    db.executemany("INSERT INTO units(id,origin,character,production,category,state,revision,quiz,priority,shuffle,"
                   "data,snapshot,context,visual,style) VALUES(?,'corpus',?,'printed/woodblock','kanji','accepted',1,1,1,?,"
                   "'{}','{}','{}','{}',?)", [("K:0", "中", 3, "cursive"), ("K:2", None, 9, "unassessed")])
    units = [unit("kept", "K", 0, 0), unit("kept", "K", 1, 40),
             unit("kept", "K", 2, 80).model_copy(update={"text_source": "候"})]
    export.record_corpus_runs(db, corpus(tmp_path, units))
    assert db.execute("SELECT first,size,text,hand_order,shuffle FROM unit_ngrams ORDER BY first,size").fetchall() == [
        ("K:0", 2, "中上", 0, 3), ("K:0", 3, "中上候", 0, 3), ("K:1", 2, "上候", 1, 7)]


def test_an_export_run_again_replaces_its_corpus_runs_and_leaves_the_others(tmp_path):
    db = export_db()
    held(db, [("K:0", "申"), ("K:1", "上"), ("K:2", "候")], document="kept")
    db.execute("INSERT INTO unit_ngrams(first,size,second,text) VALUES('K:1',3,'gone','x'),('other:0',2,'other:1','y')")
    export.record_corpus_runs(db, corpus(tmp_path, [unit("kept", "K", 0, 0), unit("kept", "K", 1, 40), unit("kept", "K", 2, 80)]))
    assert db.execute("SELECT first,size,text FROM unit_ngrams ORDER BY first,size").fetchall() == [
        ("K:0", 2, "申上"), ("K:0", 3, "申上候"), ("K:1", 2, "上候"), ("other:0", 2, "y")]


def test_a_catalogue_holds_no_trigger_but_the_ledgers_and_refuses_one_made_with_the_site_migrations():
    db = export_db()
    assert {r[0] for r in db.execute("SELECT DISTINCT tbl_name FROM sqlite_master WHERE type='trigger'")} == {
        "assertions", "assertion_evidence", "assertion_premises", "assertion_actions", "forms", "representations"}
    catalogue.schema(db)
    db.execute("CREATE TRIGGER t AFTER INSERT ON units BEGIN SELECT 1; END")
    with pytest.raises(ValueError, match="export it again"):
        catalogue.schema(db)
