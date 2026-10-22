import gzip
import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_publish import PAGE, UNITS, crops, ids, layout, run, unit  # noqa: F401 -- the fixture

from glyph_atlas import withdrawn


@pytest.fixture
def withdraw(layout, monkeypatch):  # noqa: F811
    sys.modules.pop("withdraw", None)
    module = importlib.import_module("withdraw")
    monkeypatch.setattr(withdrawn, "documents", lambda: frozenset({"book:a", "hl:never"}))
    return module


def write(withdraw, monkeypatch, path):
    monkeypatch.setattr(sys, "argv", ["withdraw.py", str(path)])
    withdraw.main()
    return path


def entry_of(root, text_id):
    cid = ids(root)[text_id]
    rev = sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT rev FROM pages WHERE id=?", (cid // PAGE,)).fetchone()[0]
    body = json.loads(gzip.decompress((root / "out" / "pages" / f"{cid // PAGE}-{rev}.json.gz").read_bytes()))
    return next(e for e in body["crops"] if e["id"] == text_id)


def test_a_withdrawal_names_every_crop_on_the_documents_pages_at_its_revision(layout, withdraw, monkeypatch, tmp_path):  # noqa: F811
    path = write(withdraw, monkeypatch, tmp_path / "w.sqlite")
    rows = sqlite3.connect(path).execute("SELECT id, origin, revision, document FROM units ORDER BY id").fetchall()
    assert rows == [("ex:a1", "withdrawn", 0, "book:a"), ("ex:a2", "withdrawn", 3, "book:a"), ("ex:b1", "withdrawn", 0, "book:a")]
    with pytest.raises(SystemExit):
        write(withdraw, monkeypatch, path)


def test_a_withdrawn_crop_leaves_the_site_with_its_record_runs_and_images(layout, withdraw, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    publish.media_map.merge([("exa1", "pack-1.bin", 0, 10, "image/webp"), ("exa2", "pack-1.bin", 10, 10, "image/webp"),
                             ("keep", "pack-1.bin", 20, 10, "image/webp")])
    publish.media_map.send(remote.upload)
    run(publish, monkeypatch, write(withdraw, monkeypatch, tmp_path / "w.sqlite"))
    gone = {ids(root)[i] for i in ("ex:a1", "ex:a2", "ex:b1")}
    assert not gone & set(crops(remote)) and ids(root)["codh:g1"] in crops(remote)
    assert remote.query("glyph-atlas-v3-runs", "SELECT * FROM runs") == []
    assert entry_of(root, "ex:a2") == {"slot": 1, "id": "ex:a2", "retired": True, "withdrawn": True, "cut": 0}
    shard = json.loads(gzip.decompress((root / "out" / "media" / "exa.json.gz").read_bytes()))
    assert shard["keys"] == {} and shard["objects"] == ["pack-1.bin"]
    assert set(json.loads(gzip.decompress((root / "out" / "media" / "kee.json.gz").read_bytes()))["keys"]) == {"p"}
    assert remote.uploaded.count("exa.json.gz") == 2
    assert json.loads((max((root / "out" / "publish").glob("*-w")) / "unmapped.json").read_text()) == ["exa1", "exa2", "exb1"]
    # Run again, the same withdrawal finds nothing to change.
    uploaded = list(remote.uploaded)
    run(publish, monkeypatch, write(withdraw, monkeypatch, tmp_path / "again.sqlite"))
    assert remote.uploaded == uploaded


def test_a_withdrawal_reviewed_since_is_refused(layout, withdraw, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    path = write(withdraw, monkeypatch, tmp_path / "w.sqlite")
    sqlite3.connect(path).execute("UPDATE units SET revision=2 WHERE id='ex:a2'").connection.commit()
    with pytest.raises(SystemExit):
        run(publish, monkeypatch, path)
    assert ids(root)["ex:a2"] in crops(remote)


def test_a_crop_retired_before_its_document_was_withdrawn_gives_up_its_record(layout, withdraw, monkeypatch, tmp_path):  # noqa: F811
    root, publish, _remote = layout
    gone = unit("ex:b1", "hl:p2", {"x": 0, "y": 0, "w": 10, "h": 10}, "う")
    db = sqlite3.connect(tmp_path / "retired.sqlite")
    db.execute(f"CREATE TABLE units({','.join(UNITS)})")
    db.execute(f"INSERT INTO units VALUES({','.join('?' * len(UNITS))})", (gone[0], "retired", *gone[2:]))
    db.commit()
    run(publish, monkeypatch, tmp_path / "retired.sqlite")
    assert entry_of(root, "ex:b1")["box"] == {"x": 0, "y": 0, "w": 10, "h": 10}
    run(publish, monkeypatch, write(withdraw, monkeypatch, tmp_path / "w.sqlite"))
    assert entry_of(root, "ex:b1") == {"slot": 0, "id": "ex:b1", "retired": True, "withdrawn": True, "cut": 0}
