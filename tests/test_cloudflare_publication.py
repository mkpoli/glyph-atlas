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
        db.execute("INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "one", "local", "ア", "ア", None, None, "manuscript", "kana", "pending", 0, 1, 1, 0,
            data, "{}", "{}", "{}"))
        db.executemany("INSERT INTO media VALUES(?,?,?,?,?)", [
            (key, "pack-0001.bin", 0, 7, "image/webp"),
            (denied, "pack-0001.bin", 7, 7, "image/webp")])
    (local / "pack-0001.bin").write_bytes(b"allowedPRIVATE")
    record = json.dumps({"id": "corpus-one", "label": "イ"}, ensure_ascii=False).encode()
    with database(corpus / "corpus.sqlite") as db:
        db.execute("INSERT INTO corpus_units VALUES(?,?,?,?,?,?,?,?,?)", (
            "corpus-one", "イ", None, None, 1, "records.bin", 0, len(record), "manuscript"))
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
        db.execute("UPDATE units SET character='カ',state='checked',revision=3 WHERE id='one'")
        db.execute("INSERT INTO submissions VALUES('review','person','{}','{}','now',0)")
        db.commit()
        db.executescript(sql)
        assert db.execute("SELECT character,state,revision FROM units").fetchone() == ("カ", "checked", 3)
        assert db.execute("SELECT count(*) FROM submissions").fetchone()[0] == 1
        assert db.execute("SELECT * FROM corpus_characters").fetchall() == [("イ", "manuscript", 1)]


def test_truncated_publication_is_rejected(publication):
    module, local, corpus, output = publication
    (local / "pack-0001.bin").write_bytes(b"short")
    with pytest.raises(ValueError, match="Incomplete media pack"):
        module.seal(local, corpus, output)
