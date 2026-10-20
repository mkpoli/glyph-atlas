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
    for name in ("crop_facts", "load_history", "history"):
        sys.modules.pop(name, None)
    module = importlib.import_module("crop_facts")
    loader = importlib.import_module("load_history")

    def applied(database, stamp):
        return bool(remote.query(database, f"SELECT 1 AS n FROM imports WHERE stamp = '{stamp}'"))

    for m in (module, loader):
        monkeypatch.setattr(m, "send", remote.send)
        monkeypatch.setattr(m, "applied", applied)
    monkeypatch.setattr(module, "query", remote.query)
    monkeypatch.setattr(module, "upload", remote.upload)
    monkeypatch.setattr(sys, "argv", ["crop_facts.py", *flags])
    module.main()
    remote.dbs["glyph-atlas-v3"].row_factory = None


FORM_UNIT = ["id", "family", "cluster", "rank", "similarity", "image", "split", "clustered", "cluster_form", "cluster_issue",
             "cluster_character", "cluster_family", "glyph_set", "glyph_form", "glyph_decision", "glyph_issue", "glyph_character",
             "glyph_family", "form", "issue", "issue_character", "written_family"]
TILE = "/atlas/media/" + "ab" * 32 + ".webp"
SHA = "cd" * 32


def form_unit(id, cluster, rank, clustered=1, cluster_form=None, own=None, image=TILE):
    """A glyph of か's family; `own` is its own decision's form."""
    form = own if own is not None else cluster_form
    return (id, "U+304B", cluster, rank, 0.9, image, "0101010", clustered, cluster_form, None, None, None,
            int(own is not None), own, "g1" if own is not None else None, None, None, None, form, None, None, None)


def hash_images(root, hashes):
    """Give crops' bundle entries the image hash of their cut."""
    for path in (root / "out" / "pages").glob("*.json.gz"):
        body = json.loads(gzip.decompress(path.read_bytes()))
        page = int(path.name.split("-")[0])
        for e in body["crops"]:
            if page * PAGE + e["slot"] in hashes:
                e["image_sha256"] = hashes[page * PAGE + e["slot"]]
        path.write_bytes(gzip.compress(json.dumps(body, ensure_ascii=False).encode()))


def sources(root, decided="か", units=None):
    for name in ("unit_votes", "form_families", "form_clusters", "form_units"):
        (root / f"source-{name}.sqlite").unlink(missing_ok=True)
    write_source(root, "unit_votes", ["id", "origin", "visual", "context", "image_sha256"], [
        ("ex:a1", "local", visual(), CONTEXT, SHA), ("ex:a2", "local", visual(), CONTEXT, "ef" * 32),
        ("ex:b1", "local", visual(), CONTEXT, SHA), ("ex:gone", "local", visual(), CONTEXT, SHA),
        ("codh:g1", "corpus", NONE, NO_CONTEXT, None)])
    write_source(root, "form_families", ["code_point", "char", "label", "count", "cluster_count", "forms", "assigned", "rejected", "revision"],
                 [("U+304B", "か", "か", 4, 2, "[]", 4, 0, "r1")])
    write_source(root, "form_clusters", ["id", "family", "label", "count", "coherence", "shape_position", "size_position",
                                         "form", "decision", "issue"],
                 [("U+304B:c1", "U+304B", "Cluster 1", 3, 0.9, 0, 0, decided, "d1", None),
                  ("U+304B:c2", "U+304B", "Cluster 2", 1, 0.8, 1, 1, None, None, None)])
    write_source(root, "form_units", FORM_UNIT, units or [
        form_unit("codh:g1", "U+304B:c1", 0, cluster_form=decided), form_unit("ex:a1", "U+304B:c1", 1, cluster_form=decided, own="𛀙"),
        form_unit("ex:b1", "U+304B:c1", 2, cluster_form=decided), form_unit("codh:gone", "U+304B:c2", 0),
        form_unit("ex:a2", "U+304B:old", 0, clustered=0, own="か", image=None)])


def retire(root, remote, crop):
    for db in (sqlite3.connect(root / "out" / "atlas.sqlite"), remote.dbs["glyph-atlas-v3"]):
        db.execute("DELETE FROM crops WHERE id=?", (crop,))
        db.commit()


def drop_0006(root, remote):
    """A layout loaded before migration 0006."""
    for db in (sqlite3.connect(root / "out" / "atlas.sqlite"), remote.dbs["glyph-atlas-v3"]):
        for table in ("form_glyphs", "form_clusters", "form_families"):
            db.execute(f"DROP TABLE {table}")
        db.commit()


