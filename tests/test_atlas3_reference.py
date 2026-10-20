import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_publish import ids, layout, write_source  # noqa: F401 -- `layout` is the fixture

CARD = ("code_point", "character", "name", "data", "detail", "head")


def card(char, head=None, detail=None):
    point = " ".join(f"U+{ord(c):04X}" for c in char)
    return (point, char, f"NAME {point}", json.dumps({"char": char}), json.dumps(detail or {"char": char}, ensure_ascii=False),
            head and f"U+{ord(head):04X}")


def group(*crops):
    return {"id": "U+3042:01", "representatives": [{"id": c, "image": f"/layers/visual-groups/samples/{c}/image",
                                                    "source_label": "あ"} for c in crops],
            "anchors": [{"id": crops[0], "position": 1}]}


def sources(root, cards=(), variants=(), derived=(), writers=(), representations=()):
    for table in ("characters", "character_variants", "character_derived", "grapheme_writers", "component_variants", "words",
                  "word_spellings", "representations", "han_ids", "han_components", "han_component_names"):
        (root / f"source-{table}.sqlite").unlink(missing_ok=True)
    write_source(root, "characters", CARD, cards)
    write_source(root, "character_variants", ("a", "b", "relation", "source", "detail", "written", "widens"), variants)
    write_source(root, "character_derived", ("a", "forms"), derived)
    write_source(root, "grapheme_writers", ("writer", "grapheme", "own"), writers)
    write_source(root, "component_variants", ("a", "b", "count", "pairs"), [("氵", "水", 3, "[]")])
    write_source(root, "words", ("id", "language", "reading", "class"), [("ja/ばかり/副助詞", "ja", "ばかり", "副助詞")])
    write_source(root, "word_spellings", ("word", "spelling", "source", "locator", "tier", "word_by", "basis", "related",
                                          "statement", "documents", "occurrences"),
                 [("ja/ばかり/副助詞", "許", "dictionary", "x", "attested", "source", "", "", "許（ばかり）", None, None)])
    write_source(root, "representations", ("id", "scheme", "value", "namespace", "version"), representations)
    write_source(root, "han_ids", ("char", "sequences", "shapes"), [("好", '["⿰女子"]', None)])
    write_source(root, "han_components", ("component", "tier", "size", "code_point", "n", "direct"), [("女", 0, 2, "U+597D", 1, 1)])
    write_source(root, "han_component_names", ("query", "component", "total"), [("女", "女", 1)])


def load(remote, monkeypatch, *flags):
    for name in ("reference", "load_history"):
        sys.modules.pop(name, None)
    module = importlib.import_module("reference")
    history = importlib.import_module("load_history")
    applied = lambda database, stamp: bool(remote.query(database, f"SELECT 1 AS n FROM imports WHERE stamp = '{stamp}'"))
    for m in (module, history):
        monkeypatch.setattr(m, "send", remote.send)
        monkeypatch.setattr(m, "applied", applied)
    monkeypatch.setattr(sys, "argv", ["reference.py", *flags])
    module.main()
    remote.dbs["glyph-atlas-v3"].row_factory = None


def grapheme(db, text):
    return db.execute("SELECT id FROM graphemes WHERE text=?", (text,)).fetchone()[0]


