import gzip
import importlib
import json
import sqlite3
import sys
import time
from collections import Counter
from datetime import UTC, datetime

import pytest
from test_atlas3_publish import (  # noqa: F401 -- layout is a fixture
    UNITS,
    FakeD1,
    catalogue,
    corpus_catalogue,
    layout,
    run,
    unit,
    write_source,
)

SHARDS = 256
PACK = "corpus-0001.bin"


def module():
    sys.modules.pop("answers", None)
    return importlib.import_module("answers")


def patched(publish, monkeypatch, packs=None, reads=None, sample=None, collection=True, failing=None, seeded=True, **constants):
    """answers.py with the fake D1 and uploads, and what it uploaded; R2 holds `packs` ({object: bytes}), each read
    counted in `reads`. An upload to a prefix holding `failing` stops the run, as a failed upload does. D1 holds the
    seed's stamp unless `seeded` is false. `constants` replace the module's own (`PER_CHARACTER=1`)."""
    answers_module = module()
    if seeded:
        publish.query("glyph-atlas-v3", "INSERT OR IGNORE INTO imports(stamp, at) VALUES('migration:0019', 0)")
    for name, value in constants.items():
        monkeypatch.setattr(answers_module, name, value)
    uploaded = []

    def upload(files, prefix):
        if failing and failing in prefix:
            sys.exit(f"upload to {prefix} failed; rerun to finish")
        uploaded.append((prefix, [f.name for f in files]))
    monkeypatch.setattr(answers_module, "query", publish.query)
    monkeypatch.setattr(answers_module, "send", publish.send)
    monkeypatch.setattr(answers_module, "upload", upload)
    if sample is not None:
        monkeypatch.setattr(answers_module, "SAMPLE_RANGE", sample)

    def read(key, offset, size):
        if reads is not None:
            reads.append((key, offset, size))
        return (packs or {})[key][offset:offset + size]
    monkeypatch.setattr(answers_module, "r2_reader", lambda: read)
    monkeypatch.setattr(answers_module, "status", lambda: {"crops": 3})
    monkeypatch.setattr(answers_module, "COLLECTION", publish.OUT if collection else publish.OUT / "no-collection")
    monkeypatch.setattr(answers_module, "CORPUS_INDEX", publish.OUT / "corpus-index.json")
    return answers_module, uploaded


def answers(publish, monkeypatch, *flags, **options):
    """Run answers.py as `patched` sets it up; what it uploaded."""
    answers_module, uploaded = patched(publish, monkeypatch, **options)
    monkeypatch.setattr(sys, "argv", ["answers.py", *flags])
    answers_module.main()
    return uploaded


def latest(root):
    return max(p for p in (root / "out" / "pub").iterdir() if not p.name.startswith("pointer-"))


def pointer(root):
    return json.loads(gzip.decompress(max((root / "out" / "pub").glob("pointer-*/pointer.json")).read_bytes()))


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


