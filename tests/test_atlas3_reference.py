import gzip
import hashlib
import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_publish import ids, layout  # noqa: F401 -- `layout` is the fixture

from glyph_atlas import reference_input


def card(char, head=None, detail=None, members=None, occurrences=0, name=None):
    point = " ".join(f"U+{ord(c):04X}" for c in char)
    family = head or char
    data = {"code_point": point, "char": char, "occurrence_count": occurrences, "ligature": None,
            "grapheme": {"code_point": f"U+{ord(family):04X}", "char": family,
                         "members": [{"char": m} for m in (members or [char])]}}
    return (point, char, name or f"NAME {point}", json.dumps(data, ensure_ascii=False),
            json.dumps(detail or {"char": char}, ensure_ascii=False))


def group(*crops):
    return {"id": "U+3042:01", "representatives": [{"id": c, "image": f"/layers/visual-groups/samples/{c}/image",
                                                    "source_label": "あ"} for c in crops],
            "anchors": [{"id": crops[0], "position": 1}]}


# The readings table the load reads, written by `sources` beside the input.
READINGS = {}


def sources(root, cards=(), variants=(), derived=(), writers=(), substitutions=(("氵", "水", 3, "[]"),), words=None,
            readings=None, aliases=(),
            cited=(("variant_sources", '{"cjkvi-variants":"x"}'), ("word_sources", '{"honkoku-ruby":"y"}'))):
    """The reference input, every table written."""
    READINGS["path"] = root / "kanji-readings.json"
    READINGS["path"].write_text(json.dumps({"readings": readings or {}}, ensure_ascii=False))
    path = root / "reference-input.sqlite"
    path.unlink(missing_ok=True)
    with reference_input.writing(path) as db:
        for table, rows in (
                ("characters", cards), ("aliases", aliases), ("character_variants", variants), ("character_derived", derived),
                ("grapheme_writers", writers), ("component_variants", substitutions),
                ("words", [("ja/ばかり/副助詞", "ja", "ばかり", "副助詞")] if words is None else words),
                ("word_spellings", [("ja/ばかり/副助詞", "許", "dictionary", "x", "attested", "source", "", "", "許（ばかり）",
                                     None, None)]),
                ("han_ids", [("好", '["⿰女子"]', None)]),
                ("han_components", [("女", 0, 2, "U+597D", 1, 1), ("女", 1, 3, "U+5973", 1, 1), ("女", 0, 2, "U+5982", 2, 0)]),
                ("han_component_names", [("女", "女", 3), ("⼥", "女", 3)])):
            reference_input.replace(db, table, rows)
        for key, value in cited:
            reference_input.put_source(db, key, value)


class Bucket:
    def __init__(self):
        self.objects, self.puts = {}, 0

    def upload(self, files, prefix):
        for f in files:
            self.objects[prefix + f.name] = json.loads(gzip.decompress(f.read_bytes()))
            self.puts += 1

    def remove(self, paths):
        for p in paths:
            self.objects.pop("v3/reference/" + p, None)

    def manifest(self):
        return self.objects.get("v3/reference/manifest.json.gz", {})


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
    monkeypatch.setattr(module, "fetch_manifest", bucket.manifest)
    monkeypatch.setattr(module, "READINGS", READINGS["path"])
    monkeypatch.setattr(sys, "argv", ["reference.py", *flags])
    module.main()
    remote.dbs["glyph-atlas-v3"].row_factory = None
    return bucket


def grapheme(db, text):
    found = db.execute("SELECT id FROM graphemes WHERE text=?", (text,)).fetchone()
    return found and found[0]


