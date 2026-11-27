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
    media_url,
    run,
    unit,
)

from glyph_atlas import withdrawn

# A pack named by its content (`pack_names`).
PACK = f"packs/{'1' * 64}.bin"


def media_key(seed):
    return media_url(seed).rsplit("/", 1)[-1].removesuffix(".webp")


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
    k1, k2, kb1, keep = media_key("ex:a1"), media_key("ex:a2"), media_key("ex:b1"), media_key("keep")
    publish.media_map.merge([(k1, PACK, 0, 10, "image/webp"), (k2, PACK, 10, 10, "image/webp"),
                             (keep, PACK, 20, 10, "image/webp")])
    publish.media_map.send(remote.upload)
    run(publish, monkeypatch, write(withdraw, monkeypatch, tmp_path / "w.sqlite"))
    gone = {ids(root)[i] for i in ("ex:a1", "ex:a2", "ex:b1")}
    assert not gone & set(crops(remote)) and ids(root)["codh:g1"] in crops(remote)
    assert remote.query("glyph-atlas-v3-runs", "SELECT * FROM runs") == []
    assert entry_of(root, "ex:a2") == {"slot": 1, "id": "ex:a2", "retired": True, "withdrawn": True, "cut": 0}
    for key in (k1, k2):
        shard = json.loads(gzip.decompress((root / "out" / "media" / f"{key[:3]}.json.gz").read_bytes()))
        assert shard["keys"] == {} and shard["objects"] == [PACK]
        assert remote.uploaded.count(f"{key[:3]}.json.gz") == 2
    assert set(json.loads(gzip.decompress((root / "out" / "media" / f"{keep[:3]}.json.gz").read_bytes()))["keys"]) == {keep[3:]}
    assert json.loads((max((root / "out" / "publish").glob("*-w")) / "unmapped.json").read_text()) == sorted([k1, k2, kb1])
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
    k1, k2 = media_key("ex:a1"), media_key("ex:a2")
    publish.media_map.merge([(k1, PACK, 0, 10, "image/webp"), (k2, PACK, 10, 10, "image/webp")])
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
    assert not kept.get("retired") and "withdrawn" not in kept and kept["image"] == media_url("ex:a2")
    shard = json.loads(gzip.decompress((root / "out" / "media" / f"{k2[:3]}.json.gz").read_bytes()))
    assert set(shard["keys"]) == {k2[3:]}
    assert json.loads(gzip.decompress((root / "out" / "media" / f"{k1[:3]}.json.gz").read_bytes()))["keys"] == {}
    assert entry_of(root, "ex:a1")["withdrawn"] is True and ids(root)["ex:a1"] not in crops(remote)
    refused = max((root / "out" / "publish").glob("*-w")) / "refused-withdrawals.txt"
    assert refused.read_text() == "ex:a2\n"


