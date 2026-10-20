import gzip
import importlib
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime

import pytest
from test_atlas3_publish import UNITS, FakeD1, layout, unit, write_source  # noqa: F401 -- layout is a fixture

SHARDS = 256
PACK = "corpus-0001.bin"


def module():
    sys.modules.pop("answers", None)
    return importlib.import_module("answers")


def answers(publish, monkeypatch, *flags, packs=None, reads=None, sample=None):
    """Run answers.py with the fake D1 and uploads; R2 holds `packs` ({object: bytes}), each read counted in `reads`."""
    answers_module = module()
    uploaded = []
    monkeypatch.setattr(answers_module, "query", publish.query)
    monkeypatch.setattr(answers_module, "upload", lambda files, prefix: uploaded.append((prefix, [f.name for f in files])))
    if sample is not None:
        monkeypatch.setattr(answers_module, "SAMPLE_RANGE", sample)

    def read(key, offset, size):
        if reads is not None:
            reads.append((key, offset, size))
        return (packs or {})[key][offset:offset + size]
    monkeypatch.setattr(answers_module, "r2_reader", lambda: read)
    monkeypatch.setattr(sys, "argv", ["answers.py", *flags])
    answers_module.main()
    return uploaded


def latest(root):
    return max(p for p in (root / "out" / "pub").iterdir() if not p.name.startswith("pointer-"))


def answer(root, name):
    return json.loads(gzip.decompress((latest(root) / name).read_bytes()))


def shard_of(root, char):
    return answer(root, f"counts/{char % SHARDS}.json.gz")


