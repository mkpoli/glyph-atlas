import gzip
import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_publish import (  # noqa: F401 -- `layout` is the fixture
    PAGE,
    UNITS,
    crops,
    ids,
    layout,
    run,
    unit,
    write_source,
)

# The scripts folder is on the path once test_atlas3_publish is imported.
votes = importlib.import_module("votes")

ENGINES = [{"name": "NDLkotenOCR", "sha256": "fd75", "provider": "CUDAExecutionProvider"},
           {"name": "Atlas classifier", "sha256": "4360", "provider": "CUDAExecutionProvider"}]


def visual(text="三", image="/atlas/media/a.webp"):
    return json.dumps({"status": "ready", "candidates": [
        {"text": text, "score": 0.77, "engine": "NDLkotenOCR"},
        {"text": "ミ", "engine": "Atlas classifier", "verified": False, "identity_scope": "character", "family": "U+30DF", "score": 0.1}],
        "engines": ENGINES, "votes": [{"text": text, "score": 0.88, "engine": "Atlas classifier", "identity_scope": "character",
                                       "family": "U+4E09", "members": [text]}], "input_image": image, "verified": False},
        ensure_ascii=False)


CONTEXT = json.dumps({"status": "ready", "basis": "transcription", "candidates": [
    {"text": "三", "engine": "Nearby transcription", "offset": 0, "before": "モ載タリ", "after": "十日ノ旅"},
    {"text": "三十", "engine": "Nearby transcription", "offset": 0, "before": "モ載タリ", "after": "日ノ旅行"}],
    "line_id": "hk:1:L13", "anchor": 9, "source_text": "三"}, ensure_ascii=False)
NONE = json.dumps({"status": "unavailable", "candidates": []})
NO_CONTEXT = json.dumps({"status": "unavailable", "candidates": [], "basis": "transcription"})


def test_votes_are_stored_without_what_every_row_repeats_and_restore_whole():
    key, stored = votes.visual(visual())
    guessed = votes.context(CONTEXT)
    found = votes.entry(0, 1, stored, guessed)
    assert json.loads(key) == ENGINES and "NDLkotenOCR" not in votes.compact(found)
    assert "Nearby transcription" not in votes.compact(found)
    back = votes.restore(json.loads(key), found)
    assert back["visual"] == {k: v for k, v in json.loads(visual()).items() if k != "input_image"}
    assert back["context"] == json.loads(CONTEXT)
    assert votes.visual(NONE) == (None, None) and votes.context(NO_CONTEXT) is None
    assert votes.entry(0, None, None, None) is None
    assert votes.restore(None, None) == {"visual": json.loads(NONE), "context": json.loads(NO_CONTEXT)}


def facts(root, remote, monkeypatch, *flags):
    for name in ("crop_facts", "load_history", "history", "media_map"):
        sys.modules.pop(name, None)
    module = importlib.import_module("crop_facts")
    loader = importlib.import_module("load_history")
    importlib.import_module("glyph_atlas.forms")._CLUSTERS.invalidate()

    def applied(database, stamp):
        return bool(remote.query(database, f"SELECT 1 AS n FROM imports WHERE stamp = '{stamp}'"))

    for m in (module, loader):
        monkeypatch.setattr(m, "send", remote.send)
        monkeypatch.setattr(m, "applied", applied)
    monkeypatch.setattr(module, "query", remote.query)
    monkeypatch.setattr(module, "upload", remote.upload)
    remote.removed = getattr(remote, "removed", [])
    monkeypatch.setattr(module, "remove", lambda names, prefix: remote.removed.extend(prefix + n for n in names))
    # Tiles: every glyph but those of `remote.unheld` has pixels on disk, all with one tile.
    monkeypatch.setattr(module, "tile_keys", lambda corpus_root, wanted: (
        {i: getattr(remote, "tile", TILE) for i in wanted if i not in getattr(remote, "unheld", ())}, None))
    remote.packed = getattr(remote, "packed", [])
    monkeypatch.setattr(module, "pack", lambda media, keys, folder, workers: (
        folder.mkdir(parents=True, exist_ok=True), (folder / "p.bin").write_bytes(b"RIFF"),
        remote.packed.append(sorted(keys)), ([(k, "packs/p.bin", 0, 4, None) for k in keys], set()))[-1])
    monkeypatch.setattr(module, "put_packs", lambda files: remote.uploaded.extend(f"packs/{f.name}" for f in files))
    monkeypatch.setattr(sys, "argv", ["crop_facts.py", "--clusters", str(root / "forms"), *flags])
    try:
        module.main()
    finally:
        remote.dbs["glyph-atlas-v3"].row_factory = None
    return module


TILE = "ab" * 32
SHA = "cd" * 32
# The clustering of か: c1 with three glyphs (one the mirror lacks), c2 with a glyph the mirror lacks.
CLUSTERS = {"U+304B:c1": [("codh:g1", 0.9), ("ex:a1", 0.9), ("ex:b1", 0.9), ("codh:gone", 0.8)],
            "U+304B:c2": [("codh:gone2", 0.8)]}