def test_crop_facts_load_votes_to_r2_and_the_forms_by_integer_crop_id(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    known = ids(root)
    sources(root)
    hash_images(root, {known["ex:a1"]: SHA, known["ex:a2"]: SHA, known["ex:b1"]: SHA})
    retire(root, remote, known["ex:b1"])
    drop_0006(root, remote)
    db, mirror = remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "mirror.sqlite")

    facts(root, remote, monkeypatch)
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='form_glyphs'").fetchone() and not remote.uploaded
    assert not mirror.execute("SELECT 1 FROM codes WHERE field IN ('models', 'form_cluster')").fetchone()

    facts(root, remote, monkeypatch, "--send")
    assert db.execute("SELECT 1 FROM imports WHERE stamp='migration:0006'").fetchone()
    code = mirror.execute("SELECT code FROM codes WHERE field='models'").fetchone()[0]
    assert json.loads(db.execute("SELECT key FROM codes WHERE field='models' AND code=?", (code,)).fetchone()[0]) == ENGINES
    # Votes: only the reading made on the crop's current image, in its page object.
    a1 = known["ex:a1"]
    page = votes.read(root / "out" / "votes", a1 // PAGE)
    assert list(page) == [a1 % PAGE] and page[a1 % PAGE]["cut"] == 0 and page[a1 % PAGE]["models"] == code
    assert votes.restore(ENGINES, page[a1 % PAGE])["context"] == json.loads(CONTEXT)
    assert f"{a1 // PAGE}.json.gz" in remote.uploaded and "v3/votes/" in remote.prefixes
    report = (root / "out" / "crop-facts-report.txt").read_text()
    assert "dropped (1) crop the mirror lacks" in report and "dropped (1) read on another image" in report
    # Forms: the cluster's number is the mirror's; the list holds its glyphs by rank; the counts leave out
    # the retired glyph and the glyph the mirror lacks, whose cluster is left with none.
    c1 = mirror.execute("SELECT code FROM codes WHERE field='form_cluster' AND key='U+304B:c1'").fetchone()[0]
    assert db.execute("SELECT id, key, count, form, decision FROM form_clusters").fetchall() == [(c1, "U+304B:c1", 2, "か", "d1")]
    count, clusters, assigned, folder = db.execute("SELECT count, cluster_count, assigned, lists FROM form_families").fetchone()
    assert (count, clusters, assigned) == (2, 1, 2) and folder.startswith("r1-")
    assert sorted(db.execute("SELECT crop, cluster, own, form, decision FROM form_glyphs").fetchall()) == sorted([
        (a1, c1, 1, "𛀙", "g1"), (known["ex:a2"], None, 1, "か", "g1")])
    listed = json.loads(gzip.decompress((root / "out" / "forms" / folder / f"{c1}.json.gz").read_bytes()))
    assert listed == {"family": "U+304B", "key": "U+304B:c1",
                      "members": [[known["codh:g1"], 0, 0.9, "ab" * 32, "0101010"], [a1, 1, 0.9, "ab" * 32, "0101010"]]}
    assert f"v3/forms/{folder}/" in remote.prefixes
    assert "dropped (1) crop no longer live (retired)" in report and "dropped (1) no glyph left" in report
    assert "0 glyphs whose form, issue, character or family differs" in report
    # The local copy replayed the same parts; a second run sends nothing.
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert local.execute("SELECT * FROM form_glyphs ORDER BY crop").fetchall() == db.execute("SELECT * FROM form_glyphs ORDER BY crop").fetchall()
    stamps, uploads = db.execute("SELECT count(*) FROM imports").fetchone(), len(remote.uploaded)
    facts(root, remote, monkeypatch, "--send")
    assert db.execute("SELECT count(*) FROM imports").fetchone() == stamps and len(remote.uploaded) == uploads


def test_a_rerun_after_a_fresh_copy_sends_the_difference(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    known = ids(root)
    sources(root)
    facts(root, remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    c1 = db.execute("SELECT id FROM form_clusters WHERE key='U+304B:c1'").fetchone()[0]
    folder = db.execute("SELECT lists FROM form_families").fetchone()[0]
    # The fresh copy has another cluster decision, and a1 follows its cluster again.
    sources(root, decided="𛀙", units=[form_unit("codh:g1", "U+304B:c1", 0, cluster_form="𛀙"),
                                        form_unit("ex:a1", "U+304B:c1", 1, cluster_form="𛀙")])
    facts(root, remote, monkeypatch, "--send")
    assert db.execute("SELECT id, form FROM form_clusters").fetchall() == [(c1, "𛀙")]
    assert db.execute("SELECT count(*) FROM form_glyphs").fetchone() == (0,)
    assert db.execute("SELECT count, assigned, lists <> ? FROM form_families", (folder,)).fetchone() == (2, 2, 1)
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert local.execute("SELECT id, form FROM form_clusters").fetchall() == [(c1, "𛀙")]
    assert known["ex:a1"] in {m[0] for m in json.loads(gzip.decompress(
        next((root / "out" / "forms").glob(f"r1-*/{c1}.json.gz")).read_bytes()))["members"]}


def test_a_load_over_decisions_the_site_made_is_refused(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root)
    db = remote.dbs["glyph-atlas-v3"]
    db.execute("INSERT INTO form_decisions(seq, id, at, actor, kind, family, revision, units, crops, note) "
               "VALUES(1, 'd9', '2026-10-21', 'a', 'cluster', 'U+304B', 'r1', '[]', '[]', '')")
    db.commit()
    with pytest.raises(SystemExit, match="form decisions"):
        facts(root, remote, monkeypatch, "--send")


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
    assert remote.prefixes[-1] == "v3/votes/" and remote.uploaded[-1] == f"{page}.json.gz"
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
