def test_a_collection_catalogue_is_filled_without_deriving_forms(monkeypatch):
    import sqlite3
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
    import export_character_variants as variants
    from cloudflare_schema import schema

    monkeypatch.setattr(variants, "derived_rows", lambda: (_ for _ in ()).throw(AssertionError("derived forms computed")))
    monkeypatch.setattr(variants, "ids_rows", lambda: (_ for _ in ()).throw(AssertionError("descriptions computed")))
    db = sqlite3.connect(":memory:")
    schema(db)
    variants.fill(db, derived=False)
    assert db.execute("SELECT count(*) FROM character_derived").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM character_variants").fetchone()[0] > 0


def test_the_words_part_replaces_the_word_tables_alone(monkeypatch, tmp_path):
    import json
    import sqlite3
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
    import export_character_variants as variants
    from cloudflare_schema import schema

    monkeypatch.setattr(variants, "derived_rows", lambda: (_ for _ in ()).throw(AssertionError("derived forms computed")))
    db = sqlite3.connect(":memory:")
    schema(db)
    db.execute("INSERT INTO words(id,language,reading,class) VALUES('ja/古い/名詞','ja','ふるい','名詞')")
    db.executescript("".join(variants.word_statements()))
    assert db.execute("SELECT count(*) FROM words").fetchone()[0] == len(variants.word_rows())
    assert db.execute("SELECT count(*) FROM words WHERE id='ja/古い/名詞'").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM word_spellings").fetchone()[0] == len(variants.spelling_rows())
    assert db.execute("SELECT count(*) FROM character_variants").fetchone()[0] == 0
    keys = {key for (key,) in db.execute("SELECT key FROM metadata")}
    assert {"word_sources", "units_refreshed_at"} <= keys
    monkeypatch.setattr(sys, "argv", ["export_character_variants.py", "--words", str(tmp_path)])
    variants.main()
    assert [path.name for path in (tmp_path / "sql").iterdir()] == ["part-001.sql"]
    expected = {"words": len(variants.word_rows()), "spellings": len(variants.spelling_rows())}
    assert json.dumps(expected, separators=(",", ":")) in (tmp_path / "apply.sh").read_text()


def test_the_writers_part_replaces_grapheme_writers_alone(monkeypatch, tmp_path):
    import json
    import sqlite3
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
    import export_character_variants as variants
    from cloudflare_schema import schema

    monkeypatch.setattr(variants, "derived_rows", lambda: (_ for _ in ()).throw(AssertionError("derived forms computed")))
    db = sqlite3.connect(":memory:")
    schema(db)
    db.execute("INSERT INTO grapheme_writers(writer,grapheme) VALUES('甲','乙')")
    db.executescript("".join(variants.writer_statements()))
    rows = set(db.execute("SELECT writer,grapheme FROM grapheme_writers"))
    assert ("后", "後") in rows and ("弁", "辯") in rows and ("甲", "乙") not in rows
    assert len(rows) == len(variants.writer_rows())
    assert db.execute("SELECT writer FROM grapheme_writers WHERE grapheme='後'").fetchall() == [("后",)]
    assert set(db.execute("SELECT grapheme,own FROM grapheme_writers WHERE writer='𠮮'")) == {("后", 0), ("𠯑", 0)}
    assert db.execute("SELECT own FROM grapheme_writers WHERE writer='后'").fetchall() == [(1,)]
    monkeypatch.setattr(sys, "argv", ["export_character_variants.py", "--writers", str(tmp_path)])
    variants.main()
    assert [path.name for path in (tmp_path / "sql").iterdir()] == ["part-001.sql"]
    apply = (tmp_path / "apply.sh").read_text()
    assert json.dumps({"writers": len(variants.writer_rows())}, separators=(",", ":")) in apply
    assert "FROM grapheme_writers" in apply and "TABLES" not in apply and "COUNT" not in apply
