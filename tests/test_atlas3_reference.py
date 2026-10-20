import gzip
import hashlib
import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_publish import ids, layout, write_source  # noqa: F401 -- `layout` is the fixture

CARD = ("code_point", "character", "name", "data", "detail", "head")


def card(char, head=None, detail=None, members=None, occurrences=0, name=None):
    point = " ".join(f"U+{ord(c):04X}" for c in char)
    family = head or char
    data = {"code_point": point, "char": char, "occurrence_count": occurrences, "ligature": None,
            "grapheme": {"code_point": f"U+{ord(family):04X}", "char": family,
                         "members": [{"char": m} for m in (members or [char])]}}
    return (point, char, name or f"NAME {point}", json.dumps(data, ensure_ascii=False),
            json.dumps(detail or {"char": char}, ensure_ascii=False), f"U+{ord(family):04X}")


def group(*crops):
    return {"id": "U+3042:01", "representatives": [{"id": c, "image": f"/layers/visual-groups/samples/{c}/image",
                                                    "source_label": "あ"} for c in crops],
            "anchors": [{"id": crops[0], "position": 1}]}


def sources(root, cards=(), variants=(), derived=(), writers=(), substitutions=(("氵", "水", 3, "[]"),), words=None):
    for table in ("characters", "character_variants", "character_derived", "grapheme_writers", "component_variants", "words",
                  "word_spellings", "han_ids", "han_components", "han_component_names"):
        (root / f"source-{table}.sqlite").unlink(missing_ok=True)
    write_source(root, "characters", CARD, cards)
    write_source(root, "character_variants", ("a", "b", "relation", "source", "detail", "written", "widens"), variants)
    write_source(root, "character_derived", ("a", "forms"), derived)
    write_source(root, "grapheme_writers", ("writer", "grapheme", "own"), writers)
    write_source(root, "component_variants", ("a", "b", "count", "pairs"), substitutions)
    write_source(root, "words", ("id", "language", "reading", "class"),
                 [("ja/ばかり/副助詞", "ja", "ばかり", "副助詞")] if words is None else words)
    write_source(root, "word_spellings", ("word", "spelling", "source", "locator", "tier", "word_by", "basis", "related",
                                          "statement", "documents", "occurrences"),
                 [("ja/ばかり/副助詞", "許", "dictionary", "x", "attested", "source", "", "", "許（ばかり）", None, None)])
    write_source(root, "han_ids", ("char", "sequences", "shapes"), [("好", '["⿰女子"]', None)])
    write_source(root, "han_components", ("component", "tier", "size", "code_point", "n", "direct"), [("女", 0, 2, "U+597D", 1, 1)])
    write_source(root, "han_component_names", ("query", "component", "total"), [("女", "女", 1)])


class Bucket:
    def __init__(self):
        self.objects, self.puts = {}, 0

    def upload(self, files, prefix):
        for f in files:
            self.objects[prefix + f.name] = json.loads(gzip.decompress(f.read_bytes()))
            self.puts += 1

    def remove(self, paths):
        for p in paths:
            del self.objects["v3/reference/" + p]


def load(remote, monkeypatch, *flags, bucket=None):
    for name in ("reference", "load_history"):
        sys.modules.pop(name, None)
    module = importlib.import_module("reference")
    history = importlib.import_module("load_history")
    applied = lambda database, stamp: bool(remote.query(database, f"SELECT 1 AS n FROM imports WHERE stamp = '{stamp}'"))
    for m in (module, history):
        monkeypatch.setattr(m, "send", remote.send)
        monkeypatch.setattr(m, "applied", applied)
    bucket = bucket or Bucket()
    monkeypatch.setattr(module, "upload", bucket.upload)
    monkeypatch.setattr(module, "remove", bucket.remove)
    monkeypatch.setattr(sys, "argv", ["reference.py", *flags])
    module.main()
    remote.dbs["glyph-atlas-v3"].row_factory = None
    return bucket


def grapheme(db, text):
    found = db.execute("SELECT id FROM graphemes WHERE text=?", (text,)).fetchone()
    return found and found[0]


def shard(key):
    return hashlib.sha256(key.encode()).hexdigest()[:2]


def both(root, remote, sql):
    for db in (sqlite3.connect(root / "out" / "atlas.sqlite"), remote.dbs["glyph-atlas-v3"]):
        db.executescript(sql)
        db.commit()


