import gc
import gzip
import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_publish import (  # noqa: F401 -- the fixture
    PAGE,
    UNITS,
    catalogue,
    crops,
    ids,
    layout,
    run,
    unit,
)

from glyph_atlas import withdrawn

# A pack named by its content (`pack_names`).
PACK = f"packs/{'1' * 64}.bin"


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
    publish.media_map.merge([("exa1", PACK, 0, 10, "image/webp"), ("exa2", PACK, 10, 10, "image/webp"),
                             ("keep", PACK, 20, 10, "image/webp")])
    publish.media_map.send(remote.upload)
    run(publish, monkeypatch, write(withdraw, monkeypatch, tmp_path / "w.sqlite"))
    gone = {ids(root)[i] for i in ("ex:a1", "ex:a2", "ex:b1")}
    assert not gone & set(crops(remote)) and ids(root)["codh:g1"] in crops(remote)
    assert remote.query("glyph-atlas-v3-runs", "SELECT * FROM runs") == []
    assert entry_of(root, "ex:a2") == {"slot": 1, "id": "ex:a2", "retired": True, "withdrawn": True, "cut": 0}
    shard = json.loads(gzip.decompress((root / "out" / "media" / "exa.json.gz").read_bytes()))
    assert shard["keys"] == {} and shard["objects"] == [PACK]
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


