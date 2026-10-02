"""Publication packs must exclude private images and preserve online corrections."""
import hashlib
import importlib
import json
import sqlite3
from pathlib import Path

import pytest

# Every migration, as a deployment applies them.
SCHEMA = "\n".join(p.read_text() for p in sorted(Path("apps/cloudflare/migrations").glob("*.sql")))


def database(path):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db


@pytest.fixture
def publication(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    module = importlib.import_module("seal_cloudflare")
    monkeypatch.setattr(module, "reviewed_baselines", lambda db, corpus: {})
    local, corpus, output = (tmp_path / n for n in ("local", "corpus", "sealed"))
    local.mkdir()
    corpus.mkdir()
    key, denied = "a" * 64, "b" * 64
    data = json.dumps({"id": "one", "label": "ア", "image": f"/atlas/media/{key}.webp"})
    with database(local / "catalogue.sqlite") as db:
        db.execute("INSERT INTO units(id,origin,character,family,visual_group,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual,document) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "one", "local", "ア", None, None, "handwritten", "kana", "pending", 0, 1, 1, 0,
            data, "{}", "{}", "{}", None))
        db.execute("INSERT INTO units(id,origin,character,family,visual_group,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual,document) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "two", "local", "ウ", None, None, "handwritten", "kana", "pending", 0, 1, 1, 0,
            json.dumps({"id": "two", "label": "ウ"}), "{}", "{}", "{}", None))
        db.execute("INSERT INTO unit_ngrams(first,size,second,text,vertical) VALUES('one',2,'two','アウ',0)")
        db.executemany("INSERT INTO media VALUES(?,?,?,?,?)", [
            (key, "pack-0001.bin", 0, 7, "image/webp"),
            (denied, "pack-0001.bin", 7, 7, "image/webp")])
    (local / "pack-0001.bin").write_bytes(b"allowedPRIVATE")
    record = json.dumps({"id": "corpus-one", "label": "イ"}, ensure_ascii=False).encode()
    with database(corpus / "corpus.sqlite") as db:
        db.execute("INSERT INTO corpus_units(id,character,family,visual_group,shuffle,object,offset,size,production,named) VALUES(?,?,?,?,?,?,?,?,?,?)", (
            "corpus-one", "イ", None, None, 1, "records.bin", 0, len(record), "handwritten", 0))
    (corpus / "records.bin").write_bytes(record)
    return module, local, corpus, output


def test_sealed_objects_drop_unreferenced_private_bytes(publication):
    module, local, corpus, output = publication
    module.seal(local, corpus, output)
    manifest = json.loads((output / "publication.json").read_text())
    assert manifest["counts"]["media"] == 1
    for item in manifest["objects"]:
        payload = (output / item["file"]).read_bytes()
        assert b"PRIVATE" not in payload
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]
        assert item["key"] == "packs/" + item["sha256"] + ".bin"
    with sqlite3.connect(output / "atlas.sqlite") as db:
        for table in ("media", "corpus_units"):
            name, offset, size = db.execute(f"SELECT object,offset,size FROM {table}").fetchone()
            contents = (output / "objects" / name.removeprefix("packs/")).read_bytes()[offset:offset+size]
            assert len(contents) == size
            assert contents == b"allowed" if table == "media" else json.loads(contents)["id"] == "corpus-one"


def test_publication_sql_does_not_overwrite_online_review(publication):
    module, local, corpus, output = publication
    module.seal(local, corpus, output)
    with database(":memory:") as db:
        sql = (output / "catalogue.sql").read_text()
        db.executescript(sql)
        assert db.execute("SELECT first,size,second,third,text,document,vertical FROM unit_ngrams").fetchall() == [("one", 2, "two", None, "アウ", None, 0)]
        db.execute("UPDATE units SET character='カ',state='checked',revision=3 WHERE id='one'")
        # Named online since the last publication, by a round whose row this test leaves out.
        db.execute("UPDATE corpus_units SET named=1 WHERE id='corpus-one'")
        db.execute("INSERT INTO submissions VALUES('review','person','{}','{}','now',0)")
        db.commit()
        db.executescript(sql)
        assert db.execute("SELECT character,state,revision FROM units WHERE id='one'").fetchone() == ("カ", "checked", 3)
        assert db.execute("SELECT text FROM unit_ngrams").fetchall() == [("カウ",)], "a pair keeps the label reviewed online"
        assert db.execute("SELECT count(*) FROM submissions").fetchone()[0] == 1
        assert db.execute("SELECT named FROM corpus_units").fetchone() == (1,), "a publication never resets named"
        assert db.execute("SELECT * FROM corpus_characters").fetchall() == [("イ", "handwritten", 1, 1)]


def test_truncated_publication_is_rejected(publication):
    module, local, corpus, output = publication
    (local / "pack-0001.bin").write_bytes(b"short")
    with pytest.raises(ValueError, match="Incomplete media pack"):
        module.seal(local, corpus, output)


def test_the_ledger_rows_an_export_carries_are_sealed_insert_only_and_their_slots_resolved(publication):
    module, local, corpus, output = publication
    version = "one@" + "c" * 64 + "@1,2,3,4"
    with sqlite3.connect(local / "catalogue.sqlite") as db:
        db.execute("UPDATE units SET data=json_set(data,'$.image_sha256',?,'$.box',json('{\"x\":1,\"y\":2,\"w\":3,\"h\":4}')) WHERE id='one'",
                   ("c" * 64,))
        db.execute("INSERT INTO assertions(id,subject,predicate,scope,slot,value,tier,asserted_by,asserted_at) "
                   "VALUES('lc:1','one','has_form','','','\"unreadable\"','observed','ann','2026-10-01T00:00:00.000Z')")
        db.execute("INSERT INTO assertion_evidence(assertion,kind,ref) VALUES('lc:1','crop',?)", (version,))
    module.seal(local, corpus, output)
    sql = (output / "catalogue.sql").read_text()
    assert 'INSERT OR IGNORE INTO "assertions"' in sql and 'INSERT OR IGNORE INTO "assertion_evidence"' in sql
    assert json.loads((output / "publication.json").read_text())["counts"]["assertions"] == 1
    with database(":memory:") as db:
        db.executescript(sql)
        # A reviewer on the site disagrees before the next publication.
        db.execute("INSERT INTO assertions(id,subject,predicate,scope,slot,value,tier,asserted_by,asserted_at) "
                   "VALUES('cf:1','one','has_form','','','\"unresolved\"','observed','bob','2026-10-02T00:00:00.000Z')")
        db.execute("INSERT INTO assertion_evidence(assertion,kind,ref) VALUES('cf:1','crop',?)", (version,))
        db.executescript(sql)
        assert db.execute("SELECT count(*) FROM assertions").fetchone()[0] == 2
        status, supporting, crop = db.execute("SELECT status,supporting,crop_version FROM current_claims").fetchone()
        assert (status, json.loads(supporting), crop) == ("disputed", [], version), "the site's own claim counts too"