def test_cards_and_variants_name_graphemes_and_visual_groups_name_crops(layout, monkeypatch):
    root, _publish, remote = layout
    sources(root, cards=[card("あ", detail={"char": "あ", "visual_analysis": {"groups": [group("ex:a1", "gone:1", "ex:b1")]}}),
                         card("𛀂", head="あ")],
            variants=[("あ", "𛀂", "variant", "test", "", 1, 1), ("あ", "安", "source", "test", "", 0, 0)],
            derived=[("あ", '[{"form":"安"}]')], writers=[("𛀂", "あ", 0)])
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    before = local.execute("SELECT max(id) FROM graphemes").fetchone()[0]
    load(remote, monkeypatch, "--send")
    db, mirror = remote.dbs["glyph-atlas-v3"], ids(root)
    a, hentaigana, an = grapheme(db, "あ"), grapheme(db, "𛀂"), grapheme(db, "安")
    # The layout's characters keep their numbers; new ones follow in the order first named, with the card's head.
    assert a <= before and (hentaigana, an) == (before + 1, before + 2)
    assert db.execute("SELECT head FROM graphemes WHERE id=?", (hentaigana,)).fetchone() == (a,)
    assert db.execute("SELECT grapheme, code_point FROM characters ORDER BY grapheme").fetchall() == [(a, "U+3042"), (hentaigana, "U+1B002")]
    assert sorted(db.execute("SELECT a, b, relation FROM character_variants")) == [(a, hentaigana, "variant"), (a, an, "source")]
    assert db.execute("SELECT a FROM character_derived").fetchall() == [(a,)]
    assert db.execute("SELECT writer, grapheme, own FROM grapheme_writers").fetchall() == [(hentaigana, a, 0)]
    # A visual group's crops are integer ids in their list order; the one the mirror lacks leaves the list.
    assert db.execute("SELECT grp, role, rank, crop FROM visual_samples WHERE grapheme=? ORDER BY role, rank", (a,)).fetchall() == [
        (0, 0, 0, mirror["ex:a1"]), (0, 0, 1, mirror["ex:b1"]), (0, 1, 0, mirror["ex:a1"])]
    detail = db.execute("SELECT detail FROM characters WHERE grapheme=?", (a,)).fetchone()[0]
    assert "ex:a1" not in detail and "gone:1" not in detail
    assert json.loads(detail)["visual_analysis"]["groups"][0]["representatives"] == [{"source_label": "あ"}] * 2
    assert db.execute("SELECT count(*) FROM han_components").fetchone() == (1,)
    assert "crops in visual groups the mirror lacks: 1" in (root / "out" / "reference-report.txt").read_text()
    # The local copy replays the same parts, and a rerun sends nothing.
    assert local.execute("SELECT count(*) FROM characters").fetchone() == (2,)
    stamps = db.execute("SELECT count(*) FROM imports").fetchone()
    load(remote, monkeypatch, "--send")
    assert db.execute("SELECT count(*) FROM imports").fetchone() == stamps


def test_a_dry_run_writes_the_parts_and_changes_nothing(layout, monkeypatch):
    root, _publish, remote = layout
    sources(root, cards=[card("𛀂", head="あ")])
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    graphemes = local.execute("SELECT count(*) FROM graphemes").fetchone()
    load(remote, monkeypatch)
    assert remote.dbs["glyph-atlas-v3"].execute("SELECT count(*) FROM characters").fetchone() == (0,)
    assert local.execute("SELECT count(*) FROM graphemes").fetchone() == graphemes
    assert list((root / "out" / "reference-load").glob("reference-*/rows-0000.sql"))


def test_a_card_over_the_statement_limit_arrives_whole(layout, monkeypatch):
    root, _publish, remote = layout
    long = {"char": "あ", "derived": ["字" * 40_000]}
    sources(root, cards=[card("あ", detail=long)])
    load(remote, monkeypatch, "--send")
    detail = remote.dbs["glyph-atlas-v3"].execute("SELECT detail FROM characters").fetchone()[0]
    assert json.loads(detail) == long


def test_migration_0004_is_applied_once_where_the_layout_lacks_it(layout, monkeypatch):
    root, _publish, remote = layout
    sources(root, cards=[card("あ")])
    drop = ";".join(f"DROP TABLE {t}" for t in ("characters", "visual_samples", "character_variants", "character_derived",
                                                "grapheme_writers", "component_variants", "words", "word_spellings",
                                                "representations", "han_ids", "han_components", "han_component_names"))
    for db in (sqlite3.connect(root / "out" / "atlas.sqlite"), remote.dbs["glyph-atlas-v3"]):
        db.executescript(drop)
    load(remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    assert db.execute("SELECT count(*) FROM imports WHERE stamp='migration:0004'").fetchone() == (1,)
    assert db.execute("SELECT count(*) FROM characters").fetchone() == (1,)


def test_another_translation_replaces_the_load_and_keeps_representations(layout, monkeypatch):
    root, _publish, remote = layout
    rp = [("rp:a", "ids", "⿱日𤴓", None, None)]
    sources(root, cards=[card("あ")], representations=rp)
    load(remote, monkeypatch, "--send")
    sources(root, cards=[card("い")], representations=[*rp, ("rp:b", "unicode", "昰", None, None)])
    with pytest.raises(SystemExit, match="--replace"):
        load(remote, monkeypatch, "--send")
    load(remote, monkeypatch, "--send", "--replace")
    db = remote.dbs["glyph-atlas-v3"]
    assert db.execute("SELECT code_point FROM characters").fetchall() == [("U+3044",)]
    assert db.execute("SELECT id FROM representations ORDER BY id").fetchall() == [("rp:a",), ("rp:b",)]