def clustering(root, monkeypatch, clusters=None, revision="r1", order=None):
    """The clustering at `root/forms`, as `atlas forms cluster` writes it: one family of か."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    monkeypatch.setenv("ATLAS_FORM_CLUSTERS", str(root / "forms"))
    clusters = CLUSTERS if clusters is None else clusters
    folder = root / "forms"
    folder.mkdir(exist_ok=True)
    rows = [(i, key, sim, rank) for key, ms in clusters.items() for rank, (i, sim) in enumerate(ms)]
    (folder / "clusters.json").write_text(json.dumps({"revision": revision, "families": {"U+304B": {
        "family": "U+304B", "char": "か", "label": "か", "count": len(rows),
        "members": [{"code_point": "U+304B", "char": "か"}, {"code_point": "U+1B019", "char": "𛀙"}],
        "clusters": [{"id": key, "label": f"Cluster {n}", "count": len(ms), "coherence": 0.9}
                     for n, (key, ms) in enumerate(clusters.items(), 1)]}}}))
    pq.write_table(pa.table({"id": [r[0] for r in rows], "family": ["U+304B"] * len(rows), "cluster": [r[1] for r in rows],
                             "similarity": [r[2] for r in rows], "rank": [r[3] for r in rows]}), folder / "units.parquet")
    np.save(folder / "embeddings.npy", np.array([[1, n % 3] for n in range(len(rows))], np.float16))
    (folder / "neighbours.json").write_text(json.dumps({"U+304B": {"order": order, "adjacent": [1.0]}} if order else {}))


def hash_images(root, hashes, looks=None):
    """Give crops' bundle entries the image hash of their cut, and a tone and size."""
    for path in (root / "out" / "pages").glob("*.json.gz"):
        body = json.loads(gzip.decompress(path.read_bytes()))
        page = int(path.name.split("-")[0])
        for e in body["crops"]:
            crop = page * PAGE + e["slot"]
            if crop in hashes:
                e["image_sha256"] = hashes[crop]
            if crop in (looks or {}):
                e["tone"], e["image_size"] = looks[crop]
        path.write_bytes(gzip.compress(json.dumps(body, ensure_ascii=False).encode()))


def unit_facts(root, rows):
    (root / "source-unit_facts.sqlite").unlink(missing_ok=True)
    write_source(root, "unit_facts", ["id", "origin", "visual", "context", "image_sha256", "tone", "image_size"], rows)


def mapped_tile(root, key=TILE):
    """A tile the image map already places."""
    importlib.import_module("media_map").merge([(key, "packs/held.bin", 0, 10, None)])
    (root / "out" / "media" / "unsent").unlink()


def decide(root, remote, *rows):
    """Decisions in the loaded history, on D1 and the local copy: (seq, id, at, kind, cluster, form, crops, revision)."""
    known = ids(root)
    for db in (sqlite3.connect(root / "out" / "atlas.sqlite"), remote.dbs["glyph-atlas-v3"]):
        db.executemany("INSERT INTO form_decisions(seq, id, at, actor, kind, family, cluster, form, revision, units, crops, note) "
                       "VALUES(?, ?, ?, 'a', ?, 'U+304B', ?, ?, ?, ?, ?, '')",
                       [(seq, id, at, kind, cluster, form, revision, json.dumps(list(units)), json.dumps([known[u] for u in units]))
                        for seq, id, at, kind, cluster, form, units, revision in rows])
        db.commit()


def cluster_decided(root, remote, seq=1, id="d1", at="2026-01-01", cluster="U+304B:c1", form="か", revision="r1"):
    decide(root, remote, (seq, id, at, "cluster", cluster, form, (), revision))


def glyph_decided(root, remote, crops=("ex:a1", "ex:a2"), id="g1", seq=0, at="2025-01-01", form="𛀙", kind="glyph"):
    decide(root, remote, (seq, id, at, kind, None, form, crops, "r1"))


def retire(root, remote, crop):
    for db in (sqlite3.connect(root / "out" / "atlas.sqlite"), remote.dbs["glyph-atlas-v3"]):
        db.execute("DELETE FROM crops WHERE id=?", (crop,))
        db.commit()


def drop_0006(root, remote):
    """A layout loaded before migration 0006 (and before builds named themselves in `codes`)."""
    for db in (sqlite3.connect(root / "out" / "atlas.sqlite"), remote.dbs["glyph-atlas-v3"]):
        db.executescript("""
          DROP TRIGGER form_decision_while_loading; DROP TRIGGER form_decision_list_current; DROP TABLE form_state;
          DROP INDEX form_decision_list;
          ALTER TABLE form_decisions DROP COLUMN list; DROP TABLE form_glyphs; DROP TABLE form_clusters;
          DROP TABLE form_families; DELETE FROM codes WHERE field='build';""")
        db.commit()


