import gzip
import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "atlas3"))


def archive(tmp_path, monkeypatch, *flags):
    monkeypatch.setenv("ATLAS3_DIR", str(tmp_path))
    for name in ("build", "review_archive"):
        sys.modules.pop(name, None)
    module = importlib.import_module("review_archive")
    monkeypatch.setattr(sys, "argv", ["review_archive.py", "--snapshots", str(tmp_path / "snapshots.sqlite"), *flags])
    module.main()
    return module


def write(path, table, columns, rows):
    db = sqlite3.connect(path)
    db.execute(f"CREATE TABLE {table}({','.join(columns)})")
    db.executemany(f"INSERT INTO {table} VALUES({','.join('?' * len(columns))})", rows)
    db.commit()


def event(id, target, kind="review", expected=0):
    return (id, target, kind, json.dumps({"id": id, "target_id": target, "evidence": '{ "verdict": "wrong" }'}),
            json.dumps({"character": {"id": target, "revision": expected}}), expected)


@pytest.fixture
def sources(tmp_path):
    (tmp_path / "out").mkdir()
    write(tmp_path / "out" / "mirror.sqlite", "ids", ["text", "id"], [("ex:a", (1 << 24) + 3), ("ex:b", (2 << 24) + 1)])
    write(tmp_path / "source-units.sqlite", "units", ["_rowid", "id", "origin"],
          [(1, "ex:a", "local"), (2, "ex:b", "retired"), (3, "ex:gone", "local")])
    write(tmp_path / "source-events.sqlite", "events", ["id", "target", "kind", "event", "snapshot", "expected_revision"],
          [event("cf:1", "ex:a"), event("cf:2", "ex:a", "undo", 1), event("cf:3", "ex:b"), event("cf:4", "ex:gone")])
    write(tmp_path / "snapshots.sqlite", "units", ["id", "snapshot"],
          [("ex:a", json.dumps({"character": {"id": "ex:a", "revision": 0}})), ("ex:b", None)])
    return tmp_path


def test_reviews_are_archived_by_page_as_the_old_export_gave_them(sources, monkeypatch, capsys):
    uploaded = []
    monkeypatch.setitem(sys.modules, "publish", type(sys)("publish"))
    sys.modules["publish"].upload = lambda files, prefix: uploaded.extend(prefix + f.name for f in files)
    archive(sources, monkeypatch, "--send")
    out = sources / "out" / "review-legacy"
    first = json.loads(gzip.decompress((out / "1.json.gz").read_bytes()))
    assert first["version"] == 1 and sorted(first["reviews"]) == ["legacy:cf:1", "legacy:cf:2"]
    assert first["reviews"]["legacy:cf:1"]["publication_snapshot"] == {"character": {"id": "ex:a", "revision": 0}}
    assert first["reviews"]["legacy:cf:1"]["event"]["evidence"] == '{ "verdict": "wrong" }'
    assert first["reviews"]["legacy:cf:2"]["legacy_hidden"] is True
    second = json.loads(gzip.decompress((out / "2.json.gz").read_bytes()))
    assert second["reviews"]["legacy:cf:3"]["publication_snapshot"] == {}
    assert uploaded == ["v3/review-legacy/1.json.gz", "v3/review-legacy/2.json.gz"]
    text = capsys.readouterr().out
    assert "3 reviews (1 hidden) in 2 page archives" in text and "left out (1): crop the mirror lacks" in text
    # The same copies give the same bytes.
    before = (out / "1.json.gz").read_bytes()
    archive(sources, monkeypatch)
    assert (out / "1.json.gz").read_bytes() == before


def test_a_collected_crop_without_its_publication_record_stops_the_archive(sources, monkeypatch):
    db = sqlite3.connect(sources / "snapshots.sqlite")
    db.execute("DELETE FROM units WHERE id='ex:a'")
    db.commit()
    with pytest.raises(SystemExit, match="ex:a: no publication record"):
        archive(sources, monkeypatch)