def test_a_card_is_one_object_with_its_edges_and_the_layout_numbers_its_characters(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    mirror = ids(root)
    both(root, remote, "INSERT INTO codes VALUES('visual_group', 7, 'U+3042:01');")
    # A crop the mirror holds but `crops` no longer does leaves its group's list.
    sqlite3.connect(root / "out" / "atlas.sqlite").execute(f"DELETE FROM crops WHERE id={mirror['ex:b1']}").connection.commit()
    sources(root, cards=[card("あ", members=["あ", "𛀂"], detail={"char": "あ", "visual_analysis": {"groups": [group("ex:a1", "gone:1", "ex:b1")]}}),
                         card("𛀂", head="あ", members=["あ", "𛀂"])],
            variants=[("あ", "𛀂", "variant", "test", "", 1, 1), ("安", "U+3042", "source", "test", "", 0, 0)],
            derived=[("あ", '[["阿", [[["安", "阿"]]]]]')], writers=[("𛀂", "あ", 0)],
            substitutions=[("安", "阿", 2, '[{"a":"x","b":"y","sources":["s"]}]'), ("氵", "水", 3, "[]")])
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    before = local.execute("SELECT max(id) FROM graphemes").fetchone()[0]
    bucket = load(remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    a, hentaigana = grapheme(db, "あ"), grapheme(db, "𛀂")
    # Card characters the layout lacks are numbered after its own, with the card's head; 安 is no card.
    assert a <= before and hentaigana == before + 1 and grapheme(db, "安") is None
    assert db.execute("SELECT head FROM graphemes WHERE id IN (?, ?)", (a, hentaigana)).fetchall() == [(a,), (a,)]
    found = bucket.objects["v3/reference/char/U+3042.json.gz"]
    assert found["grapheme"] == a and found["data"]["char"] == "あ"
    # Edges from both ends, the end given as a code point read as its character.
    assert found["variants"] == [["𛀂", "variant", "test", "", 1, 1], ["安", "source", "test", "", 0, 0]]
    assert found["derived"] == [["阿", [[["安", "阿"]]]]]
    assert found["substitutions"] == [["安", "阿", 2, [{"a": "x", "b": "y", "sources": ["s"]}]]]
    assert found["writes"] == [] and found["writers"] == [["𛀂", 0]]
    assert found["ids"] == {"あ": a, "𛀂": hentaigana}
    assert bucket.objects["v3/reference/char/U+1B002.json.gz"]["writes"] == [["あ", 0]]
    # A visual group is keyed by its code; its samples name published crops by integer id.
    shown = found["detail"]["visual_analysis"]["groups"][0]
    assert shown["code"] == 7 and "id" not in shown
    assert shown["representatives"] == [{"crop": mirror["ex:a1"], "source_label": "あ"}]
    assert shown["anchors"] == [{"crop": mirror["ex:a1"], "position": 1}]
    # A family's members share the shard of its head.
    assert set(bucket.objects[f"v3/reference/summary/{shard('U+3042')}.json.gz"]) == {"U+3042", "U+1B002"}
    assert bucket.objects["v3/reference/list/graphemes/0.json.gz"]["total"] == 2
    assert bucket.objects[f"v3/reference/ids/{shard('好')}.json.gz"] == {"好": [["⿰女子"], None]}
    assert db.execute("SELECT * FROM component_variants ORDER BY a").fetchall() == [("安", "阿", 2), ("氵", "水", 3)]
    assert db.execute("SELECT code_point FROM han_components").fetchall() == [(0x597D,)]
    report = (root / "out" / "reference-report.txt").read_text()
    assert "visual group samples whose crop is not published, left out: 2" in report
    # The local copy replays the same parts, and a rerun sends nothing.
    assert local.execute("SELECT count(*) FROM words").fetchone() == (1,)
    stamps, puts = db.execute("SELECT count(*) FROM imports").fetchone(), bucket.puts
    load(remote, monkeypatch, "--send", bucket=bucket)
    assert db.execute("SELECT count(*) FROM imports").fetchone() == stamps and bucket.puts == puts


def test_heads_of_graphemes_a_publish_made_come_from_the_cards(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    both(root, remote, "INSERT INTO graphemes(id, text, head) VALUES(100, 'ゑ', NULL);")
    sources(root, cards=[card("ゑ", head="ヱ", members=["ヱ", "ゑ"]), card("ヱ", members=["ヱ", "ゑ"]),
                         card("い", head="𛀆")])
    load(remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    katakana, hentaigana = grapheme(db, "ヱ"), grapheme(db, "𛀆")
    assert db.execute("SELECT head FROM graphemes WHERE id=100").fetchone() == (katakana,)
    assert db.execute("SELECT head FROM graphemes WHERE id=?", (katakana,)).fetchone() == (katakana,)
    # A head no card describes is numbered too, and keeps no head of its own.
    assert db.execute("SELECT head FROM graphemes WHERE text='い'").fetchone() == (hentaigana,)
    assert db.execute("SELECT head FROM graphemes WHERE id=?", (hentaigana,)).fetchone() == (None,)


def test_a_dry_run_reports_and_changes_nothing(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root, cards=[card("𛀂", head="あ")])
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    graphemes = local.execute("SELECT count(*) FROM graphemes").fetchone()
    bucket = load(remote, monkeypatch)
    assert remote.dbs["glyph-atlas-v3"].execute("SELECT count(*) FROM words").fetchone() == (0,)
    assert local.execute("SELECT count(*) FROM graphemes").fetchone() == graphemes
    assert bucket.objects == {} and not (root / "out" / "reference").exists()
    assert "char " in (root / "out" / "reference-report.txt").read_text()


def test_only_what_changed_is_sent_and_an_earlier_copy_loads_again(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    db = remote.dbs["glyph-atlas-v3"]
    word = ("ja/ばかり/副助詞", "ja", "ばかり", "副助詞")
    sources(root, cards=[card("あ"), card("い")], words=[word])
    bucket = load(remote, monkeypatch, "--send")
    sources(root, cards=[card("あ", occurrences=3)], words=[])
    load(remote, monkeypatch, "--send", bucket=bucket)
    assert db.execute("SELECT count(*) FROM words").fetchone() == (0,)
    assert db.execute("SELECT * FROM name_order").fetchall() == [(0x3042, 3)]
    # The card gone from the copy leaves R2; the other changed and nothing else was sent.
    assert "v3/reference/char/U+3044.json.gz" not in bucket.objects
    assert bucket.objects["v3/reference/char/U+3042.json.gz"]["data"]["occurrence_count"] == 3
    second = sorted((root / "out" / "reference-load").glob("reference-2-*/*.sql"))
    sent = "".join(p.read_text() for p in second)
    assert "DELETE FROM \"words\"" in sent and "han_components" not in sent
    # Back to the first copy: the same rows as the first load, sent again under the next generation.
    sources(root, cards=[card("あ"), card("い")], words=[word])
    load(remote, monkeypatch, "--send", bucket=bucket)
    assert db.execute("SELECT id FROM words").fetchall() == [(word[0],)]
    assert db.execute("SELECT count(*) FROM name_order").fetchone() == (0,)
    assert "v3/reference/char/U+3044.json.gz" in bucket.objects
    # And to the second again: the same statements as the second load, which its stamps do not skip.
    sources(root, cards=[card("あ", occurrences=3)], words=[])
    load(remote, monkeypatch, "--send", bucket=bucket)
    assert db.execute("SELECT count(*) FROM words").fetchone() == (0,)
    assert db.execute("SELECT * FROM name_order").fetchall() == [(0x3042, 3)]


def test_a_load_that_stopped_partway_is_finished_first(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root, cards=[card("あ")])
    import publish
    monkeypatch.setattr(publish, "PART_BYTES", 200)
    real, calls = remote.send, []

    def flaky(database, file, stamp):
        calls.append(stamp)
        return False if len(calls) == 3 else real(database, file, stamp)

    monkeypatch.setattr(remote, "send", flaky)
    with pytest.raises(SystemExit, match="did not apply"):
        load(remote, monkeypatch, "--send")
    monkeypatch.setattr(remote, "send", real)
    load(remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    assert db.execute("SELECT count(*) FROM han_components").fetchone() == (1,)
    assert db.execute("SELECT count(*) FROM words").fetchone() == (1,)
    assert len(list((root / "out" / "reference-load").glob("reference-*-*"))) == 1


def test_migration_0004_is_applied_once_where_the_layout_lacks_it(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root, cards=[card("あ")])
    both(root, remote, ";".join(f"DROP TABLE {t}" for t in ("component_variants", "words", "word_spellings",
                                                            "han_components", "han_component_names", "name_order")))
    load(remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    assert db.execute("SELECT count(*) FROM imports WHERE stamp='migration:0004'").fetchone() == (1,)
    assert db.execute("SELECT count(*) FROM han_component_names").fetchone() == (1,)
