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