def test_a_publication_counts_each_character_and_moves_the_pointer_last(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    uploaded = answers(publish, monkeypatch, sample=0)
    atlas = sqlite3.connect(root / "out" / "atlas.sqlite")
    char = atlas.execute("SELECT id FROM graphemes WHERE text='い'").fetchone()[0]
    body = shard_of(root, char)
    assert body["cell"] == ["kind", "style", "state", "quiz", "decade", "visual_group", "n"]
    # い: one checked crop of book:a, witnessed in 1700.
    state = atlas.execute("SELECT code FROM codes WHERE field='state' AND key='checked'").fetchone()[0]
    assert [c[2] for c in body["chars"][str(char)]] == [state]
    assert body["chars"][str(char)][0][4] == 1700 and body["chars"][str(char)][0][-1] == 1
    assert sum(len(names) for prefix, names in uploaded if prefix.endswith("/counts/")) == SHARDS
    assert uploaded[-1] == ("v3/", ["pointer.json"])
    assert remote.query("glyph-atlas-v3", "SELECT gen FROM generation")[0]["gen"] == 2
    pointer = json.loads(gzip.decompress(next((root / "out" / "pub").glob("pointer-*/pointer.json")).read_bytes()))
    assert pointer["through_gen"] == 1


def test_a_dry_run_bumps_nothing_and_uploads_nothing(layout, monkeypatch):  # noqa: F811
    _root, publish, remote = layout
    assert answers(publish, monkeypatch, "--dry-run", sample=0) == []
    assert remote.query("glyph-atlas-v3", "SELECT gen FROM generation")[0]["gen"] == 1


def test_review_events_on_the_new_layout_stop_a_publication_before_the_pointer_moves(layout, monkeypatch):  # noqa: F811
    _root, publish, remote = layout
    remote.dbs["glyph-atlas-v3"].execute("INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind) VALUES(1,1,0,1,1,1,0,0)")
    module_uploads = []
    with pytest.raises(SystemExit):
        module_uploads = answers(publish, monkeypatch, sample=0)
    assert module_uploads == []


def test_a_bump_another_run_made_stops_this_one(layout, monkeypatch):  # noqa: F811
    _root, publish, _remote = layout
    real = publish.query

    def racing(database, sql):
        if sql.startswith("UPDATE generation"):
            real(database, "UPDATE generation SET gen = gen + 1")
        return real(database, sql)

    monkeypatch.setattr(publish, "query", racing)
    with pytest.raises(SystemExit):
        answers(publish, monkeypatch, sample=0)


# Corpus glyphs: two of か (one printed, one of no material), one of 한 and one no one has read, each
# record in one pack.
GLYPHS = [("codh:k1", "か", "printed", "codh:b"), ("codh:k2", "か", None, "codh:b"),
          ("kor:h1", "한", "printed", "kor:b"), ("codh:u1", None, "printed", "codh:b")]


@pytest.fixture
def corpus_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS3_DIR", str(tmp_path))
    write_source(tmp_path, "units", UNITS, [
        unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ"),
        unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, "ア", state="checked", revision=3, quiz=0),
        unit("ex:b1", "hl:p2", {"x": 0, "y": 0, "w": 10, "h": 10}, "う", document="book:b"),
    ])
    pack, rows = b"", []
    for i, (gid, char, production, document) in enumerate(GLYPHS):
        record = json.dumps({"id": gid, "label": char, "image": f"/atlas/media/{i}.webp", "text": "line", "proxyable": True}).encode()
        rows.append((gid, char, None, None, i, PACK, len(pack), len(record), production, 0, "printed", document, char))
        pack += record
    write_source(tmp_path, "corpus_units", ("id", "character", "family", "visual_group", "shuffle", "object", "offset",
                                            "size", "production", "named", "style", "document", "label"), rows)
    for name, cols in (("media", ("key", "object", "offset", "size", "content_type")),
                       ("unit_ngrams", ("first", "size", "second", "third", "text", "document", "vertical", "hand_order",
                                        "shuffle", "graphemes")), ("pages", ("id", "document", "data"))):
        write_source(tmp_path, name, cols, [])
    write_source(tmp_path, "document_dating", ("document", "axis", "kind", "start", "end", "precision", "label", "status"), [])
    # ア is filed under あ's family.
    write_source(tmp_path, "characters", ("code_point", "character", "name", "head"),
                 [("U+3042", "あ", "a", "U+3042"), ("U+30A2", "ア", "a", "U+3042")])
    write_source(tmp_path, "aliases", ("query", "code_point", "rank"), [])
    for name in ("build", "publish", "media_map"):
        sys.modules.pop(name, None)
    importlib.import_module("build").main()
    publish = importlib.import_module("publish")
    remote = FakeD1(tmp_path)
    monkeypatch.setattr(publish, "send", remote.send)
    monkeypatch.setattr(publish, "query", remote.query)
    monkeypatch.setattr(publish, "upload", remote.upload)
    return tmp_path, publish, {PACK: pack}


def ids(root):
    return dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))


def test_browse_corpus_and_book_counts_name_characters_by_grapheme(corpus_layout, monkeypatch):
    root, publish, packs = corpus_layout
    answers(publish, monkeypatch, packs=packs, sample=0)
    atlas = sqlite3.connect(root / "out" / "atlas.sqlite")
    code = lambda field, key: atlas.execute("SELECT code FROM codes WHERE field=? AND key=?", (field, key)).fetchone()[0]
    char = dict(atlas.execute("SELECT text, id FROM graphemes"))
    book = dict(atlas.execute("SELECT key, id FROM books"))
    production = dict(atlas.execute("SELECT key, id FROM productions"))
    facets = answer(root, "facets.json.gz")
    assert facets["cell"] == ["char", "book", "production", "category", "state", "quiz", "n"]
    kana = code("category", "kana")
    assert facets["cells"] == sorted([[char["あ"], book["book:a"], production["printed"], kana, code("state", "pending"), 1, 1],
                                      [char["ア"], book["book:a"], production["printed"], kana, code("state", "checked"), 0, 1],
                                      [char["う"], book["book:b"], production["printed"], kana, code("state", "pending"), 1, 1]])
    assert facets["chars"][str(char["ア"])] == ["ア", "U+3042"]
    assert facets["chars"][str(char["う"])] == ["う", "U+3046"]
    glyphs = answer(root, "corpus.json.gz")
    pending = code("state", "pending")
    # The glyph no one has read has no character and is left out; one of no material is unknown.
    assert glyphs["cells"] == sorted([[char["か"], production["printed"], pending, 1, 1],
                                      [char["か"], production["unknown"], pending, 1, 1],
                                      [char["한"], production["printed"], pending, 1, 1]])
    assert glyphs["chars"][str(char["한"])] == ["한", "U+D55C"]
    assert answer(root, "books.json.gz")["books"] == {str(book["book:a"]): [2, 0], str(book["book:b"]): [1, 0],
                                                    str(book["codh:b"]): [0, 3], str(book["kor:b"]): [0, 1]}


