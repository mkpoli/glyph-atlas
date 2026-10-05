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