def test_a_crop_reviewed_on_the_site_since_keeps_its_row_record_and_images(layout, withdraw, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    publish.media_map.merge([("exa1", PACK, 0, 10, "image/webp"), ("exa2", PACK, 10, 10, "image/webp")])
    publish.media_map.send(remote.upload)
    path = write(withdraw, monkeypatch, tmp_path / "w.sqlite")
    a2 = ids(root)["ex:a2"]
    # Reviewed on the site after the withdrawal was written: D1's revision moves past the local copy's.
    remote.dbs["glyph-atlas-v3"].execute("UPDATE crops SET revision=4 WHERE id=?", (a2,)).connection.commit()
    with pytest.raises(SystemExit, match="1 withdrawn crops were reviewed on the site since.*ex:a2"):
        run(publish, monkeypatch, path)
    assert a2 in crops(remote) and crops(remote)[a2]["revision"] == 4
    assert sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT revision FROM crops WHERE id=?", (a2,)).fetchone() == (4,)
    kept = entry_of(root, "ex:a2")
    assert not kept.get("retired") and "withdrawn" not in kept and kept["image"].endswith("exa2.webp")
    shard = json.loads(gzip.decompress((root / "out" / "media" / "exa.json.gz").read_bytes()))
    assert set(shard["keys"]) == {"2"}
    assert entry_of(root, "ex:a1")["withdrawn"] is True and ids(root)["ex:a1"] not in crops(remote)
    refused = max((root / "out" / "publish").glob("*-w")) / "refused-withdrawals.txt"
    assert refused.read_text() == "ex:a2\n"


def test_an_image_another_crop_names_stays_mapped(layout, withdraw, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    publish.media_map.merge([("exa1", PACK, 0, 10, "image/webp"), ("exa2", PACK, 10, 10, "image/webp")])
    publish.media_map.send(remote.upload)
    twin = list(unit("ex:z1", "hl:z1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ", document="book:z"))
    twin[UNITS.index("data")] = json.dumps({"page_id": "hl:z1", "box": {"x": 0, "y": 0, "w": 10, "h": 10},
                                            "image": "/atlas/media/exa1.webp", "label": "あ"})
    run(publish, monkeypatch, catalogue(tmp_path / "z.sqlite", [tuple(twin)]))
    run(publish, monkeypatch, write(withdraw, monkeypatch, tmp_path / "w.sqlite"))
    shard = json.loads(gzip.decompress((root / "out" / "media" / "exa.json.gz").read_bytes()))
    assert set(shard["keys"]) == {"1"}
    assert json.loads((max((root / "out" / "publish").glob("*-w")) / "unmapped.json").read_text()) == ["exa2", "exb1"]


def test_a_replacement_never_ends_at_a_withdrawn_crop(layout, withdraw, monkeypatch, tmp_path):  # noqa: F811
    root, publish, _remote = layout
    z = [unit(f"ex:z{k}", "hl:z1", {"x": 0, "y": 20 * k, "w": 10, "h": 10}, "あ", document="book:z") for k in (1, 2)]
    run(publish, monkeypatch, catalogue(tmp_path / "z.sqlite", z))
    gone = list(z[0])
    gone[UNITS.index("origin")] = "retired"
    path = catalogue(tmp_path / "gone.sqlite", [tuple(gone)])
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE unit_redirects(id, target)")
    db.execute("INSERT INTO unit_redirects VALUES('ex:z1', 'ex:a1')")
    db.commit()
    run(publish, monkeypatch, path)
    assert entry_of(root, "ex:z1")["replaced_by"] == ids(root)["ex:a1"]
    redirects = sqlite3.connect(root / "source-unit_redirects.sqlite")
    redirects.execute("CREATE TABLE IF NOT EXISTS unit_redirects(id, target)")
    redirects.executemany("INSERT INTO unit_redirects VALUES(?, ?)", [("ex:z1", "ex:a1"), ("ex:a1", "ex:z2")])
    redirects.commit()
    run(publish, monkeypatch, write(withdraw, monkeypatch, tmp_path / "w.sqlite"))
    assert entry_of(root, "ex:z1")["replaced_by"] == ids(root)["ex:z2"]


def test_the_image_map_changes_only_in_a_run_that_is_pending(layout, withdraw, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    publish.media_map.merge([("exa1", PACK, 0, 10, "image/webp")])
    publish.media_map.send(remote.upload)
    path = write(withdraw, monkeypatch, tmp_path / "w.sqlite")
    run(publish, monkeypatch, path, "--dry-run")
    deliver = publish.deliver

    def stop(*_args):
        sys.exit("stopped")
    monkeypatch.setattr(publish, "deliver", stop)
    with pytest.raises(SystemExit, match="stopped"):
        run(publish, monkeypatch, path)
    assert not (root / "out" / "media" / "unsent").exists()
    assert set(json.loads(gzip.decompress((root / "out" / "media" / "exa.json.gz").read_bytes()))["keys"]) == {"1"}
    # The next publish finishes the pending run, and the keys go then.
    monkeypatch.setattr(publish, "deliver", deliver)
    run(publish, monkeypatch, write(withdraw, monkeypatch, tmp_path / "again.sqlite"))
    assert json.loads(gzip.decompress((root / "out" / "media" / "exa.json.gz").read_bytes()))["keys"] == {}


def test_a_withdrawal_catalogue_holds_withdrawals_alone(layout, withdraw, monkeypatch, tmp_path):  # noqa: F811
    _root, publish, _remote = layout
    path = write(withdraw, monkeypatch, tmp_path / "w.sqlite")
    db = sqlite3.connect(path)
    db.execute("INSERT INTO units(id, origin, revision, document, data) VALUES('ex:z9', 'local', 0, 'book:z', '{}')")
    db.commit()
    with pytest.raises(SystemExit, match="withdrawn units alone"):
        run(publish, monkeypatch, path)


def test_a_withdrawal_stopped_after_the_deletion_finishes_when_run_again(layout, withdraw, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    path = write(withdraw, monkeypatch, tmp_path / "w.sqlite")
    a1, a2 = ids(root)["ex:a1"], ids(root)["ex:a2"]
    remote.dbs["glyph-atlas-v3"].execute("UPDATE crops SET revision=4 WHERE id=?", (a2,)).connection.commit()
    write_parts = publish.write_parts

    def stop(*_args):
        sys.exit("stopped")
    monkeypatch.setattr(publish, "write_parts", stop)
    with pytest.raises(SystemExit, match="stopped") as stopped:
        run(publish, monkeypatch, path)
    # The stopped process's connections go with it.
    del stopped
    gc.collect()
    assert a1 not in crops(remote) and "withdrawn" not in entry_of(root, "ex:a1")
    monkeypatch.setattr(publish, "write_parts", write_parts)
    with pytest.raises(SystemExit, match="1 withdrawn crops were reviewed on the site since.*ex:a2"):
        run(publish, monkeypatch, path)
    assert entry_of(root, "ex:a1")["withdrawn"] is True and "withdrawn" not in entry_of(root, "ex:a2")
    assert sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT revision FROM crops WHERE id=?", (a2,)).fetchone() == (4,)
