import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import export_character_variants as variants

from glyph_atlas import reference_input

NEVER = lambda: (_ for _ in ()).throw(AssertionError("derived forms computed"))


def test_a_collection_catalogue_is_filled_without_deriving_forms(monkeypatch):
    from cloudflare_schema import schema

    monkeypatch.setattr(variants, "derived_rows", NEVER)
    monkeypatch.setattr(variants, "ids_rows", lambda: (_ for _ in ()).throw(AssertionError("descriptions computed")))
    db = sqlite3.connect(":memory:")
    schema(db)
    variants.fill(db, derived=False)
    assert db.execute("SELECT count(*) FROM character_derived").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM character_variants").fetchone()[0] > 0


def written(path):
    db = sqlite3.connect(path)
    return {name for (name,) in db.execute("SELECT name FROM written")}, db


def test_the_words_replace_the_word_tables_alone(monkeypatch, tmp_path):
    monkeypatch.setattr(variants, "derived_rows", NEVER)
    path = tmp_path / "reference-input.sqlite"
    with reference_input.writing(path) as db:
        db.execute("INSERT INTO words(id,language,reading,class) VALUES('ja/古い/名詞','ja','ふるい','名詞')")
        reference_input.put_source(db, "variant_sources", '{"x":"y"}')
    monkeypatch.setattr(sys, "argv", ["export_character_variants.py", "--words", "--input", str(path)])
    variants.main()
    names, db = written(path)
    assert names == {"words", "word_spellings", "sources"}
    assert db.execute("SELECT count(*) FROM words").fetchone()[0] == len(variants.word_rows())
    assert db.execute("SELECT count(*) FROM words WHERE id='ja/古い/名詞'").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM word_spellings").fetchone()[0] == len(variants.spelling_rows())
    assert db.execute("SELECT count(*) FROM character_variants").fetchone()[0] == 0
    assert dict(db.execute("SELECT key, value FROM sources"))["variant_sources"] == '{"x":"y"}', "the graph's citations stay"
    assert "word_sources" in dict(db.execute("SELECT key, value FROM sources"))


def test_the_writers_replace_grapheme_writers_alone(monkeypatch, tmp_path):
    monkeypatch.setattr(variants, "derived_rows", NEVER)
    path = tmp_path / "reference-input.sqlite"
    with reference_input.writing(path) as db:
        db.execute("INSERT INTO grapheme_writers(writer,grapheme,own) VALUES('甲','乙',1)")
    monkeypatch.setattr(sys, "argv", ["export_character_variants.py", "--writers", "--input", str(path)])
    variants.main()
    names, db = written(path)
    assert names == {"grapheme_writers"}
    rows = set(db.execute("SELECT writer,grapheme FROM grapheme_writers"))
    assert ("后", "後") in rows and ("弁", "辯") in rows and ("甲", "乙") not in rows
    assert len(rows) == len(variants.writer_rows())
    assert db.execute("SELECT writer FROM grapheme_writers WHERE grapheme='後'").fetchall() == [("后",)]
    assert set(db.execute("SELECT grapheme,own FROM grapheme_writers WHERE writer='𠮮'")) == {("后", 0), ("𠯑", 0)}
    assert db.execute("SELECT own FROM grapheme_writers WHERE writer='后'").fetchall() == [(1,)]
