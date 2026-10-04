import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("prepare", Path(__file__).parents[1] / "scripts" / "prepare_publication.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def test_an_sql_file_splits_into_whole_statements():
    text = "DELETE FROM a WHERE id='x';\n\nINSERT INTO b SELECT c\n FROM d GROUP BY c;\nDROP TABLE e;\n"
    assert prepare.statements(text) == ["DELETE FROM a WHERE id='x';\n", "INSERT INTO b SELECT c\n FROM d GROUP BY c;\n",
                                        "DROP TABLE e;\n"]
    with pytest.raises(SystemExit, match="ends inside a statement"):
        prepare.statements("UPDATE a SET b=1\n")


def test_a_publication_applies_the_runs_sealing_wrote():
    ngram_sql = ["DELETE FROM unit_ngrams WHERE first IN ('hk:1','hk:2');\n",
                 "INSERT OR IGNORE INTO unit_ngrams(first,second,text,document) SELECT p.column1 FROM (VALUES ('hk:1','hk:2')) AS p;\n"]
    sql = ("INSERT OR REPLACE INTO \"media\" VALUES('aa','pack',0,1,'image/webp');\n"
           "INSERT OR IGNORE INTO \"units\" VALUES('hk:1','local');\n"
           "INSERT OR IGNORE INTO \"units\" VALUES('hk:2','local');\n" + "".join(ngram_sql))
    ledger_sql = ["INSERT OR IGNORE INTO assertions(id) VALUES('lc:1');\n", "INSERT OR IGNORE INTO assertion_actions(id) VALUES('lc:2');\n",
                  "DELETE FROM current_claims WHERE 1;\n", "INSERT INTO current_claims(subject) SELECT 1;\n"]
    media, units, ngrams, claims = prepare.split_sealed(sql + "".join(ledger_sql), wanted={"aa"}, fresh={"hk:2"})
    assert len(media) == 1 and units == ["INSERT OR IGNORE INTO \"units\" VALUES('hk:2','local');\n"]
    assert ngrams == ngram_sql
    assert claims == ledger_sql


def test_parts_keep_their_order_and_stay_under_the_size(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "PART_BYTES", 40)
    parts = prepare.write_parts(tmp_path, [["INSERT INTO m VALUES(1);\n", "INSERT INTO m VALUES(2);\n"], ["UPDATE u SET q=0;\n"]])
    assert parts == ["sql/part-01.sql", "sql/part-02.sql", "sql/part-03.sql"]
    assert "".join((tmp_path / p).read_text() for p in parts) == \
        "INSERT INTO m VALUES(1);\nINSERT INTO m VALUES(2);\nUPDATE u SET q=0;\n"


def test_a_statement_over_the_limit_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "STATEMENT_BYTES", 10)
    with pytest.raises(SystemExit, match="over D1's limit"):
        prepare.write_parts(tmp_path, [["INSERT INTO m VALUES(1);\n"]])


def export_with(tmp_path, pack_bytes):
    export = tmp_path / "export"
    export.mkdir()
    (export / "pack-0001.bin").write_bytes(b"x" * pack_bytes)
    with sqlite3.connect(export / "catalogue.sqlite") as db:
        db.execute("CREATE TABLE media (key TEXT PRIMARY KEY, object TEXT, offset INTEGER, size INTEGER, content_type TEXT)")
        db.execute("CREATE TABLE units (id TEXT PRIMARY KEY, data TEXT)")
        db.executemany("INSERT INTO media VALUES(?, 'pack-0001.bin', ?, 10, 'image/webp')", [("aa", 0), ("bb", 10), ("cc", 20)])
        db.executemany("INSERT INTO units VALUES(?, ?)", [
            ("ex:1", json.dumps({"image": "/atlas/media/aa.webp", "context_image": "/atlas/media/bb.webp"})),
            ("ex:2", json.dumps({"image": "/atlas/media/aa.webp", "context_image": "/atlas/media/cc.webp"})),
        ])
    return export


def test_crops_whose_images_lie_past_the_pack_are_left_out_here_and_in_the_export(tmp_path):
    export = export_with(tmp_path, 25)
    copy = tmp_path / "copy.sqlite"
    with sqlite3.connect(export / "catalogue.sqlite") as src, sqlite3.connect(copy) as dst:
        src.backup(dst)
    lost = prepare.drop_truncated(export, copy)
    assert lost == {"units": ["ex:2"], "media": ["cc"]}
    for target in (copy, export / "catalogue.sqlite"):
        with sqlite3.connect(target) as db:
            assert [i for i, in db.execute("SELECT id FROM units")] == ["ex:1"]
            assert [k for k, in db.execute("SELECT key FROM media ORDER BY key")] == ["aa", "bb"]


