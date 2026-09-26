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