def test_the_homepage_sample_copies_each_record_once_and_shards_it_by_shuffle(corpus_layout, monkeypatch):
    root, publish, packs = corpus_layout
    reads = []
    uploaded = answers(publish, monkeypatch, packs=packs, reads=reads, sample=2**31)
    index = answer(root, "gallery/index.json.gz")["pools"]
    assert index["sample"] == {"range": 2**31, "shards": 128, "glyphs": 4, "categories": {"hangul": 1, "kana": 2, "other": 1}}
    assert index["hangul"] == {"range": 2**31, "shards": 1, "glyphs": 1, "categories": {"hangul": 1}}
    assert index["gugyeol"]["glyphs"] == 0
    # Every glyph is read once, though 한 is in two pools.
    assert len(reads) == len({r[1] for r in reads}) == 4
    shuffles = dict(sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT id, shuffle FROM crops WHERE kind = 1"))
    found = {}
    for k in range(128):
        entries = answer(root, f"gallery/sample/{k}.json.gz")["entries"]
        assert entries == sorted(entries, key=lambda e: (e[1], e[0]))
        for crop, shuffle, category, record in entries:
            assert shuffles[crop] == shuffle and k * 2**31 // 128 <= shuffle < (k + 1) * 2**31 // 128
            found[crop] = (category, record)
    glyph = ids(root)
    assert found[glyph["kor:h1"]][0] == "hangul" and found[glyph["codh:k1"]][0] == "kana" and found[glyph["codh:u1"]][0] == "other"
    # A record keeps what a listing shows: its line text is the inspector's.
    assert found[glyph["codh:k1"]][1] == {"id": "codh:k1", "label": "か", "image": "/atlas/media/0.webp", "proxyable": True}
    assert [e[0] for e in answer(root, "gallery/hangul/0.json.gz")["entries"]] == [glyph["kor:h1"]]
    gallery = [names for prefix, names in uploaded if "/gallery/" in prefix]
    assert sum(len(n) for n in gallery) == 128 + 3 + 1
    assert uploaded[-1] == ("v3/", ["pointer.json"])
    # A second publication reads nothing again: a record's place names it for good.
    reads.clear()
    time.sleep(1.1)  # a publication is named by its second
    answers(publish, monkeypatch, packs=packs, reads=reads, sample=2**31)
    assert reads == []


def test_a_record_read_from_another_place_stops_the_publication_before_the_bump(corpus_layout, monkeypatch):
    _root, publish, packs = corpus_layout
    shifted = {PACK: b" " + packs[PACK]}
    with pytest.raises(SystemExit):
        answers(publish, monkeypatch, packs=shifted, sample=2**31)
    remote = publish.query("glyph-atlas-v3", "SELECT gen FROM generation")
    assert remote[0]["gen"] == 1


def test_a_signature_matches_the_s3_example():
    # The GET-with-range example of the AWS signature version 4 documentation for S3.
    headers = module().signed("GET", "https://examplebucket.s3.amazonaws.com/test.txt", {"Range": "bytes=0-9"},
                              "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1",
                              datetime(2013, 5, 24, tzinfo=UTC))
    assert headers["authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")


def test_a_record_is_read_by_its_byte_range(http_server, monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS3_DIR", str(tmp_path))
    http_server.put("glyph-atlas/packs/p1.bin", b"0123456789")
    read = module().s3_reader(http_server.base_url, "key", "secret")
    assert read("packs/p1.bin", 3, 4) == b"3456"