def test_a_complete_pack_loses_nothing(tmp_path):
    export = export_with(tmp_path, 30)
    assert prepare.drop_truncated(export, export / "catalogue.sqlite") == {"units": [], "media": []}


def test_an_export_made_before_a_migration_is_brought_up_to_it(tmp_path):
    export = tmp_path / "export"
    export.mkdir()
    with sqlite3.connect(export / "catalogue.sqlite") as db:
        migrations = sorted(Path("apps/cloudflare/migrations").glob("*.sql"))
        version = len(migrations)
        for migration in migrations[:-1]:
            db.executescript(migration.read_text())
        db.execute(f"PRAGMA user_version = {version - 1}")
    (tmp_path / "out").mkdir()
    copy = prepare.snapshot(export, tmp_path / "out")
    with sqlite3.connect(copy) as db:
        assert "document" in [row[1] for row in db.execute("PRAGMA table_info(units)")]
        assert "clustered" in [row[1] for row in db.execute("PRAGMA table_info(form_units)")]
        assert db.execute("PRAGMA user_version").fetchone()[0] == version


def test_the_published_status_carries_no_local_paths_disk_space_or_errors():
    from export_cloudflare import public_status

    raw = {"status": "running", "root": "/srv/x", "disk_free_bytes": 1, "completed": 3,
           "sources": [{"status": "running", "output": "pages/a", "error": "HTTP 404", "total": 5}],
           "extraction": {"recent": [{"path": "p", "errors": ["boom"], "pages": 2}]}}
    assert public_status(raw) == {"status": "snapshot", "completed": 3, "sources": [{"status": "snapshot", "total": 5}],
                                  "extraction": {"recent": [{"pages": 2}]}}


def test_a_new_extracted_crop_on_the_ink_of_a_live_crop_is_left_out():
    atlas = sqlite3.connect(":memory:")
    atlas.execute("CREATE TABLE units (id TEXT, data TEXT)")

    def data(page, x, y=10, w=40, h=40):
        return json.dumps({"page_id": page, "box": {"x": x, "y": y, "w": w, "h": h}})
    atlas.executemany("INSERT INTO units VALUES (?,?)", [
        ("ex:a", data("p", 100)),   # on the live crop a reviewer moved to x=110
        ("ex:b", data("p", 300)),   # clear of every live crop
        ("ex:c", data("q", 100)),   # same place, another page
        ("ex:d", data("p", 500)),   # over a retired crop only
        ("ex:e", data("p", 110)),   # already on the site, so not new
        ("hk:f", data("p", 110))])  # not an extracted crop
    live = [{"id": "ex:e", "origin": "local", "data": data("p", 110)},
            {"id": "ex:r", "origin": "retired", "data": data("p", 500)}]
    fresh = {"ex:a", "ex:b", "ex:c", "ex:d", "hk:f"}
    assert prepare.live_overlaps(atlas, fresh, live) == ["ex:a"]
    # A live file written before the origin column was read counts every row as local.
    assert prepare.live_overlaps(atlas, fresh, [{"id": "ex:r", "data": data("p", 500)}]) == ["ex:d"]


def test_a_crowded_live_range_is_read_in_pieces_each_id_once(monkeypatch):
    import re
    import subprocess

    ids = [f"ex:{a}{b}{c}" for a in "0az" for b in "019" for c in "05x"] + ["ex:1", "ex:10", "ex:zz"]
    def d1(sql, tries=4):
        low, high = re.search(r"u.id >= '([^']*)' AND u.id < '([^']*)'", sql).groups()
        found = [{"id": i} for i in ids if low <= i < high]
        if len(found) > 4:  # too large for one response
            raise subprocess.CalledProcessError(1, "wrangler")
        return found
    monkeypatch.setattr(prepare, "d1", d1)
    got = [row["id"] for low, high in [("ex:", "ex:1"), ("ex:1", "ex:2"), ("ex:z", "ex:~")]
           for row in prepare.read_range(low, high)]
    assert sorted(got) == sorted(i for i in ids if i.startswith(("ex:0", "ex:1", "ex:z")))


A, B, C, D = (c * 64 for c in "abcd")
UNIT_COLUMNS = "id,origin,character,family,visual_group,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual,document"


def resumed_export(tmp_path, keys):
    """An export resumed after its first run's packs were published and deleted: pack-0001 and pack-0002
    are gone, and the crop cut again went into pack-0003."""
    export = tmp_path / "export"
    export.mkdir()
    with sqlite3.connect(export / "catalogue.sqlite") as db:
        prepare.schema(db)
        db.executemany("INSERT INTO media VALUES(?,?,?,?,'image/webp')", [
            (A, "pack-0001.bin", 0, 5), (C, "pack-0002.bin", 0, 5), (D, "pack-0002.bin", 5, 5), (B, "pack-0003.bin", 0, 5)])
        for ident, key in keys.items():
            data = json.dumps({"id": ident, "label": "a", "image": f"/atlas/media/{key}.webp"})
            db.execute(f"INSERT INTO units({UNIT_COLUMNS}) VALUES({','.join('?' * 17)})",
                       (ident, "local", "a", None, None, "handwritten", "latin", "pending", 1, 1, 1, 0, data, "{}", "{}", "{}", None))
    (export / "pack-0003.bin").write_bytes(b"recut")
    return export