def shard(key, digits=3):
    return hashlib.sha256(key.encode()).hexdigest()[:digits]


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
    summary = bucket.objects[f"v3/reference/summary/{shard('U+3042')}.json.gz"]
    assert list(summary) == ["U+3042"] and set(summary["U+3042"]) == {"U+3042", "U+1B002"}
    assert bucket.objects["v3/reference/list/graphemes/0.json.gz"]["total"] == 2
    assert bucket.objects[f"v3/reference/ids/{shard('好', 2)}.json.gz"] == {"好": [["⿰女子"], None]}
    assert db.execute("SELECT * FROM component_variants ORDER BY a").fetchall() == [("安", "阿", 2), ("氵", "水", 3)]
    # A component's characters by tier and size, code points as steps, flags n * 2 + direct.
    assert bucket.objects["v3/reference/components/U+5973.json.gz"] == {
        "total": 3, "groups": [[0, 2, [0x597D, 5], [3, 4]], [1, 3, [0x5973], [3]]]}
    assert bucket.objects["v3/reference/components/names.json.gz"] == {"same": "女", "other": {"⼥": "女"}}
    report = (root / "out" / "reference-report.txt").read_text()
    assert "visual group samples whose crop is not published, left out: 2" in report
    # The local copy replays the same parts, and a rerun sends nothing.
    assert local.execute("SELECT count(*) FROM words").fetchone() == (1,)
    stamps, puts = db.execute("SELECT count(*) FROM imports").fetchone(), bucket.puts
    load(remote, monkeypatch, "--send", bucket=bucket)
    assert db.execute("SELECT count(*) FROM imports").fetchone() == stamps and bucket.puts == puts