def test_a_publication_writes_the_site_values_the_status_pages_serve(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    (root / "out" / "corpus-index.json").write_text('{"sources": 2}')
    answers(publish, monkeypatch, sample=0)
    assert dict(remote.dbs["glyph-atlas-v3"].execute("SELECT key, value FROM site")) == {"collection": '{"crops":3}',
                                                                                     "corpus_index": '{"sources":2}'}
    # Without a corpus index the summary stays as it was.
    (root / "out" / "corpus-index.json").unlink()
    time.sleep(1.1)  # a publication is named by its second
    answers(publish, monkeypatch, sample=0)
    assert dict(remote.dbs["glyph-atlas-v3"].execute("SELECT key, value FROM site"))["corpus_index"] == '{"sources":2}'


def test_unchanged_answers_publish_nothing(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    answers(publish, monkeypatch, sample=0)
    time.sleep(1.1)  # a publication is named by its second
    assert answers(publish, monkeypatch, sample=0) == []
    assert remote.query("glyph-atlas-v3", "SELECT gen FROM generation")[0]["gen"] == 2
    # A crop that changes state changes the answers, which are published again.
    atlas = sqlite3.connect(root / "out" / "atlas.sqlite")
    atlas.execute("UPDATE crops SET state = (SELECT code FROM codes WHERE field='state' AND key='pending') "
                  "WHERE char = (SELECT id FROM graphemes WHERE text='い')")
    atlas.commit()
    time.sleep(1.1)
    assert answers(publish, monkeypatch, sample=0)[-1] == ("v3/", ["pointer.json"])


def test_without_the_collection_s_folders_its_status_stays_as_published(layout, monkeypatch):  # noqa: F811
    _root, publish, remote = layout
    remote.dbs["glyph-atlas-v3"].execute("INSERT INTO site VALUES('collection', '{\"crops\":7}')")
    answers(publish, monkeypatch, sample=0, collection=False)
    assert dict(remote.dbs["glyph-atlas-v3"].execute("SELECT key, value FROM site"))["collection"] == '{"crops":7}'


def test_with_no_site_value_here_the_pointer_still_moves(layout, monkeypatch):  # noqa: F811
    _root, publish, remote = layout
    real = publish.query

    def no_empty(database, sql):
        assert sql.strip(), "an empty statement"
        return real(database, sql)

    monkeypatch.setattr(publish, "query", no_empty)
    assert answers(publish, monkeypatch, sample=0, collection=False)[-1] == ("v3/", ["pointer.json"])
    assert dict(remote.dbs["glyph-atlas-v3"].execute("SELECT key, value FROM site")) == {}


def test_a_dry_run_bumps_nothing_and_uploads_nothing(layout, monkeypatch):  # noqa: F811
    _root, publish, remote = layout
    assert answers(publish, monkeypatch, "--dry-run", sample=0) == []
    assert remote.query("glyph-atlas-v3", "SELECT gen FROM generation")[0]["gen"] == 1


def test_a_bump_another_run_made_stops_this_one(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    d1, crop = remote.dbs["glyph-atlas-v3"], ids(root)["ex:a1"]
    reviewed(remote, crop, state=1)
    real = publish.send

    def racing(database, file, stamp):
        if stamp.startswith("pub:"):
            d1.execute("UPDATE generation SET gen = gen + 1")
            d1.commit()
        real(database, file, stamp)
        # As `send` answers: whether the file's stamp is there.
        return bool(d1.execute("SELECT 1 FROM imports WHERE stamp=?", (stamp,)).fetchone())

    monkeypatch.setattr(publish, "send", racing)
    uploaded = []
    with pytest.raises(SystemExit, match="another publication bumped generation 1"):
        uploaded = answers(publish, monkeypatch, sample=0)
    # Nothing of this run's file applied: the crop stays listed for the publication that bumped.
    assert uploaded == [] and listed(remote) == ({crop: 1}, [])
    assert d1.execute("SELECT gen FROM generation").fetchone()[0] == 2


# Corpus glyphs: two of か (one printed, one of no material), one of 한 and one no one has read, each
# record in one pack.
GLYPHS = [("codh:k1", "か", "printed", "codh:b"), ("codh:k2", "か", None, "codh:b"),
          ("kor:h1", "한", "printed", "kor:b"), ("codh:u1", None, "printed", "codh:b")]


@pytest.fixture
def corpus_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS3_DIR", str(tmp_path))
    write_source(tmp_path, "units", UNITS, [
        unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ", priority=0),
        unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, "ア", state="checked", revision=3, quiz=0),
        unit("ex:b1", "hl:p2", {"x": 0, "y": 0, "w": 10, "h": 10}, "う", document="book:b", priority=2),
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
    monkeypatch.setattr(publish, "answer", lambda: None)
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
    assert facets["cell"] == ["char", "book", "production", "category", "priority", "state", "quiz", "n"]
    kana = code("category", "kana")
    assert facets["cells"] == sorted([[char["あ"], book["book:a"], production["printed"], kana, 0, code("state", "pending"), 1, 1],
                                      [char["ア"], book["book:a"], production["printed"], kana, 1, code("state", "checked"), 0, 1],
                                      [char["う"], book["book:b"], production["printed"], kana, 2, code("state", "pending"), 1, 1]])
    assert facets["chars"][str(char["ア"])] == ["ア", "U+3042"]
    assert facets["chars"][str(char["う"])] == ["う", "U+3046"]
    glyphs = answer(root, "corpus.json.gz")
    pending = code("state", "pending")
    # The glyph no one has read has no character and is left out; one of no material is unknown.
    assert glyphs["cells"] == sorted([[char["か"], production["printed"], pending, 1, 1],
                                      [char["か"], production["unknown"], pending, 1, 1],
                                      [char["한"], production["printed"], pending, 1, 1]])
    assert glyphs["chars"][str(char["한"])] == ["한", "U+D55C"]
    # The crops with no character by family are stated even where there are none: a missing list is no empty one.
    assert glyphs["filed"] == []
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
    assert sum(len(n) for n in gallery) == 128 + 4 + 1
    assert uploaded[-1] == ("v3/", ["pointer.json"])
    # A second publication reads nothing again: a record's place names it for good.
    reads.clear()
    time.sleep(1.1)  # a publication is named by its second
    answers(publish, monkeypatch, packs=packs, reads=reads, sample=2**31)
    assert reads == []


def test_a_group_pool_keeps_a_few_glyphs_of_each_character_and_the_sample_all(corpus_layout, monkeypatch):
    root, publish, packs = corpus_layout
    # Kana stands in for a group copied by character: か has two glyphs, 한 one.
    answers(publish, monkeypatch, packs=packs, sample=2**31, WHOLE=("hangul", "kana"), PER_CHARACTER=1)
    index = answer(root, "gallery/index.json.gz")["pools"]
    assert index["kana"]["glyphs"] == 1 and index["hangul"]["glyphs"] == 1
    assert index["sample"]["glyphs"] == 4
    glyph = ids(root)
    assert [e[0] for e in answer(root, "gallery/kana/0.json.gz")["entries"]] in ([glyph["codh:k1"]], [glyph["codh:k2"]])
    time.sleep(1.1)  # a publication is named by its second
    answers(publish, monkeypatch, packs=packs, sample=2**31, WHOLE=("hangul", "kana"), PER_CHARACTER=2)
    assert answer(root, "gallery/index.json.gz")["pools"]["kana"]["glyphs"] == 2


def test_a_character_s_kept_glyphs_spread_over_its_shuffles():
    rows = [(i, i * 1000, 7, "한") for i in range(100)]

    class Atlas:
        def execute(self, sql):
            return iter(rows)
    pools_module = module()
    pools_module.PER_CHARACTER = 4
    kept = [g[1] for g in pools_module.pools(Atlas())["hangul"]]
    assert kept == [12000, 37000, 62000, 87000]


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


def test_crops_with_no_character_are_counted_under_the_family_they_are_filed_under(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, _remote = layout
    run(publish, monkeypatch, corpus_catalogue(tmp_path / "corpus.sqlite", [("codh:m1", None, "U+3082", "も")]))
    answers(publish, monkeypatch, sample=0)
    atlas = sqlite3.connect(root / "out" / "atlas.sqlite")
    family = atlas.execute("SELECT id FROM graphemes WHERE text='も'").fetchone()[0]
    body = shard_of(root, family)
    assert [cell[0] for cell in body["families"][str(family)]] == [1] and body["families"][str(family)][0][-1] == 1
    assert str(family) not in body["chars"]
    # The grapheme totals read them from one answer, with the family's own text and key.
    glyphs = answer(root, "corpus.json.gz")
    assert glyphs["filed"] == [[family, 1, 1]]
    assert glyphs["chars"][str(family)] == ["も", "U+3082"]


# --- The crops the site changed ---

def grapheme(db, text):
    return db.execute("SELECT id FROM graphemes WHERE text=?", (text,)).fetchone()[0]


def changed(remote, crop, event=True, **columns):
    """A change of the site's to one crop, as the Worker writes it in one batch: one count out of the
    crop's `tally` cell and one into its new cell in the current generation, D1's row one revision on,
    an event of that generation unless `event` is false (a forms follow records none), and the crop
    listed in `touched` at that generation; the generation."""
    d1 = remote.dbs["glyph-atlas-v3"]
    gen = d1.execute("SELECT gen FROM generation").fetchone()[0]
    tally = ("INSERT INTO tally(gen,kind,char,family,book,production,style,state,quiz,decade,n) "
             f"SELECT ?,{sys.modules['publish'].TALLY_CELL},? FROM crops c WHERE c.id=? ON CONFLICT DO UPDATE SET n=n+excluded.n")
    d1.execute(tally, (gen, -1, crop))
    d1.execute(f"UPDATE crops SET {','.join(f'{c}=?' for c in columns)}, revision=revision+1 WHERE id=?", (*columns.values(), crop))
    d1.execute(tally, (gen, 1, crop))
    if event:
        d1.execute("INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind) SELECT id,revision,cut,?,1,1,0,1 FROM crops WHERE id=?",
                   (gen, crop))
    d1.execute("INSERT INTO touched(crop,gen) VALUES(?,?) ON CONFLICT(crop) DO UPDATE SET gen=excluded.gen", (crop, gen))
    d1.commit()
    return gen


def reviewed(remote, crop, **columns):
    """A review of one crop on the site."""
    return changed(remote, crop, **columns)


def row(db, crop):
    cursor = db.execute("SELECT * FROM crops WHERE id=?", (crop,))
    found = cursor.fetchone()
    return found and dict(zip([c[0] for c in cursor.description], found, strict=True))


def listed(remote):
    d1 = remote.dbs["glyph-atlas-v3"]
    return dict(d1.execute("SELECT crop, gen FROM touched")), [r[0] for r in d1.execute("SELECT crop FROM absorbing")]


def counted(root, text):
    """The crops the latest answers count under a character, by state."""
    char = grapheme(sqlite3.connect(root / "out" / "atlas.sqlite"), text)
    return {cell[2]: cell[-1] for cell in shard_of(root, char)["chars"].get(str(char), [])}


def counts_hold(root, remote):
    """Whether the site's counts are right: for every cell, the answer of the publication the pointer
    names plus D1's `tally` rows above the pointer's generation is the number of D1's crops. A cell is a
    character (-1 for none), kind, style, state, withheld flag and witness decade, which the answers
    and `tally` both hold."""
    d1, named = remote.dbs["glyph-atlas-v3"], pointer(root)
    folder, shown = root / "out" / "pub" / named["pub"], Counter()

    def add(char, cells):
        for kind, style, state, quiz, decade, _group, n in cells:
            shown[(char, kind, -1 if style is None else style, state, quiz, -1 if decade is None else decade)] += n
    for shard in range(SHARDS):
        for char, cells in json.loads(gzip.decompress((folder / "counts" / f"{shard}.json.gz").read_bytes()))["chars"].items():
            add(int(char), cells)
    for cells in json.loads(gzip.decompress((folder / "unread.json.gz").read_bytes()))["categories"].values():
        add(-1, cells)
    for *cell, n in d1.execute("SELECT char, kind, style, state, quiz, decade, sum(n) FROM tally WHERE gen > ? GROUP BY 1, 2, 3, 4, 5, 6",
                               (named["through_gen"],)):
        shown[tuple(cell)] += n
    held = {tuple(cell): n for *cell, n in d1.execute(
        f"SELECT coalesce(char, -1), kind, coalesce(style, -1), state, quiz, coalesce({sys.modules['answers'].DECADE}, -1), count(*) "
        "FROM crops WHERE kind < 2 GROUP BY 1, 2, 3, 4, 5, 6")}
    assert {cell: n for cell, n in shown.items() if n} == held
    return True


def dumps(root, remote):
    return ["\n".join(db.iterdump()) for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite"),
                                                sqlite3.connect(root / "out" / "mirror.sqlite"))]


def test_a_reviewed_crop_is_counted_under_its_new_character(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    answers(publish, monkeypatch, sample=0)
    assert counted(root, "あ") == {0: 1} and counted(root, "う") == {0: 1}
    d1, crop = remote.dbs["glyph-atlas-v3"], ids(root)["ex:a1"]
    # A reviewer reads あ as う and checks it, in generation 2: the site counts it from `tally` at once.
    assert reviewed(remote, crop, char=grapheme(d1, "う"), state=1) == 2
    assert counts_hold(root, remote)
    time.sleep(1.1)  # a publication is named by its second
    assert answers(publish, monkeypatch, sample=0)[-1] == ("v3/", ["pointer.json"])
    assert counted(root, "あ") == {} and counted(root, "う") == {0: 1, 1: 1}
    assert counts_hold(root, remote)
    assert pointer(root)["through_gen"] == 2
    assert d1.execute("SELECT gen FROM generation").fetchone()[0] == 3
    assert listed(remote) == ({}, [])
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert row(local, crop) == row(d1, crop) and row(local, crop)["revision"] == 1
    # The store counts the review, and the next publication speaks against that revision.
    assert local.execute("SELECT revision, at FROM review_revisions WHERE crop=?", (crop,)).fetchone() == (1, 1)
    assert local.execute("SELECT count(*) FROM reviewer_styles").fetchone()[0] == 0


def test_a_crop_that_keeps_a_reviewer_s_style_is_marked_in_the_local_copy(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    d1, crop = remote.dbs["glyph-atlas-v3"], ids(root)["ex:a1"]
    gen = d1.execute("SELECT gen FROM generation").fetchone()[0]
    # A style of the crop's own, as the Worker writes it: a style event the store does not count.
    d1.execute("UPDATE crops SET style=0, revision=revision+1 WHERE id=?", (crop,))
    d1.execute("INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,style) VALUES(?,1,0,?,1,1,0,9,0)", (crop, gen))
    d1.execute("INSERT INTO touched VALUES(?,?)", (crop, gen))
    d1.commit()
    answers(publish, monkeypatch, sample=0)
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert row(local, crop) == row(d1, crop) and row(local, crop)["style"] == 0
    assert local.execute("SELECT crop FROM reviewer_styles").fetchall() == [(crop,)]
    assert local.execute("SELECT revision, at FROM review_revisions WHERE crop=?", (crop,)).fetchone() == (0, 1)


def test_a_crop_changed_after_the_bump_is_taken_as_its_generation_left_it(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    answers(publish, monkeypatch, sample=0)
    d1, crop, other, late = remote.dbs["glyph-atlas-v3"], *(ids(root)[k] for k in ("ex:a1", "ex:b1", "ex:a2"))
    reviewed(remote, crop, state=1)
    reviewed(remote, other, state=1)
    real = publish.send

    def racing(database, file, stamp):
        out = real(database, file, stamp)
        if stamp.startswith("pub:"):
            # Once the bump's file has applied: one listed crop is flagged, and another crop is reviewed.
            assert reviewed(remote, crop, state=2) == 3
            reviewed(remote, late, state=0)
        return out

    monkeypatch.setattr(publish, "send", racing)
    time.sleep(1.1)  # a publication is named by its second
    answers(publish, monkeypatch, sample=0)
    monkeypatch.setattr(publish, "send", real)
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    # Both crops listed through generation 2 are taken as it left them; the two changed in generation 3 are listed again.
    assert listed(remote) == ({crop: 3, late: 3}, [])
    assert row(local, other) == row(d1, other) and (row(local, crop)["state"], row(local, crop)["revision"]) == (1, 1)
    assert row(local, late)["state"] == 1
    assert counted(root, "あ") == {1: 1}
    assert pointer(root)["through_gen"] == 2
    # The answers hold generation 2 and `tally` the two changes of generation 3.
    assert counts_hold(root, remote)
    time.sleep(1.1)
    answers(publish, monkeypatch, sample=0)
    assert listed(remote) == ({}, [])
    assert row(local, crop) == row(d1, crop) and row(local, crop)["state"] == 2 and row(local, late) == row(d1, late)
    assert counted(root, "あ") == {2: 1}
    assert pointer(root)["through_gen"] == 3
    assert counts_hold(root, remote)


def test_the_bump_s_file_sent_again_changes_nothing_more(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    d1, crop = remote.dbs["glyph-atlas-v3"], ids(root)["ex:a1"]
    reviewed(remote, crop, state=1)
    answers_module, _uploaded = patched(publish, monkeypatch)
    assert answers_module.bump("20260101T000000Z", 1)
    assert d1.execute("SELECT gen FROM generation").fetchone()[0] == 2 and listed(remote) == ({}, [crop])
    assert d1.execute("SELECT count(*) FROM imports WHERE stamp='pub:20260101T000000Z'").fetchone()[0] == 1
    # The site changes the crop again, in the next generation.
    reviewed(remote, crop, state=2)
    before = dumps(root, remote)[0]
    d1.executescript((root / "out" / "pub" / "pointer-20260101T000000Z" / "bump.sql").read_text())
    assert dumps(root, remote)[0] == before
    # The file of another publication that read the same generation stamps nothing and changes nothing.
    answers_module.bump("20260101T000001Z", 1)
    assert dumps(root, remote)[0] == before
    assert json.loads(d1.execute("SELECT row FROM absorbing WHERE crop=?", (crop,)).fetchone()[0])["state"] == 1


def test_a_crop_d1_no_longer_holds_leaves_the_local_copy(layout, monkeypatch, capsys):  # noqa: F811
    root, publish, remote = layout
    d1, crop = remote.dbs["glyph-atlas-v3"], ids(root)["ex:b1"]
    d1.execute("DELETE FROM crops WHERE id=?", (crop,))
    # A listed crop neither database holds is passed over.
    d1.executemany("INSERT INTO touched VALUES(?,1)", [(crop,), (crop + 5,)])
    d1.commit()
    answers(publish, monkeypatch, sample=0)
    assert row(sqlite3.connect(root / "out" / "atlas.sqlite"), crop) is None
    assert counted(root, "う") == {}
    assert listed(remote) == ({}, [])
    assert "absorbed 2 crops from glyph-atlas-v3: 0 rows taken, 1 removed, 1 in neither database" in capsys.readouterr().out


def named(remote, root):
    """A crop the site read as a character and filed under a category it had no row for, as the Worker
    numbers both: the next grapheme id and the next category code."""
    d1, crop = remote.dbs["glyph-atlas-v3"], ids(root)["ex:a1"]
    char = d1.execute("INSERT INTO graphemes(text) VALUES('ゑ')").lastrowid
    category = d1.execute("SELECT coalesce(max(code), 0) + 1 FROM codes WHERE field='category'").fetchone()[0]
    d1.execute("INSERT INTO codes(field,code,key) VALUES('category',?,'hentaigana')", (category,))
    reviewed(remote, crop, char=char, category=category, state=1)
    return crop, char, category


def test_a_grapheme_and_a_code_the_site_added_are_copied_with_the_crop(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    crop, char, category = named(remote, root)
    answers(publish, monkeypatch, sample=0)
    local, mirror = sqlite3.connect(root / "out" / "atlas.sqlite"), sqlite3.connect(root / "out" / "mirror.sqlite")
    assert local.execute("SELECT text, head FROM graphemes WHERE id=?", (char,)).fetchone() == ("ゑ", None)
    assert local.execute("SELECT key FROM codes WHERE field='category' AND code=?", (category,)).fetchone() == ("hentaigana",)
    # The allocator holds the code, so a publish numbers the next category after it.
    assert mirror.execute("SELECT code FROM codes WHERE field='category' AND key='hentaigana'").fetchone() == (category,)
    assert row(local, crop) == row(remote.dbs["glyph-atlas-v3"], crop)
    assert counted(root, "ゑ") == {1: 1}
    assert answer(root, "facets.json.gz")["chars"][str(char)] == ["ゑ", "U+3091"]


@pytest.mark.parametrize("clash", ["grapheme", "code", "allocator"])
def test_an_id_that_stands_for_something_else_here_stops_the_run_before_the_local_copy_changes(layout, monkeypatch, clash):  # noqa: F811
    root, publish, remote = layout
    crop, char, category = named(remote, root)
    local, mirror = sqlite3.connect(root / "out" / "atlas.sqlite"), sqlite3.connect(root / "out" / "mirror.sqlite")
    # A publish numbered its own next grapheme or category meanwhile.
    if clash == "grapheme":
        local.execute("INSERT INTO graphemes(id,text) VALUES(?,'ゐ')", (char,))
    elif clash == "code":
        local.execute("INSERT INTO codes(field,code,key) VALUES('category',?,'seal')", (category,))
    else:
        mirror.execute("INSERT INTO codes(field,key,code) VALUES('category','seal',?)", (category,))
    local.commit()
    mirror.commit()
    before = dumps(root, remote)[1:]
    uploaded = []
    with pytest.raises(SystemExit, match="number 1 graphemes or codes differently"):
        uploaded = answers(publish, monkeypatch, sample=0)
    assert uploaded == [] and dumps(root, remote)[1:] == before
    # The copied crop waits in D1 for a run that can take it.
    assert listed(remote) == ({}, [crop])


def test_what_a_stopped_run_left_in_absorbing_is_taken_by_the_next(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    answers(publish, monkeypatch, sample=0)
    d1, crop = remote.dbs["glyph-atlas-v3"], ids(root)["ex:a1"]
    reviewed(remote, crop, state=1)
    # A run that bumped the generation and copied the listed crops, and stopped there.
    assert patched(publish, monkeypatch)[0].bump("20260101T000000Z", 2)
    assert listed(remote) == ({}, [crop]) and d1.execute("SELECT gen FROM generation").fetchone()[0] == 3
    assert row(sqlite3.connect(root / "out" / "atlas.sqlite"), crop)["state"] == 0
    # The pointer names the publication before, whose answers and the `tally` rows since still count right.
    assert counts_hold(root, remote)
    time.sleep(1.1)  # a publication is named by its second
    assert answers(publish, monkeypatch, sample=0)[-1] == ("v3/", ["pointer.json"])
    assert listed(remote) == ({}, [])
    assert row(sqlite3.connect(root / "out" / "atlas.sqlite"), crop) == row(d1, crop)
    assert counted(root, "あ") == {1: 1}
    # It publishes through the generation it found.
    assert pointer(root)["through_gen"] == 3
    assert counts_hold(root, remote)


def test_a_run_that_stops_between_the_bump_and_the_pointer_is_finished_by_the_next(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    answers(publish, monkeypatch, sample=0)
    first = json.loads((root / "out" / "answers-published.json").read_text())
    d1, crop = remote.dbs["glyph-atlas-v3"], ids(root)["ex:a1"]
    reviewed(remote, crop, state=1)
    time.sleep(1.1)  # a publication is named by its second
    with pytest.raises(SystemExit, match="rerun to finish"):
        answers(publish, monkeypatch, sample=0, failing="/counts/")
    # The generation moved on and the local copy took the crop; the pointer names the publication before.
    assert d1.execute("SELECT gen FROM generation").fetchone()[0] == 3
    assert listed(remote) == ({}, [])
    assert row(sqlite3.connect(root / "out" / "atlas.sqlite"), crop) == row(d1, crop)
    assert json.loads((root / "out" / "answers-published.json").read_text()) == first
    assert counts_hold(root, remote)
    time.sleep(1.1)
    assert answers(publish, monkeypatch, sample=0)[-1] == ("v3/", ["pointer.json"])
    assert counted(root, "あ") == {1: 1}
    assert pointer(root)["through_gen"] == 3
    assert d1.execute("SELECT gen FROM generation").fetchone()[0] == 4
    assert counts_hold(root, remote)


def test_a_run_that_stops_once_the_local_copy_has_the_crops_is_finished_by_the_next(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    answers(publish, monkeypatch, sample=0)
    d1, crop = remote.dbs["glyph-atlas-v3"], ids(root)["ex:a1"]
    reviewed(remote, crop, state=1)
    real = publish.query

    def stopping(database, sql):
        # D1 cannot be reached once both local databases have committed, before `absorbing` is emptied.
        if sql.startswith("DELETE FROM absorbing"):
            sys.exit("could not read glyph-atlas-v3; rerun to finish")
        return real(database, sql)

    monkeypatch.setattr(publish, "query", stopping)
    time.sleep(1.1)  # a publication is named by its second
    with pytest.raises(SystemExit, match="rerun to finish"):
        answers(publish, monkeypatch, sample=0)
    monkeypatch.setattr(publish, "query", real)
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert listed(remote) == ({}, [crop]) and row(local, crop) == row(d1, crop)
    assert pointer(root)["through_gen"] == 1 and counts_hold(root, remote)
    # The site flags the crop before the next run, which takes the copied row again and then the newer one.
    reviewed(remote, crop, state=2)
    assert counts_hold(root, remote)
    time.sleep(1.1)
    assert answers(publish, monkeypatch, sample=0)[-1] == ("v3/", ["pointer.json"])
    assert listed(remote) == ({}, [])
    assert row(local, crop) == row(d1, crop) and row(local, crop)["state"] == 2
    assert counted(root, "あ") == {2: 1}
    assert pointer(root)["through_gen"] == 3 and counts_hold(root, remote)


def test_a_dry_run_changes_neither_database(layout, monkeypatch, capsys):  # noqa: F811
    root, publish, remote = layout
    d1, crop, copied = remote.dbs["glyph-atlas-v3"], ids(root)["ex:a1"], ids(root)["ex:b1"]
    reviewed(remote, crop, state=1)
    d1.execute("INSERT INTO absorbing(crop, through, row) VALUES(?, 0, NULL)", (copied,))
    d1.execute("INSERT INTO imports(stamp, at) VALUES('migration:0019', 0)")
    d1.commit()
    before = dumps(root, remote)
    assert answers(publish, monkeypatch, "--dry-run", sample=0) == []
    assert dumps(root, remote) == before
    assert "1 crops are listed for the next publication and 1 wait in `absorbing`" in capsys.readouterr().out
    # The answers are those of the local copy as it is.
    assert counted(root, "あ") == {0: 1}


def test_a_publish_ends_by_taking_the_crops_the_site_changed(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    d1, crop = remote.dbs["glyph-atlas-v3"], ids(root)["ex:a2"]
    reviewed(remote, crop, state=2)
    answers_module, uploaded = patched(publish, monkeypatch, sample=0)
    monkeypatch.setattr(publish, "answer", lambda: answers_module.publication(False))
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a3", "hl:p1", {"x": 0, "y": 40, "w": 10, "h": 10}, "え")]))
    assert uploaded[-1] == ("v3/", ["pointer.json"])
    assert listed(remote) == ({}, [])
    assert row(sqlite3.connect(root / "out" / "atlas.sqlite"), crop) == row(d1, crop)
    assert counted(root, "い") == {2: 1} and counted(root, "え") == {0: 1}


def test_a_followed_glyph_s_base_is_taken_and_a_republish_stages_nothing_for_it(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    run(publish, monkeypatch, corpus_catalogue(tmp_path / "c1.sqlite", [("codh:x1", "き", None, None)]))
    answers(publish, monkeypatch, sample=0)
    d1, glyph = remote.dbs["glyph-atlas-v3"], ids(root)["codh:x1"]
    ki, ka = grapheme(d1, "き"), grapheme(d1, "か")
    # A forms decision, which a crop facts load has brought to the local copy, moves the glyph to か in the
    # Worker: its base is き, it moves on a revision with no event, and it is listed.
    for db in (d1, sqlite3.connect(root / "out" / "atlas.sqlite")):
        db.execute("INSERT INTO form_glyphs(crop,family,form,decision) VALUES(?, 'U+304B', 'か', 'd')", (glyph,))
        db.commit()
    d1.execute("INSERT INTO form_bases(crop, char) VALUES(?, ?)", (glyph, ki))
    changed(remote, glyph, event=False, char=ka, bumps=1)
    time.sleep(1.1)  # a publication is named by its second
    answers(publish, monkeypatch, sample=0)
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert row(local, glyph) == row(d1, glyph) and row(local, glyph)["char"] == ka
    assert local.execute("SELECT char FROM form_bases WHERE crop=?", (glyph,)).fetchall() == [(ki,)]
    # A republish of the corpus carries the glyph as き still: nothing is sent, and its page stays as published.
    before, sent = dumps(root, remote)[:2], set((root / "out" / "publish").rglob("*.sql"))
    run(publish, monkeypatch, corpus_catalogue(tmp_path / "c2.sqlite", [("codh:x1", "き", None, None)]))
    assert dumps(root, remote)[:2] == before
    assert not [part for part in set((root / "out" / "publish").rglob("*.sql")) - sent if part.name.startswith("atlas-")]
    # The decision is withdrawn: the glyph is き again with no base, here as in D1.
    d1.execute("DELETE FROM form_bases WHERE crop=?", (glyph,))
    changed(remote, glyph, event=False, char=ki, bumps=2)
    time.sleep(1.1)
    answers(publish, monkeypatch, sample=0)
    assert row(local, glyph) == row(d1, glyph) and row(local, glyph)["char"] == ki
    assert local.execute("SELECT count(*) FROM form_bases WHERE crop=?", (glyph,)).fetchone()[0] == 0


# --- The seed ---

def stamps(remote):
    return {r[0] for r in remote.dbs["glyph-atlas-v3"].execute("SELECT stamp FROM imports WHERE stamp LIKE 'migration:%'")}


def test_a_publication_is_refused_until_the_seed_has_run(layout, monkeypatch, capsys):  # noqa: F811
    root, publish, remote = layout
    uploaded = []
    with pytest.raises(SystemExit, match="answers.py --seed"):
        uploaded = answers(publish, monkeypatch, sample=0, seeded=False)
    assert uploaded == [] and not (root / "out" / "pub").exists()
    assert remote.query("glyph-atlas-v3", "SELECT gen FROM generation")[0]["gen"] == 1
    # A dry run says so and writes its answers.
    assert answers(publish, monkeypatch, "--dry-run", sample=0, seeded=False) == []
    assert "a publication is refused until then" in capsys.readouterr().out
    assert counted(root, "あ") == {0: 1}
    assert remote.query("glyph-atlas-v3", "SELECT gen FROM generation")[0]["gen"] == 1


def changed_before_the_list(remote, root):
    """Crops the site changed before the Worker listed any: one reviewed in generation 1, one whose only
    event is the history load's (generation 0) and whose base both copies hold alike, one that followed
    a forms decision the local copy has not seen, and one the Worker has listed since."""
    d1, crop = remote.dbs["glyph-atlas-v3"], ids(root)
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    for db in (d1, local):
        db.execute("INSERT INTO form_bases(crop, char) VALUES(?, NULL)", (crop["ex:a2"],))
    local.commit()
    d1.executemany("INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind) VALUES(?,?,0,?,1,1,0,0)",
                   [(crop["ex:a1"], 1, 1), (crop["ex:a1"], 2, 1), (crop["ex:a2"], 1, 0), (crop["ex:b1"], 1, 1)])
    d1.execute("INSERT INTO form_bases(crop, char) VALUES(?, NULL)", (crop["codh:g1"],))
    d1.execute("INSERT INTO touched VALUES(?,1)", (crop["ex:b1"],))
    d1.commit()
    return crop


def test_the_seed_lists_the_crops_changed_before_the_list_once(layout, monkeypatch, capsys):  # noqa: F811
    root, publish, remote = layout
    crop = changed_before_the_list(remote, root)
    before = dumps(root, remote)
    assert answers(publish, monkeypatch, "--seed", "--dry-run", seeded=False) == []
    assert ("would list 2 crops named by events of a generation above 0 and 1 followed glyphs the local copy holds "
            "differently (2 hold a `form_bases` row in glyph-atlas-v3)") in capsys.readouterr().out
    assert dumps(root, remote) == before
    answers(publish, monkeypatch, "--seed", seeded=False)
    # The crop the Worker listed keeps its generation.
    assert listed(remote) == ({crop["ex:a1"]: 0, crop["codh:g1"]: 0, crop["ex:b1"]: 1}, [])
    assert stamps(remote) == {"migration:0019"}
    assert "`touched` lists 3 crops" in capsys.readouterr().out
    # Run again, it finds its stamp: a crop changed since is the Worker's to list.
    remote.dbs["glyph-atlas-v3"].execute("INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind) VALUES(?,2,0,1,1,1,0,0)",
                                         (crop["ex:a2"],))
    remote.dbs["glyph-atlas-v3"].commit()
    before = dumps(root, remote)
    answers(publish, monkeypatch, "--seed", seeded=False)
    assert dumps(root, remote) == before
    assert "the seed is in place" in capsys.readouterr().out
    # The publication then takes the listed crops.
    assert answers(publish, monkeypatch, sample=0, seeded=False)[-1] == ("v3/", ["pointer.json"])
    assert listed(remote) == ({}, [])


def test_the_seed_lists_a_followed_glyph_only_where_the_copies_differ(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    d1, crop, local = remote.dbs["glyph-atlas-v3"], ids(root), sqlite3.connect(root / "out" / "atlas.sqlite")
    char = local.execute("SELECT id FROM graphemes LIMIT 1").fetchone()[0]
    # Three glyphs both copies hold with a base, and one whose base D1 has dropped.
    for db in (d1, local):
        db.executemany("INSERT INTO form_bases(crop, char) VALUES(?, NULL)", [(crop[name],) for name in ("ex:a1", "ex:a2", "ex:b1")])
    local.execute("INSERT INTO form_bases(crop, char) VALUES(?, NULL)", (crop["codh:g1"],))
    # Since the local copy took them, D1 moved one on a revision and holds another base for the next.
    d1.execute("UPDATE crops SET revision = revision + 1 WHERE id = ?", (crop["ex:a2"],))
    d1.execute("UPDATE form_bases SET char = ? WHERE crop = ?", (char, crop["ex:b1"]))
    d1.commit()
    local.commit()
    answers(publish, monkeypatch, "--seed", seeded=False)
    assert listed(remote) == ({crop["ex:a2"]: 0, crop["ex:b1"]: 0, crop["codh:g1"]: 0}, [])


def test_the_seed_is_refused_before_the_list_exists(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    remote.dbs["glyph-atlas-v3"].execute("DROP TABLE touched")
    remote.dbs["glyph-atlas-v3"].commit()
    before = dumps(root, remote)
    for flags in (("--seed",), ("--seed", "--dry-run")):
        with pytest.raises(SystemExit, match="publish.py --migrate"):
            answers(publish, monkeypatch, *flags, seeded=False)
    assert dumps(root, remote) == before