def prepare_run(tmp_path, monkeypatch, export, live_units, live_media):
    monkeypatch.chdir(tmp_path)
    requests = []

    def d1(sql, tries=4):
        requests.append(sql)
        assert sql.startswith("SELECT key, object, offset, size FROM media WHERE key IN (")
        return [{"key": k, "object": o, "offset": f, "size": n} for k, (o, f, n) in live_media.items() if f"'{k}'" in sql]
    monkeypatch.setattr(prepare, "d1", d1)
    monkeypatch.setattr(prepare, "MEDIA_CHUNK", 1)
    with sqlite3.connect(export / "catalogue.sqlite") as db:
        rows = {r[0]: r for r in db.execute("SELECT id, origin, revision, quiz, data, style FROM units")}
    live = tmp_path / "live.jsonl"
    live.write_text("".join(json.dumps(dict(zip(("id", "origin", "revision", "quiz", "data", "style"), rows[i], strict=True)) | {"reviewed": 0}) + "\n"
                            for i in live_units))
    monkeypatch.setattr("sys.argv", ["prepare", str(export), str(tmp_path / "out"), "--prefix", "ar:", "--live", str(live)])
    prepare.main()
    return tmp_path / "out" / "sealed", requests


def test_images_in_packs_deleted_after_publication_are_published_by_reference(tmp_path, monkeypatch):
    # ar:1 is on the site unchanged; ar:2 was cut again into a new pack; ar:3 is new to the site, but its
    # image already is (it was published for another crop).
    export = resumed_export(tmp_path, {"ar:1": A, "ar:2": B, "ar:3": C})
    live_media = {A: ("packs/" + "1" * 64 + ".bin", 40, 5), C: ("packs/" + "2" * 64 + ".bin", 0, 5)}
    sealed, requests = prepare_run(tmp_path, monkeypatch, export, ["ar:1"], live_media)
    assert len(requests) == 2, "only the images whose packs are gone are read, one chunk each"
    manifest = json.loads((sealed / "publication.json").read_text())
    assert [(sealed / o["file"]).read_bytes() for o in manifest["objects"]] == [b"recut"], "only the new pack is uploaded"
    with sqlite3.connect(sealed / "atlas.sqlite") as db:
        rows = {k: (o, f, n) for k, o, f, n in db.execute("SELECT key, object, offset, size FROM media")}
    assert rows[A] == live_media[A] and rows[C] == live_media[C]
    assert rows[B] == (manifest["objects"][0]["key"], 0, 5)
    sql = "".join((sealed / p).read_text() for p in manifest["sql"])
    assert f"'{B}','{manifest['objects'][0]['key']}'" in sql and f"'{C}','{live_media[C][0]}',0,5" in sql
    assert A not in sql, "an unchanged crop's image is not published again"
    assert not any("pack-000" in line for line in sql.splitlines()), "no row names an export pack"


def test_an_image_neither_on_disk_nor_on_the_site_is_named(tmp_path, monkeypatch):
    export = resumed_export(tmp_path, {"ar:1": A, "ar:2": B, "ar:4": D})
    with pytest.raises(SystemExit, match=f"1 images are neither on disk nor on the site .*: {D}$"):
        prepare_run(tmp_path, monkeypatch, export, ["ar:1"], {A: ("packs/x.bin", 0, 5)})
    assert json.loads((tmp_path / "out" / "unserved.json").read_text()) == [D]


def test_a_pack_written_under_a_deleted_packs_name_is_refused_before_the_export_is_touched(tmp_path, monkeypatch):
    export = resumed_export(tmp_path, {"ar:1": A, "ar:2": B})
    # An export run before packs were numbered past deleted ones wrote its new crop into pack-0001 again.
    with sqlite3.connect(export / "catalogue.sqlite") as db:
        db.execute("UPDATE media SET object='pack-0001.bin' WHERE key=?", (B,))
    (export / "pack-0003.bin").rename(export / "pack-0001.bin")
    (export / "pack-0001.bin").write_bytes(b"cut")
    with pytest.raises(SystemExit, match="two export runs under one name: pack-0001.bin"):
        prepare_run(tmp_path, monkeypatch, export, ["ar:1"], {A: ("packs/x.bin", 0, 5)})
    with sqlite3.connect(export / "catalogue.sqlite") as db:
        assert db.execute("SELECT count(*) FROM media").fetchone() == (4,)