def body(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def build_of(root):
    return sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT value FROM meta WHERE key='build'").fetchone()[0]


def test_the_clustering_loads_by_integer_crop_id_with_the_decisions_carried_by_key_and_crop(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    known = ids(root)
    a1, b1, g1 = known["ex:a1"], known["ex:b1"], known["codh:g1"]
    clustering(root, monkeypatch)
    mapped_tile(root)
    hash_images(root, {}, {a1: ("#cbc0b0", [83, 133])})
    retire(root, remote, b1)
    drop_0006(root, remote)
    # Two cluster decisions on c1, the later from an earlier clustering; a glyph decision on a1 and a2,
    # a2 outside the clustering.
    cluster_decided(root, remote, 1, "d0", "2026-01-01")
    cluster_decided(root, remote, 2, "d1", "2026-01-02", revision="r0")
    glyph_decided(root, remote)
    db, mirror = remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "mirror.sqlite")

    facts(root, remote, monkeypatch)
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='form_glyphs'").fetchone() and not remote.uploaded
    assert not mirror.execute("SELECT 1 FROM codes WHERE field='form_cluster'").fetchone()

    facts(root, remote, monkeypatch, "--send")
    assert db.execute("SELECT 1 FROM imports WHERE stamp='migration:0006'").fetchone()
    assert db.execute("SELECT key FROM codes WHERE field='build'").fetchone() == (build_of(root),)
    # The cluster's number is the mirror's; its list, named by its content, holds its live glyphs by rank
    # with their tile, split groups, tone and size; c2 is left with no glyph.
    c1 = mirror.execute("SELECT code FROM codes WHERE field='form_cluster' AND key='U+304B:c1'").fetchone()[0]
    assert db.execute("SELECT id, key, label, count, shape_position, size_position, form, decision FROM form_clusters").fetchall() == [
        (c1, "U+304B:c1", "Cluster 1", 2, 0, 0, "か", "d1")]
    name = db.execute("SELECT list FROM form_clusters").fetchone()[0]
    assert name.startswith(f"{c1}-")
    members = body(root / "out" / "forms" / f"{name}.json.gz")["members"]
    assert [m[:4] for m in members] == [[g1, 0, 0.9, TILE], [a1, 1, 0.9, TILE]]
    assert all(len(m[4]) == 7 for m in members) and [m[5:] for m in members] == [[None, None], ["#cbc0b0", [83, 133]]]
    char, count, clusters, assigned, forms_, revision, index = db.execute(
        "SELECT char, count, cluster_count, assigned, forms, revision, list FROM form_families").fetchone()
    assert (char, count, clusters, assigned, revision) == ("か", 2, 1, 2, "r1") and index.startswith("U+304B-")
    assert [f["char"] for f in json.loads(forms_)] == ["か", "𛀙"]
    assert body(root / "out" / "forms" / "families" / f"{index}.json.gz") == {"family": "U+304B", "clusters": {str(c1): {
        "list": name, "count": 2, "head": [[m[0], m[1], m[3], m[5], m[6]] for m in members], "tail": []}}}
    # a1 has its own form in its cluster; a2 keeps its decision outside the clustering.
    assert sorted(db.execute("SELECT crop, family, cluster, form, decision FROM form_glyphs").fetchall()) == sorted([
        (a1, "U+304B", c1, "𛀙", "g1"), (known["ex:a2"], "U+304B", None, "𛀙", "g1")])
    assert "v3/forms/" in remote.prefixes and "v3/forms/families/" in remote.prefixes
    report = (root / "out" / "crop-facts-report.txt").read_text()
    assert "dropped (2) crop the mirror lacks" in report and "dropped (1) crop no longer live (retired)" in report
    assert "dropped (1) no glyph left" in report
    assert "1 clusters take their latest cluster decision by key, 1 of them made on an earlier clustering" in report
    # A crop's cluster is in crops.
    assert dict(db.execute("SELECT id, cluster FROM crops WHERE cluster IS NOT NULL")) == {g1: c1, a1: c1}
    assert db.execute("SELECT lists LIKE 'facts-%', loading FROM form_state").fetchone() == (1, 0)
    # The local copy replayed the same parts; nothing is pending; a second run sends nothing.
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    for table in ("form_glyphs", "form_clusters", "form_families", "crops"):
        assert local.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    assert not mirror.execute("SELECT 1 FROM pending").fetchone()
    stamps, uploads = db.execute("SELECT count(*) FROM imports").fetchone(), len(remote.uploaded)
    facts(root, remote, monkeypatch, "--send")
    assert db.execute("SELECT count(*) FROM imports").fetchone() == stamps and len(remote.uploaded) == uploads


def test_a_tile_the_image_map_lacks_is_packed_and_mapped_before_a_list_names_it(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    clustering(root, monkeypatch)
    remote.unheld = {"codh:g1"}
    glyph_decided(root, remote)
    facts(root, remote, monkeypatch)
    # A dry run renders nothing and counts the tile.
    assert not remote.packed and not remote.uploaded
    assert "2 glyphs with pixels on disk, 1 without (no tile); 0 tiles the image map holds, 1 to render" in (
        root / "out" / "crop-facts-report.txt").read_text()
    facts(root, remote, monkeypatch, "--send")
    assert remote.packed == [[TILE]]
    # The pack, then the map's shard, then the lists.
    order = remote.uploaded
    assert order.index("packs/p.bin") < order.index(f"{TILE[:3]}.json.gz") < min(
        i for i, n in enumerate(order) if n.startswith(("1-", "U+304B-")))
    assert importlib.import_module("media_map").read_shard(TILE[:3])["keys"][TILE[3:]] == [0, 0, 4]
    name = remote.dbs["glyph-atlas-v3"].execute("SELECT list FROM form_clusters").fetchone()[0]
    members = body(root / "out" / "forms" / f"{name}.json.gz")["members"]
    assert [m[3] for m in members] == [None, TILE, TILE]
    # Mapped now, the tile is not rendered again.
    remote.packed.clear()
    facts(root, remote, monkeypatch, "--send")
    assert not remote.packed


def test_tiles_are_keyed_as_the_sites_media_and_packed_from_their_pixels(tmp_path, monkeypatch, form_corpora):
    root, glyph = form_corpora
    sys.modules.pop("crop_facts", None)
    module = importlib.import_module("crop_facts")
    wanted = {*glyph["held"], glyph["unheld"], glyph["crop"]}
    keys, media = module.tile_keys(root, wanted)
    assert set(keys) == {*glyph["held"], glyph["crop"]}
    rows, failed = module.pack(media, set(keys.values()), tmp_path / "packs", 2)
    assert not failed and {r[0] for r in rows} == set(keys.values())
    for key, obj, offset, size, content_type in rows:
        assert content_type == "image/webp"
        assert (tmp_path / obj).read_bytes()[offset:offset + size][:4] == b"RIFF"


def test_a_new_clustering_sends_the_difference_and_an_inherit_decision_returns_a_glyph_to_its_cluster(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    known = ids(root)
    clustering(root, monkeypatch)
    mapped_tile(root)
    cluster_decided(root, remote)
    glyph_decided(root, remote)
    facts(root, remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    c1, before = db.execute("SELECT id, list FROM form_clusters WHERE key='U+304B:c1'").fetchone()
    family = db.execute("SELECT list FROM form_families").fetchone()[0]
    uploads = len(remote.uploaded)
    # A new clustering leaves codh:g1 out; a1 inherits its cluster's form again; a later cluster decision.
    clustering(root, monkeypatch, {"U+304B:c1": [("ex:a1", 0.9), ("ex:b1", 0.8)]}, revision="r2")
    glyph_decided(root, remote, crops=("ex:a1",), id="g2", seq=3, at="2026-02-01", form=None, kind="inherit")
    cluster_decided(root, remote, 4, "d2", "2026-02-02", form="𛀙", revision="r2")
    facts(root, remote, monkeypatch, "--send")
    assert db.execute("SELECT id, form, decision FROM form_clusters").fetchall() == [(c1, "𛀙", "d2")]
    assert db.execute("SELECT crop FROM form_glyphs").fetchall() == [(known["ex:a2"],)]
    assert db.execute("SELECT count, assigned, revision, list <> ? FROM form_families", (family,)).fetchone() == (2, 2, "r2", 1)
    assert dict(db.execute("SELECT id, cluster FROM crops WHERE cluster IS NOT NULL")) == {known["ex:a1"]: c1, known["ex:b1"]: c1}
    # Only the changed list and its family's index went up.
    after = db.execute("SELECT list FROM form_clusters").fetchone()[0]
    assert after != before and sorted(remote.uploaded[uploads:]) == sorted(
        [f"{after}.json.gz", f"{db.execute('SELECT list FROM form_families').fetchone()[0]}.json.gz", "manifest.json.gz"])
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert local.execute("SELECT id, form FROM form_clusters").fetchall() == [(c1, "𛀙")]


def test_a_glyph_carries_its_latest_cluster_decision_until_its_cluster_is_decided(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    known = ids(root)
    a1, a2, g1 = known["ex:a1"], known["ex:a2"], known["codh:g1"]
    clustering(root, monkeypatch)
    mapped_tile(root)
    # c1 decided か; later a cluster of an earlier clustering, holding a1 and a2, was decided 𛀙.
    cluster_decided(root, remote, 1, "d1", "2025-12-01")
    decide(root, remote, (2, "d0", "2026-01-01", "cluster", "U+304B:old", "𛀙", ("ex:a1", "ex:a2"), "r0"))
    facts(root, remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    c1, listed = db.execute("SELECT id, list FROM form_clusters WHERE key='U+304B:c1'").fetchone()
    # a1 keeps 𛀙 in c1 and a2 outside the clustering, both naming d0 as carried; g1 takes c1's か.
    assert sorted(db.execute("SELECT crop, family, cluster, form, decision, carried FROM form_glyphs").fetchall()) == sorted([
        (a1, "U+304B", c1, "𛀙", "d0", "d0"), (a2, "U+304B", None, "𛀙", "d0", "d0")])
    assert g1 not in {r[0] for r in db.execute("SELECT crop FROM form_glyphs")}
    assert db.execute("SELECT count, assigned FROM form_families").fetchone() == (3, 3)
    # A decision on c1 made on the site, which names its list and no crops, replaces what a1 carries.
    for target in (sqlite3.connect(root / "out" / "atlas.sqlite"), db):
        target.execute("INSERT INTO form_decisions(seq, id, at, actor, kind, family, cluster, form, revision, units, crops, note, list) "
                       "VALUES(3, 'd2', '2026-02-01', 'a', 'cluster', 'U+304B', 'U+304B:c1', 'か', 'r1', '[]', '[]', '', ?)", (listed,))
        target.execute("UPDATE form_clusters SET decision='d2' WHERE id=?", (c1,))
        target.commit()
    facts(root, remote, monkeypatch, "--send")
    assert db.execute("SELECT crop, cluster, decision, carried FROM form_glyphs").fetchall() == [(a2, None, "d0", "d0")]
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert local.execute("SELECT * FROM form_glyphs").fetchall() == db.execute("SELECT * FROM form_glyphs").fetchall()


def test_a_decision_on_a_cluster_key_the_clustering_lacks_is_reported(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    clustering(root, monkeypatch)
    mapped_tile(root)
    cluster_decided(root, remote, cluster="U+304B:gone")
    facts(root, remote, monkeypatch)
    report = (root / "out" / "crop-facts-report.txt").read_text()
    assert "0 clusters take their latest cluster decision by key" in report and "1 cluster keys with decisions name no cluster" in report


def test_shape_positions_follow_the_neighbour_chain(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    clustering(root, monkeypatch, {"U+304B:c1": [("ex:a1", 0.9)], "U+304B:c2": [("ex:b1", 0.9)]},
               order=["U+304B:c2", "U+304B:c1"])
    mapped_tile(root)
    facts(root, remote, monkeypatch, "--send")
    assert remote.dbs["glyph-atlas-v3"].execute("SELECT key, shape_position, size_position FROM form_clusters ORDER BY key").fetchall() == [
        ("U+304B:c1", 1, 0), ("U+304B:c2", 0, 1)]


def test_a_load_without_the_history_is_refused(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    clustering(root, monkeypatch)
    sqlite3.connect(root / "out" / "atlas.sqlite").execute("DROP TABLE form_decisions")
    with pytest.raises(SystemExit, match="load the history"):
        facts(root, remote, monkeypatch)


def test_a_list_no_row_or_decision_names_is_deleted_after_the_cache_time(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    clustering(root, monkeypatch)
    mapped_tile(root)
    facts(root, remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    first, family = db.execute("SELECT form_clusters.list, form_families.list FROM form_clusters, form_families").fetchone()
    # A decision made on the first list keeps it.
    db.execute("INSERT INTO form_decisions(seq, id, at, actor, kind, family, cluster, revision, units, crops, note, list) "
               "VALUES(1, 'd9', '2026-10-21', 'a', 'cluster', 'U+304B', 'U+304B:c1', 'r1', '[]', '[]', '', ?)", (first,))
    db.commit()
    sqlite3.connect(root / "out" / "atlas.sqlite").execute("INSERT INTO form_decisions(seq, id, at, actor, kind, family, cluster, "
                                                           "revision, units, crops, note) VALUES(1, 'd9', '2026-10-21', 'a', "
                                                           "'cluster', 'U+304B', 'U+304B:c1', 'r1', '[]', '[]', '')").connection.commit()
    clustering(root, monkeypatch, {"U+304B:c1": [("ex:a1", 0.9)]})
    facts(root, remote, monkeypatch, "--send")
    manifest = root / "out" / "forms" / "manifest.json.gz"
    held = body(manifest)
    assert held[f"{first}.json.gz"] is None and isinstance(held[f"families/{family}.json.gz"], int)
    # A decision names only a list some cluster holds now: the first list is no cluster's any more.
    with pytest.raises(sqlite3.IntegrityError, match="cluster has changed"):
        db.execute("INSERT INTO form_decisions(seq, id, at, actor, kind, family, cluster, revision, units, crops, note, list) "
                   "VALUES(2, 'd10', '2026-10-21', 'a', 'cluster', 'U+304B', 'U+304B:c1', 'r1', '[]', '[]', '', ?)", (first,))
    db.rollback()
    assert not remote.removed
    # Two hours on, the next load deletes the family index nothing names; the list a decision names stays.
    manifest.write_bytes(gzip.compress(json.dumps({k: v and v - 7200 for k, v in held.items()}).encode()))
    facts(root, remote, monkeypatch, "--send")
    assert remote.removed == [f"v3/forms/families/{family}.json.gz"]
    assert not (root / "out" / "forms" / "families" / f"{family}.json.gz").exists()
    assert (root / "out" / "forms" / f"{first}.json.gz").exists()


class FailingDelete(list):
    def extend(self, names):
        raise SystemExit("deleting under v3/forms/ failed; rerun to finish")


def test_a_list_whose_delete_failed_goes_up_again_when_a_load_names_it(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    clustering(root, monkeypatch)
    mapped_tile(root)
    facts(root, remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    first, family = db.execute("SELECT form_clusters.list, form_families.list FROM form_clusters, form_families").fetchone()
    clustering(root, monkeypatch, {"U+304B:c1": [("ex:a1", 0.9)]})
    facts(root, remote, monkeypatch, "--send")
    manifest = root / "out" / "forms" / "manifest.json.gz"
    aged = {k: v and v - 7200 for k, v in body(manifest).items()}
    manifest.write_bytes(gzip.compress(json.dumps(aged).encode()))
    # The delete fails part way: which of the two objects R2 still holds is unknown.
    remote.removed = FailingDelete()
    with pytest.raises(SystemExit, match="deleting"):
        facts(root, remote, monkeypatch, "--send")
    assert not (root / "out" / "forms" / f"{first}.json.gz").exists()
    assert not (root / "out" / "forms" / "families" / f"{family}.json.gz").exists()
    assert body(manifest) == aged
    # The first clustering again: its list and index go up again, named once more.
    remote.removed = []
    uploads = len(remote.uploaded)
    clustering(root, monkeypatch)
    facts(root, remote, monkeypatch, "--send")
    assert {f"{first}.json.gz", f"{family}.json.gz"} <= set(remote.uploaded[uploads:])
    assert db.execute("SELECT form_clusters.list, form_families.list FROM form_clusters, form_families").fetchone() == (first, family)
    held = body(manifest)
    assert held[f"{first}.json.gz"] is None and held[f"families/{family}.json.gz"] is None
    assert f"v3/forms/{first}.json.gz" not in remote.removed


def test_a_load_over_decisions_the_site_made_is_refused(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    clustering(root, monkeypatch)
    db = remote.dbs["glyph-atlas-v3"]
    db.execute("INSERT INTO form_decisions(seq, id, at, actor, kind, family, revision, units, crops, note) "
               "VALUES(1, 'd9', '2026-10-21', 'a', 'cluster', 'U+304B', 'r1', '[]', '[]', '')")
    db.commit()
    with pytest.raises(SystemExit, match="form decisions"):
        facts(root, remote, monkeypatch, "--send")


def test_a_decision_made_while_the_rows_are_sent_stops_the_load_and_opens_decisions_again(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    clustering(root, monkeypatch)
    mapped_tile(root)
    db = remote.dbs["glyph-atlas-v3"]
    sent = remote.send

    def racing(database, file, stamp):
        # A decision lands between the first check and `loading`.
        if stamp.endswith(".begin-0000"):
            db.execute("INSERT INTO form_decisions(seq, id, at, actor, kind, family, revision, units, crops, note) "
                       "VALUES(1, 'd9', '2026-10-21', 'a', 'cluster', 'U+304B', 'r1', '[]', '[]', '')")
            db.commit()
        return sent(database, file, stamp)

    monkeypatch.setattr(remote, "send", racing)
    with pytest.raises(SystemExit, match="form decisions"):
        facts(root, remote, monkeypatch, "--send")
    assert db.execute("SELECT loading FROM form_state").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM form_clusters").fetchone() == (0,)
    assert not sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT 1 FROM pending").fetchone()
    # While loading, the site records no decision.
    db.execute("UPDATE form_state SET loading=1")
    with pytest.raises(sqlite3.IntegrityError, match="republished"):
        db.execute("INSERT INTO form_decisions(seq, id, at, actor, kind, family, revision, units, crops, note) "
                   "VALUES(2, 'd10', '2026-10-21', 'a', 'cluster', 'U+304B', 'r1', '[]', '[]', '')")


def test_a_rerun_of_a_refused_run_sets_loading_again_while_its_rows_go(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    clustering(root, monkeypatch)
    mapped_tile(root)
    facts(root, remote, monkeypatch, "--send")
    db, local = remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")
    # A cluster decision changes rows only, so the next load's run is named the same on each attempt.
    cluster_decided(root, remote)
    sent, names, loading = remote.send, [], []
    site = "VALUES(2, 'd9', '2026-10-21', 'a', 'cluster', 'U+304B', 'r1', '[]', '[]', '')"

    def racing(database, file, stamp):
        names.append(stamp.split(".")[0])
        if stamp.endswith(".begin-0000"):
            db.execute(f"INSERT INTO form_decisions(seq, id, at, actor, kind, family, revision, units, crops, note) {site}")
            db.commit()
        if ".rows-" in stamp:
            loading.append(db.execute("SELECT loading FROM form_state").fetchone()[0])
        return sent(database, file, stamp)

    monkeypatch.setattr(remote, "send", racing)
    with pytest.raises(SystemExit, match="form decisions"):
        facts(root, remote, monkeypatch, "--send")
    assert not loading
    local.execute(f"INSERT INTO form_decisions(seq, id, at, actor, kind, family, revision, units, crops, note) {site}")
    local.commit()
    facts(root, remote, monkeypatch, "--send")
    assert len(set(names)) == 1 and loading and set(loading) == {1}
    assert db.execute("SELECT loading FROM form_state").fetchone() == (0,)
    assert db.execute("SELECT decision FROM form_clusters").fetchone() == ("d1",)


def test_an_oversized_statement_stops_the_load_before_any_number_is_kept(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    clustering(root, monkeypatch)
    mapped_tile(root)
    monkeypatch.setattr(importlib.import_module("publish"), "STATEMENT_BYTES", 100)
    with pytest.raises(SystemExit, match="over D1's limit"):
        facts(root, remote, monkeypatch, "--send")
    mirror = sqlite3.connect(root / "out" / "mirror.sqlite")
    assert not mirror.execute("SELECT 1 FROM codes WHERE field='form_cluster'").fetchone()
    assert not mirror.execute("SELECT 1 FROM pending").fetchone()


def test_votes_load_at_the_cutover_only_for_the_crops_current_image(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    known = ids(root)
    a1 = known["ex:a1"]
    clustering(root, monkeypatch)
    mapped_tile(root)
    unit_facts(root, [("ex:a1", "local", visual(), CONTEXT, SHA, None, None), ("ex:a2", "local", visual(), CONTEXT, "ef" * 32, None, None),
                      ("ex:gone", "local", visual(), CONTEXT, SHA, None, None), ("codh:g1", "corpus", NONE, NO_CONTEXT, None, None, None)])
    hash_images(root, {a1: SHA, known["ex:a2"]: SHA})
    # Without --votes nothing reads the copy.
    facts(root, remote, monkeypatch, "--send")
    assert not (root / "out" / "votes").exists()
    facts(root, remote, monkeypatch, "--votes", "--send")
    mirror, db = sqlite3.connect(root / "out" / "mirror.sqlite"), remote.dbs["glyph-atlas-v3"]
    code = mirror.execute("SELECT code FROM codes WHERE field='models'").fetchone()[0]
    assert json.loads(db.execute("SELECT key FROM codes WHERE field='models' AND code=?", (code,)).fetchone()[0]) == ENGINES
    page = votes.read(root / "out" / "votes", a1 // PAGE)
    assert list(page) == [a1 % PAGE] and page[a1 % PAGE]["cut"] == 0 and page[a1 % PAGE]["models"] == code
    assert page[a1 % PAGE]["loaded"] is True
    assert votes.restore(ENGINES, page[a1 % PAGE])["context"] == json.loads(CONTEXT)
    assert f"{a1 // PAGE}.json.gz" in remote.uploaded and f"v3/votes/{build_of(root)}/" in remote.prefixes
    report = (root / "out" / "crop-facts-report.txt").read_text()
    assert "dropped (1) crop the mirror lacks" in report and "dropped (1) read on another image" in report


def test_a_rerun_after_a_fresh_copy_refreshes_the_loaded_readings_and_keeps_a_publishs(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    known = ids(root)
    a1, a2 = known["ex:a1"], known["ex:a2"]
    clustering(root, monkeypatch)
    mapped_tile(root)
    hash_images(root, {a1: SHA, a2: SHA})
    unit_facts(root, [("ex:a1", "local", visual(), CONTEXT, SHA, None, None), ("ex:a2", "local", visual(), CONTEXT, SHA, None, None)])
    facts(root, remote, monkeypatch, "--votes", "--send")
    # A publish writes a2's entry for its cut.
    slots = votes.read(root / "out" / "votes", a2 // PAGE)
    slots[a2 % PAGE] = votes.entry(0, None, None, votes.context(CONTEXT))
    votes.write(root / "out" / "votes", a2 // PAGE, slots)
    unit_facts(root, [("ex:a1", "local", visual("王"), CONTEXT, SHA, None, None), ("ex:a2", "local", visual("王"), CONTEXT, SHA, None, None)])
    facts(root, remote, monkeypatch, "--votes", "--send")
    slots = votes.read(root / "out" / "votes", a1 // PAGE)
    assert slots[a1 % PAGE]["visual"]["votes"][0]["text"] == "王" and slots[a1 % PAGE]["loaded"]
    assert "visual" not in slots[a2 % PAGE] and "loaded" not in slots[a2 % PAGE]
    # A reading the fresh copy no longer has goes.
    unit_facts(root, [("ex:a2", "local", visual("王"), CONTEXT, SHA, None, None)])
    facts(root, remote, monkeypatch, "--votes", "--send")
    assert list(votes.read(root / "out" / "votes", a1 // PAGE)) == [a2 % PAGE]


def test_votes_wait_for_their_model_codes_and_reach_out_votes_as_they_go_up(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    a1 = ids(root)["ex:a1"]
    clustering(root, monkeypatch)
    mapped_tile(root)
    hash_images(root, {a1: SHA})
    unit_facts(root, [("ex:a1", "local", visual(), CONTEXT, SHA, None, None)])
    db = remote.dbs["glyph-atlas-v3"]
    uploaded = remote.upload

    def checked(files, prefix="v3/pages/"):
        if prefix.startswith("v3/votes/"):
            assert db.execute("SELECT 1 FROM codes WHERE field='models'").fetchone()
        uploaded(files, prefix)

    monkeypatch.setattr(remote, "upload", checked)
    sent = remote.send

    def stopped(database, file, stamp):
        # The load stops once its rows are in, before the end.
        if stamp.endswith(".end"):
            raise SystemExit("stopped")
        return sent(database, file, stamp)

    monkeypatch.setattr(remote, "send", stopped)
    with pytest.raises(SystemExit, match="stopped"):
        facts(root, remote, monkeypatch, "--votes", "--send")
    assert not (root / "out" / "votes" / f"{a1 // PAGE}.json.gz").exists()
    # A publish waits for the unfinished load.
    with pytest.raises(SystemExit, match="crop facts load .* is unfinished"):
        run(publish, monkeypatch, voted(root / "c.sqlite", []))
    monkeypatch.setattr(remote, "send", sent)
    facts(root, remote, monkeypatch, "--votes", "--send")
    assert (root / "out" / "votes" / f"{a1 // PAGE}.json.gz").exists()
    assert db.execute("SELECT loading FROM form_state").fetchone() == (0,)
    assert not sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT 1 FROM pending").fetchone()


def voted(path, units):
    db = sqlite3.connect(path)
    db.execute(f"CREATE TABLE units({','.join(UNITS)},visual,context)")
    db.executemany(f"INSERT INTO units VALUES({','.join('?' * (len(UNITS) + 2))})", units)
    db.commit()
    return path


def test_a_publish_keeps_each_crops_votes_for_its_cut(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    a1, a2 = ids(root)["ex:a1"], ids(root)["ex:a2"]
    page = a1 // PAGE

    def held():
        return votes.read(root / "out" / "votes", page)

    box1, box2 = {"x": 0, "y": 0, "w": 10, "h": 10}, {"x": 0, "y": 20, "w": 10, "h": 10}
    run(publish, monkeypatch, voted(tmp_path / "c1.sqlite", [(*unit("ex:a1", "hl:p1", box1), visual(), CONTEXT),
                                                             (*unit("ex:a2", "hl:p1", box2, "い", "checked", 3), visual("二"), NO_CONTEXT)]))
    assert {s: ("context" in e, e["cut"]) for s, e in held().items()} == {a1 % PAGE: (True, 0), a2 % PAGE: (False, 0)}
    assert remote.prefixes[-1] == f"v3/votes/{build_of(root)}/" and remote.uploaded[-1] == f"{page}.json.gz"
    # The same readings change nothing.
    uploads = len(remote.uploaded)
    run(publish, monkeypatch, voted(tmp_path / "c2.sqlite", [(*unit("ex:a1", "hl:p1", box1), visual(), CONTEXT)]))
    assert len(remote.uploaded) == uploads
    # A re-cut takes the new image's readings for its new cut; no reading removes the crop's entry.
    run(publish, monkeypatch, voted(tmp_path / "c3.sqlite", [(*unit("ex:a1", "hl:p1", {"x": 1, "y": 1, "w": 9, "h": 9}), visual("王"), CONTEXT),
                                                             (*unit("ex:a2", "hl:p1", box2, "い", "checked", 3), NONE, NO_CONTEXT)]))
    entry = held()[a1 % PAGE]
    assert entry["cut"] == crops(remote)[a1]["cut"] == 1 and entry["visual"]["votes"][0]["text"] == "王"
    assert a2 % PAGE not in held()
    # A retired crop's entry goes with it.
    gone = unit("ex:a1", "hl:p1", {"x": 1, "y": 1, "w": 9, "h": 9})
    run(publish, monkeypatch, voted(tmp_path / "c4.sqlite", [((gone[0], "retired", *gone[2:]) + (NONE, NO_CONTEXT))]))
    assert held() == {}


def test_a_publish_sends_votes_after_its_model_codes_and_keeps_them_by_build(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    a1 = ids(root)["ex:a1"]
    db = remote.dbs["glyph-atlas-v3"]
    uploaded = remote.upload

    def checked(files, prefix="v3/pages/"):
        if prefix.startswith("v3/votes/"):
            assert db.execute("SELECT 1 FROM codes WHERE field='models'").fetchone()
        uploaded(files, prefix)

    marked = publish.mark

    def stop(run, line):
        marked(run, line)
        if line == "votes uploaded":
            raise SystemExit("stopped after the votes went up")

    monkeypatch.setattr(publish, "upload", checked)
    monkeypatch.setattr(publish, "mark", stop)
    with pytest.raises(SystemExit, match="stopped"):
        run(publish, monkeypatch, voted(tmp_path / "c1.sqlite", [(*unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}),
                                                                   visual(), CONTEXT)]))
    # The local copy holds what went up even though the publish stopped right after.
    assert a1 % PAGE in votes.read(root / "out" / "votes", a1 // PAGE)
    # A rebuilt layout numbers crops afresh and starts its own folder of vote objects.
    old = build_of(root)
    monkeypatch.setattr(sys, "argv", ["build.py", "--replace"])
    monkeypatch.setattr(importlib.import_module("build").time, "strftime", lambda _f: "29991231T000000")
    importlib.import_module("build").main()
    assert build_of(root) != old and not (root / "out" / "votes").exists()
    assert sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT key FROM codes WHERE field='build'").fetchone() == (
        "29991231T000000",)
    assert votes.prefix(build_of(root)) == "v3/votes/29991231T000000/"