def test_a_reading_and_a_variant_bring_the_families_of_their_cards(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    many = [chr(0x4E00 + i) for i in range(60)]
    sources(root, cards=[card("時", members=["時", "旹"]), card("旹", head="時", members=["時", "旹"]), card("刻"), card("鴇"),
                         *(card(c) for c in many)],
            variants=[("時", "旹", "variant", "test", "", 1, 1), ("刻", "時", "variant", "test", "", 1, 0), ("時", "𠁣", "variant", "test", "", 1, 1)],
            readings={"とき": ["時", "朱", "刻"], "いち": many})
    bucket = load(remote, monkeypatch, "--send")
    toki = bucket.objects[f"v3/reference/readings/{shard('とき')}.json.gz"]["とき"]
    # Each reading's kanji in its order, with the families of those that have cards, each family whole.
    assert toki["kanji"] == ["時", "朱", "刻"]
    assert {head: set(family) for head, family in toki["families"].items()} == {"U+6642": {"U+6642", "U+65F9"}, "U+523B": {"U+523B"}}
    assert toki["families"]["U+6642"]["U+65F9"]["char"] == "旹"
    # Only the first READING_CARDS kanji bring their cards; the list stays whole.
    ichi = bucket.objects[f"v3/reference/readings/{shard('いち')}.json.gz"]["いち"]
    assert ichi["kanji"] == many and len(ichi["families"]) == 49
    # A card's object holds the families of the cards its edges name, from either end; 𠁣 has no card.
    found = bucket.objects["v3/reference/char/U+6642.json.gz"]["variant_families"]
    assert {head: set(family) for head, family in found.items()} == {"U+6642": {"U+6642", "U+65F9"}, "U+523B": {"U+523B"}}
    assert bucket.objects["v3/reference/char/U+9D07.json.gz"]["variant_families"] == {}


def test_search_names_load_under_one_code_point_and_change_as_the_aliases_do(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root, cards=[card("ね")], aliases=[("ね", "U+306D", 0), ("u+306d", "U+306D", 0), ("ne", "U+306D", 6),
                                                         ("か゚", "U+304B U+309A", 0)])
    load(remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    # A name of a card of several code points has no row.
    assert db.execute("SELECT * FROM names ORDER BY text").fetchall() == [("ne", 0x306D, 6), ("u+306d", 0x306D, 0), ("ね", 0x306D, 0)]
    sources(root, cards=[card("ね")], aliases=[("ね", "U+306D", 0), ("ne", "U+306D", 3)])
    load(remote, monkeypatch, "--send")
    assert db.execute("SELECT * FROM names ORDER BY text").fetchall() == [("ne", 0x306D, 3), ("ね", 0x306D, 0)]
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert local.execute("SELECT * FROM names ORDER BY text").fetchall() == [("ne", 0x306D, 3), ("ね", 0x306D, 0)]


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


def test_names_said_by_the_code_point_are_kept_as_steps(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root, cards=[card("一", name="CJK UNIFIED IDEOGRAPH-4E00"), card("丁", name="CJK UNIFIED IDEOGRAPH-4E01", occurrences=2),
                         card("兀", name="CJK COMPATIBILITY IDEOGRAPH-FA0C"), card("あ", name="HIRAGANA LETTER A"),
                         card("𠀀", name="CJK UNIFIED IDEOGRAPH-020000")])
    bucket = load(remote, monkeypatch, "--send")
    # 兀 is U+5140 and 𠀀's name pads its hex: both are names of their own.
    assert bucket.objects["v3/reference/names.json.gz"] == {
        "names": [["U+3042", "HIRAGANA LETTER A", 0], ["U+5140", "CJK COMPATIBILITY IDEOGRAPH-FA0C", 0],
                  ["U+20000", "CJK UNIFIED IDEOGRAPH-020000", 0]],
        "plain": [["CJK UNIFIED IDEOGRAPH-", [0x4E00, 1], [0, 2]]]}


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
    assert bucket.objects["v3/reference/names.json.gz"] == {"names": [["U+3042", "NAME U+3042", 3]], "plain": []}
    # The card gone from the copy leaves R2; the other changed and nothing else was sent.
    assert "v3/reference/char/U+3044.json.gz" not in bucket.objects
    assert bucket.objects["v3/reference/char/U+3042.json.gz"]["data"]["occurrence_count"] == 3
    second = sorted((root / "out" / "reference-load").glob("reference-2-*/*.sql"))
    sent = "".join(p.read_text() for p in second)
    assert "DELETE FROM \"words\"" in sent and "component_variants" not in sent
    # Back to the first copy: the same rows as the first load, sent again under the next generation.
    sources(root, cards=[card("あ"), card("い")], words=[word])
    load(remote, monkeypatch, "--send", bucket=bucket)
    assert db.execute("SELECT id FROM words").fetchall() == [(word[0],)]
    assert [n[2] for n in bucket.objects["v3/reference/names.json.gz"]["names"]] == [0, 0]
    assert "v3/reference/char/U+3044.json.gz" in bucket.objects
    # And to the second again: the same statements as the second load, which its stamps do not skip.
    sources(root, cards=[card("あ", occurrences=3)], words=[])
    load(remote, monkeypatch, "--send", bucket=bucket)
    assert db.execute("SELECT count(*) FROM words").fetchone() == (0,)
    assert bucket.objects["v3/reference/names.json.gz"] == {"names": [["U+3042", "NAME U+3042", 3]], "plain": []}


def test_objects_to_delete_come_from_the_manifest_in_r2(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root, cards=[card("あ"), card("い")])
    bucket = load(remote, monkeypatch, "--send")
    assert bucket.manifest()["char/U+3044.json.gz"] and not (root / "out" / "reference").exists()
    # A load that adds う stops once its cards are uploaded, before its deletions and manifest.
    sources(root, cards=[card("あ"), card("う")])
    real = bucket.upload

    def stops(files, prefix):
        real(files, prefix)
        if prefix.endswith("char/"):
            raise SystemExit("stopped after the cards")

    bucket.upload = stops
    with pytest.raises(SystemExit, match="stopped"):
        load(remote, monkeypatch, "--send", bucket=bucket)
    bucket.upload = real
    assert bucket.manifest()["char/U+3046.json.gz"] == "" and "v3/reference/char/U+3046.json.gz" in bucket.objects
    # The next load no longer has う or い: both leave R2, though no local folder ever listed う.
    sources(root, cards=[card("あ")])
    load(remote, monkeypatch, "--send", bucket=bucket)
    assert sorted(k for k in bucket.objects if "/char/" in k) == ["v3/reference/char/U+3042.json.gz"]
    assert sorted(bucket.manifest()) == sorted(k.removeprefix("v3/reference/") for k in bucket.objects
                                               if not k.endswith("manifest.json.gz"))


def test_a_row_changed_under_its_key_is_replaced(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root, cards=[card("あ")], words=[("ja/ばかり/副助詞", "ja", "ばかり", "副助詞")])
    bucket = load(remote, monkeypatch, "--send")
    sources(root, cards=[card("あ")], words=[("ja/ばかり/副助詞", "ja", "ばかり", "助詞")])
    load(remote, monkeypatch, "--send", bucket=bucket)
    for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        assert db.execute("SELECT class FROM words").fetchall() == [("助詞",)]
    sent = "".join(p.read_text() for p in sorted((root / "out" / "reference-load").glob("reference-2-*/*.sql")))
    assert sent.count("DELETE FROM") == 1 and sent.count("INSERT INTO \"words\"") == 1


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
    assert db.execute("SELECT count(*) FROM words").fetchone() == (1,)
    assert len(list((root / "out" / "reference-load").glob("reference-*-*"))) == 1


def test_a_part_d1_applied_but_the_local_copy_missed_is_replayed_before_anything_else(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root, cards=[card("あ")])
    real = remote.send

    def stops(database, file, stamp):
        real(database, file, stamp)
        raise SystemExit("stopped after D1 applied a part")

    monkeypatch.setattr(remote, "send", stops)
    with pytest.raises(SystemExit, match="stopped"):
        load(remote, monkeypatch, "--send")
    monkeypatch.setattr(remote, "send", real)
    # The rows changed before the rerun: its difference from the local copy alone would insert the
    # first load's words again, which D1 holds, and refuse them.
    sources(root, cards=[card("あ"), card("い")],
            words=[("ja/ばかり/副助詞", "ja", "ばかり", "副助詞"), ("ja/のみ/副助詞", "ja", "のみ", "副助詞")])
    load(remote, monkeypatch, "--send")
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    for db in (remote.dbs["glyph-atlas-v3"], local):
        assert db.execute("SELECT id FROM words ORDER BY id").fetchall() == [("ja/のみ/副助詞",), ("ja/ばかり/副助詞",)]
    assert local.execute("SELECT count(*) FROM imports WHERE stamp LIKE 'reference-1-%'").fetchone()[0] >= 1


def test_migration_0004_is_applied_once_where_the_layout_lacks_it(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root, cards=[card("あ")])
    both(root, remote, ";".join(f"DROP TABLE {t}" for t in ("component_variants", "words", "word_spellings")))
    load(remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    assert db.execute("SELECT count(*) FROM imports WHERE stamp='migration:0004'").fetchone() == (1,)
    assert db.execute("SELECT count(*) FROM words").fetchone() == (1,)


def test_the_citations_load_into_site_beside_the_values_books_writes(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    both(root, remote, """INSERT INTO site VALUES('collection', '{"books": 2}');""")
    sources(root, cards=[card("あ")])
    bucket = load(remote, monkeypatch, "--send")
    for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        assert dict(db.execute("SELECT key, value FROM site")) == {
            "collection": '{"books": 2}', "variant_sources": '{"cjkvi-variants":"x"}', "word_sources": '{"honkoku-ruby":"y"}'}
    # A citation the input no longer holds leaves site; `collection` stays, being books.py's.
    sources(root, cards=[card("あ")], cited=[("variant_sources", '{"cjkvi-variants":"z"}')])
    load(remote, monkeypatch, "--send", bucket=bucket)
    assert dict(remote.dbs["glyph-atlas-v3"].execute("SELECT key, value FROM site")) == {
        "collection": '{"books": 2}', "variant_sources": '{"cjkvi-variants":"z"}'}


def test_a_table_no_generator_wrote_stops_the_load(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    with reference_input.writing(root / "reference-input.sqlite") as db:
        reference_input.replace(db, "characters", [card("あ")])
    with pytest.raises(SystemExit, match="export_character_variants.py writes it"):
        load(remote, monkeypatch)