def test_an_image_another_crop_names_stays_mapped(layout, withdraw, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    k1, k2, kb1 = media_key("ex:a1"), media_key("ex:a2"), media_key("ex:b1")
    publish.media_map.merge([(k1, PACK, 0, 10, "image/webp"), (k2, PACK, 10, 10, "image/webp")])
    publish.media_map.send(remote.upload)
    twin = list(unit("ex:z1", "hl:z1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ", document="book:z"))
    twin[UNITS.index("data")] = json.dumps({"page_id": "hl:z1", "box": {"x": 0, "y": 0, "w": 10, "h": 10},
                                            "image": media_url("ex:a1"), "label": "あ"})
    run(publish, monkeypatch, catalogue(tmp_path / "z.sqlite", [tuple(twin)]))
    run(publish, monkeypatch, write(withdraw, monkeypatch, tmp_path / "w.sqlite"))
    shard = json.loads(gzip.decompress((root / "out" / "media" / f"{k1[:3]}.json.gz").read_bytes()))
    assert set(shard["keys"]) == {k1[3:]}
    assert json.loads((max((root / "out" / "publish").glob("*-w")) / "unmapped.json").read_text()) == sorted([k2, kb1])


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
    k1 = media_key("ex:a1")
    publish.media_map.merge([(k1, PACK, 0, 10, "image/webp")])
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
    assert set(json.loads(gzip.decompress((root / "out" / "media" / f"{k1[:3]}.json.gz").read_bytes()))["keys"]) == {k1[3:]}
    # The next publish finishes the pending run, and the keys go then.
    monkeypatch.setattr(publish, "deliver", deliver)
    run(publish, monkeypatch, write(withdraw, monkeypatch, tmp_path / "again.sqlite"))
    assert json.loads(gzip.decompress((root / "out" / "media" / f"{k1[:3]}.json.gz").read_bytes()))["keys"] == {}


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


def photographed(root, publish, remote, monkeypatch, path, photos):
    """Publish the facts `photos` ({page key: photo URL or None}) of book:a, with every image mapped."""
    db = sqlite3.connect(catalogue(path, []))
    db.execute("CREATE TABLE pages(id, document, data)")
    db.executemany("INSERT INTO pages VALUES(?, 'book:a', ?)",
                   [(key, json.dumps({"page_number": n, **({"photo": url} if url else {})})) for n, (key, url) in enumerate(photos.items())])
    db.commit()
    db.close()
    run(publish, monkeypatch, path)


def facts_of(root, key):
    pid, rev = sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT id, rev FROM pages WHERE key=?", (key,)).fetchone()
    return json.loads(gzip.decompress((root / "out" / "pages" / f"{pid}-{rev}.json.gz").read_bytes()))["page"]


def mapped(root, prefix):
    return set(json.loads(gzip.decompress((root / "out" / "media" / f"{prefix}.json.gz").read_bytes()))["keys"])


@pytest.fixture
def photos(layout):  # noqa: F811
    _root, publish, remote = layout
    publish.media_map.merge([(key, PACK, 10 * n, 10, "image/webp") for n, key in enumerate(("fo1", "fo2", "fo3", "fox"))])
    publish.media_map.send(remote.upload)
    return layout


def test_a_page_whose_crops_are_all_withdrawn_gives_up_its_hosted_photograph(photos, withdraw, monkeypatch, tmp_path):
    root, publish, remote = photos
    photographed(root, publish, remote, monkeypatch, tmp_path / "c.sqlite", {"hl:p1": "/atlas/media/fo1.webp", "hl:p2": "/atlas/media/fo2.webp"})
    assert facts_of(root, "hl:p1")["photo"] == "/atlas/media/fo1.webp" and mapped(root, "fo1") == {""}
    run(publish, monkeypatch, write(withdraw, monkeypatch, tmp_path / "w.sqlite"))
    assert "photo" not in facts_of(root, "hl:p1") and "photo" not in facts_of(root, "hl:p2")
    assert mapped(root, "fo1") == mapped(root, "fo2") == set()
    # An image no withdrawn page names stays.
    assert mapped(root, "fox") == {""}


def test_a_photograph_a_page_stops_naming_leaves_the_map_unless_another_page_shows_it(photos, monkeypatch, tmp_path):
    root, publish, remote = photos
    photographed(root, publish, remote, monkeypatch, tmp_path / "a.sqlite", {"hl:p1": "/atlas/media/fo1.webp", "hl:p2": "/atlas/media/fo1.webp"})
    # One page takes another photograph: the first is still the other page's.
    photographed(root, publish, remote, monkeypatch, tmp_path / "b.sqlite", {"hl:p1": "/atlas/media/fo3.webp", "hl:p2": "/atlas/media/fo1.webp"})
    assert mapped(root, "fo1") == mapped(root, "fo3") == {""}
    # The other page names none: nothing shows the first any more.
    photographed(root, publish, remote, monkeypatch, tmp_path / "c.sqlite", {"hl:p1": "/atlas/media/fo3.webp", "hl:p2": None})
    assert "photo" not in facts_of(root, "hl:p2")
    assert mapped(root, "fo1") == set() and mapped(root, "fo3") == {""}


def test_a_page_with_no_live_crop_left_gives_up_its_photograph(photos, monkeypatch, tmp_path):
    root, publish, remote = photos
    photographed(root, publish, remote, monkeypatch, tmp_path / "a.sqlite", {"hl:p2": "/atlas/media/fo2.webp"})
    gone = unit("ex:b1", "hl:p2", {"x": 0, "y": 0, "w": 10, "h": 10}, "う")
    db = sqlite3.connect(tmp_path / "retired.sqlite")
    db.execute(f"CREATE TABLE units({','.join(UNITS)})")
    db.execute(f"INSERT INTO units VALUES({','.join('?' * len(UNITS))})", (gone[0], "retired", *gone[2:]))
    db.commit()
    db.close()
    run(publish, monkeypatch, tmp_path / "retired.sqlite")
    assert "photo" not in facts_of(root, "hl:p2") and mapped(root, "fo2") == set()
