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
    remote.removed = getattr(remote, "removed", [])
    monkeypatch.setattr(module, "remove", lambda names, prefix: remote.removed.extend(prefix + n for n in names))
    monkeypatch.setattr(sys, "argv", ["crop_facts.py", *flags])
    try:
        module.main()
    finally:
        remote.dbs["glyph-atlas-v3"].row_factory = None
    return module


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


def unit_facts(root, rows):
    (root / "source-unit_facts.sqlite").unlink(missing_ok=True)
    write_source(root, "unit_facts", ["id", "origin", "visual", "context", "image_sha256", "tone", "image_size"], rows)


def sources(root, decided="か", units=None, decision="d1"):
    for name in ("form_families", "form_clusters", "form_units"):
        (root / f"source-{name}.sqlite").unlink(missing_ok=True)
    unit_facts(root, [
        ("ex:a1", "local", visual(), CONTEXT, SHA, "#cbc0b0", "[83,133]"), ("ex:a2", "local", visual(), CONTEXT, "ef" * 32, None, None),
        ("ex:b1", "local", visual(), CONTEXT, SHA, None, None), ("ex:gone", "local", visual(), CONTEXT, SHA, None, None),
        ("codh:g1", "corpus", NONE, NO_CONTEXT, None, None, None)])
    write_source(root, "form_families", ["code_point", "char", "label", "count", "cluster_count", "forms", "assigned", "rejected", "revision"],
                 [("U+304B", "か", "か", 4, 2, "[]", 4, 0, "r1")])
    write_source(root, "form_clusters", ["id", "family", "label", "count", "coherence", "shape_position", "size_position",
                                         "form", "decision", "issue"],
                 [("U+304B:c1", "U+304B", "Cluster 1", 3, 0.9, 0, 0, decided, decision, None),
                  ("U+304B:c2", "U+304B", "Cluster 2", 1, 0.8, 1, 1, None, None, None)])
    write_source(root, "form_units", FORM_UNIT, units or [
        form_unit("codh:g1", "U+304B:c1", 0, cluster_form=decided), form_unit("ex:a1", "U+304B:c1", 1, cluster_form=decided, own="𛀙"),
        form_unit("ex:b1", "U+304B:c1", 2, cluster_form=decided), form_unit("codh:gone", "U+304B:c2", 0),
        form_unit("ex:a2", "U+304B:old", 0, clustered=0, own="か", image=None)])


def decisions(root, remote, *rows):
    """Cluster decisions in the loaded history, on D1 and the local copy: (seq, id, at, cluster)."""
    for db in (sqlite3.connect(root / "out" / "atlas.sqlite"), remote.dbs["glyph-atlas-v3"]):
        db.executemany("INSERT INTO form_decisions(seq, id, at, actor, kind, family, cluster, revision, units, crops, note) "
                       "VALUES(?, ?, ?, 'a', 'cluster', 'U+304B', ?, 'r1', '[]', '[]', '')", rows)
        db.commit()


def glyph_decided(root, remote, crops=("ex:a1", "ex:a2"), id="g1", seq=0, at="2025-01-01"):
    """A glyph decision in the loaded history, on D1 and the local copy, over the crops the sources decide."""
    known = ids(root)
    for db in (sqlite3.connect(root / "out" / "atlas.sqlite"), remote.dbs["glyph-atlas-v3"]):
        db.execute("INSERT INTO form_decisions(seq, id, at, actor, kind, family, revision, units, crops, note) "
                   "VALUES(?, ?, ?, 'a', 'glyph', 'U+304B', 'r1', ?, ?, '')",
                   (seq, id, at, json.dumps(list(crops)), json.dumps([known[c] for c in crops])))
        db.commit()


def retire(root, remote, crop):
    for db in (sqlite3.connect(root / "out" / "atlas.sqlite"), remote.dbs["glyph-atlas-v3"]):
        db.execute("DELETE FROM crops WHERE id=?", (crop,))
        db.commit()


def drop_0006(root, remote):
    """A layout loaded before migration 0006 (and before builds named themselves in `codes`)."""
    for db in (sqlite3.connect(root / "out" / "atlas.sqlite"), remote.dbs["glyph-atlas-v3"]):
        db.executescript("""
          DROP TRIGGER form_decision_while_loading; DROP TABLE form_state; DROP INDEX form_decision_list;
          ALTER TABLE form_decisions DROP COLUMN list; DROP TABLE form_glyphs; DROP TABLE form_clusters;
          DROP TABLE form_families; DELETE FROM codes WHERE field='build';""")
        db.commit()


def body(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def build_of(root):
    return sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT value FROM meta WHERE key='build'").fetchone()[0]


def test_crop_facts_load_votes_to_r2_and_the_forms_by_integer_crop_id(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    glyph_decided(root, remote)
    known = ids(root)
    sources(root)
    hash_images(root, {known["ex:a1"]: SHA, known["ex:a2"]: SHA, known["ex:b1"]: SHA})
    retire(root, remote, known["ex:b1"])
    drop_0006(root, remote)
    decisions(root, remote, (1, "d0", "2026-01-01", "U+304B:c1"), (2, "d1", "2026-01-02", "U+304B:c1"))
    db, mirror = remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "mirror.sqlite")

    facts(root, remote, monkeypatch)
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='form_glyphs'").fetchone() and not remote.uploaded
    assert not mirror.execute("SELECT 1 FROM codes WHERE field IN ('models', 'form_cluster')").fetchone()

    facts(root, remote, monkeypatch, "--send")
    assert db.execute("SELECT 1 FROM imports WHERE stamp='migration:0006'").fetchone()
    assert db.execute("SELECT key FROM codes WHERE field='build'").fetchone() == (build_of(root),)
    code = mirror.execute("SELECT code FROM codes WHERE field='models'").fetchone()[0]
    assert json.loads(db.execute("SELECT key FROM codes WHERE field='models' AND code=?", (code,)).fetchone()[0]) == ENGINES
    # Votes: only the reading made on the crop's current image, in its page object under the build.
    a1 = known["ex:a1"]
    page = votes.read(root / "out" / "votes", a1 // PAGE)
    assert list(page) == [a1 % PAGE] and page[a1 % PAGE]["cut"] == 0 and page[a1 % PAGE]["models"] == code
    assert page[a1 % PAGE]["loaded"] is True
    assert votes.restore(ENGINES, page[a1 % PAGE])["context"] == json.loads(CONTEXT)
    assert f"{a1 // PAGE}.json.gz" in remote.uploaded and f"v3/votes/{build_of(root)}/" in remote.prefixes
    report = (root / "out" / "crop-facts-report.txt").read_text()
    assert "dropped (1) crop the mirror lacks" in report and "dropped (1) read on another image" in report
    # Forms: the cluster's number is the mirror's; its list, named by its content, holds its glyphs by rank
    # with their tone and size; the counts leave out the retired glyph and the glyph the mirror lacks,
    # whose cluster is left with none.
    c1 = mirror.execute("SELECT code FROM codes WHERE field='form_cluster' AND key='U+304B:c1'").fetchone()[0]
    assert db.execute("SELECT id, key, count, form, decision FROM form_clusters").fetchall() == [(c1, "U+304B:c1", 2, "か", "d1")]
    name = db.execute("SELECT list FROM form_clusters").fetchone()[0]
    assert name.startswith(f"{c1}-")
    count, clusters, assigned, index = db.execute("SELECT count, cluster_count, assigned, list FROM form_families").fetchone()
    assert (count, clusters, assigned) == (2, 1, 2) and index.startswith("U+304B-")
    assert sorted(db.execute("SELECT crop, cluster, form, decision FROM form_glyphs").fetchall()) == sorted([
        (a1, c1, "𛀙", "g1"), (known["ex:a2"], None, "か", "g1")])
    members = [[known["codh:g1"], 0, 0.9, "ab" * 32, "0101010", None, None], [a1, 1, 0.9, "ab" * 32, "0101010", "#cbc0b0", [83, 133]]]
    assert body(root / "out" / "forms" / f"{name}.json.gz") == {"family": "U+304B", "key": "U+304B:c1", "members": members}
    assert body(root / "out" / "forms" / "families" / f"{index}.json.gz") == {"family": "U+304B", "clusters": {str(c1): {
        "list": name, "count": 2, "head": [[m[0], m[1], m[3], m[5], m[6]] for m in members], "tail": []}}}
    assert "v3/forms/" in remote.prefixes and "v3/forms/families/" in remote.prefixes
    assert "dropped (1) crop no longer live (retired)" in report and "dropped (1) no glyph left" in report
    assert "0 glyphs whose form, issue, character or family differs" in report
    # A crop's cluster is in crops.
    assert dict(db.execute("SELECT id, cluster FROM crops WHERE cluster IS NOT NULL")) == {known["codh:g1"]: c1, a1: c1}
    assert db.execute("SELECT lists LIKE 'facts-%', loading FROM form_state").fetchone() == (1, 0)
    # The local copy replayed the same parts; nothing is pending; a second run sends nothing.
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    for table in ("form_glyphs", "form_clusters", "form_families", "crops"):
        assert local.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    assert not mirror.execute("SELECT 1 FROM pending").fetchone()
    stamps, uploads = db.execute("SELECT count(*) FROM imports").fetchone(), len(remote.uploaded)
    facts(root, remote, monkeypatch, "--send")
    assert db.execute("SELECT count(*) FROM imports").fetchone() == stamps and len(remote.uploaded) == uploads


def test_a_rerun_after_a_fresh_copy_sends_the_difference(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    glyph_decided(root, remote)
    known = ids(root)
    sources(root)
    decisions(root, remote, (1, "d1", "2026-01-01", "U+304B:c1"), (2, "d2", "2026-01-02", "U+304B:c1"))
    sources(root, decision="d2")
    facts(root, remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    c1, before = db.execute("SELECT id, list FROM form_clusters WHERE key='U+304B:c1'").fetchone()
    family = db.execute("SELECT list FROM form_families").fetchone()[0]
    uploads = len(remote.uploaded)
    # The fresh copy has another cluster decision, a1 follows its cluster again, codh:g1 has left, and a2,
    # outside the clustering, no longer has a decision of its own: it keeps no row.
    sources(root, decided="𛀙", decision="d2", units=[form_unit("ex:a1", "U+304B:c1", 1, cluster_form="𛀙"),
                                                      form_unit("ex:a2", "U+304B:old", 0, clustered=0, cluster_form="か", image=None)])
    facts(root, remote, monkeypatch, "--send")
    assert db.execute("SELECT id, form FROM form_clusters").fetchall() == [(c1, "𛀙")]
    assert db.execute("SELECT count(*) FROM form_glyphs").fetchone() == (0,)
    assert "1 glyphs outside the clustering with no decision of their own, 1 of them with a form" in (
        root / "out" / "crop-facts-report.txt").read_text()
    assert db.execute("SELECT count, assigned, list <> ? FROM form_families", (family,)).fetchone() == (1, 1, 1)
    assert dict(db.execute("SELECT id, cluster FROM crops WHERE cluster IS NOT NULL")) == {known["ex:a1"]: c1}
    # Only the changed list and its family's index went up.
    after = db.execute("SELECT list FROM form_clusters").fetchone()[0]
    assert after != before and sorted(remote.uploaded[uploads:]) == sorted(
        [f"{after}.json.gz", f"{db.execute('SELECT list FROM form_families').fetchone()[0]}.json.gz", "manifest.json.gz"])
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert local.execute("SELECT id, form FROM form_clusters").fetchall() == [(c1, "𛀙")]


def test_a_list_no_row_or_decision_names_is_deleted_after_the_cache_time(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    glyph_decided(root, remote)
    sources(root, decision=None)
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
    sources(root, decision=None, units=[form_unit("ex:a1", "U+304B:c1", 1)])
    facts(root, remote, monkeypatch, "--send")
    manifest = root / "out" / "forms" / "manifest.json.gz"
    held = body(manifest)
    assert held[f"{first}.json.gz"] is None and isinstance(held[f"families/{family}.json.gz"], int)
    assert not remote.removed
    # Two hours on, the next load deletes the family index nothing names; the list a decision names stays.
    manifest.write_bytes(gzip.compress(json.dumps({k: v and v - 7200 for k, v in held.items()}).encode()))
    facts(root, remote, monkeypatch, "--send")
    assert remote.removed == [f"v3/forms/families/{family}.json.gz"]
    assert not (root / "out" / "forms" / "families" / f"{family}.json.gz").exists()
    assert (root / "out" / "forms" / f"{first}.json.gz").exists()


def test_a_load_over_decisions_the_site_made_is_refused(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root, decision=None)
    db = remote.dbs["glyph-atlas-v3"]
    db.execute("INSERT INTO form_decisions(seq, id, at, actor, kind, family, revision, units, crops, note) "
               "VALUES(1, 'd9', '2026-10-21', 'a', 'cluster', 'U+304B', 'r1', '[]', '[]', '')")
    db.commit()
    with pytest.raises(SystemExit, match="form decisions"):
        facts(root, remote, monkeypatch, "--send")


def test_a_decision_made_while_the_rows_are_sent_stops_the_load_and_opens_decisions_again(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    glyph_decided(root, remote)
    sources(root, decision=None)
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
    glyph_decided(root, remote)
    sources(root, decision=None)
    facts(root, remote, monkeypatch, "--send")
    db, local = remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")
    # A cluster decision changes rows only, so the next load's run is named the same on each attempt.
    decisions(root, remote, (1, "d1", "2026-01-01", "U+304B:c1"))
    sources(root, decision="d1")
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


def test_a_cluster_decision_not_the_latest_loaded_stops_the_load(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root)
    with pytest.raises(SystemExit, match="not their latest cluster decision"):
        facts(root, remote, monkeypatch)
    decisions(root, remote, (1, "d1", "2026-01-01", "U+304B:c1"), (2, "d2", "2026-01-02", "U+304B:c1"))
    with pytest.raises(SystemExit, match=r"U\+304B:c1: d1 \(latest d2\)"):
        facts(root, remote, monkeypatch)


def test_a_glyph_decision_not_the_latest_loaded_stops_the_load(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root, decision=None)
    with pytest.raises(SystemExit, match="not their latest glyph decision"):
        facts(root, remote, monkeypatch)
    # A later glyph decision over a1 leaves the copy's g1 behind; a2's latest is still g1.
    glyph_decided(root, remote)
    glyph_decided(root, remote, crops=("ex:a1",), id="g2", seq=1, at="2025-02-01")
    with pytest.raises(SystemExit, match=r"1 glyphs .*'ex:a1: g1 \(latest g2\)"):
        facts(root, remote, monkeypatch)


def test_an_oversized_statement_stops_the_load_before_any_number_is_kept(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    glyph_decided(root, remote)
    sources(root, decision=None)
    monkeypatch.setattr(importlib.import_module("publish"), "STATEMENT_BYTES", 100)
    with pytest.raises(SystemExit, match="over D1's limit"):
        facts(root, remote, monkeypatch, "--send")
    mirror = sqlite3.connect(root / "out" / "mirror.sqlite")
    assert not mirror.execute("SELECT 1 FROM codes WHERE field='form_cluster'").fetchone()
    assert not mirror.execute("SELECT 1 FROM pending").fetchone()


def test_a_rerun_after_a_fresh_copy_refreshes_the_loaded_readings_and_keeps_a_publishs(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    glyph_decided(root, remote)
    known = ids(root)
    a1, a2 = known["ex:a1"], known["ex:a2"]
    sources(root, decision=None)
    hash_images(root, {a1: SHA, a2: SHA})
    unit_facts(root, [("ex:a1", "local", visual(), CONTEXT, SHA, None, None), ("ex:a2", "local", visual(), CONTEXT, SHA, None, None)])
    facts(root, remote, monkeypatch, "--send")
    # A publish writes a2's entry for its cut.
    slots = votes.read(root / "out" / "votes", a2 // PAGE)
    slots[a2 % PAGE] = votes.entry(0, None, None, votes.context(CONTEXT))
    votes.write(root / "out" / "votes", a2 // PAGE, slots)
    unit_facts(root, [("ex:a1", "local", visual("王"), CONTEXT, SHA, None, None), ("ex:a2", "local", visual("王"), CONTEXT, SHA, None, None)])
    facts(root, remote, monkeypatch, "--send")
    slots = votes.read(root / "out" / "votes", a1 // PAGE)
    assert slots[a1 % PAGE]["visual"]["votes"][0]["text"] == "王" and slots[a1 % PAGE]["loaded"]
    assert "visual" not in slots[a2 % PAGE] and "loaded" not in slots[a2 % PAGE]
    # A reading the fresh copy no longer has goes.
    unit_facts(root, [("ex:a2", "local", visual("王"), CONTEXT, SHA, None, None)])
    facts(root, remote, monkeypatch, "--send")
    assert list(votes.read(root / "out" / "votes", a1 // PAGE)) == [a2 % PAGE]


def test_votes_wait_for_their_model_codes_and_reach_out_votes_as_they_go_up(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    glyph_decided(root, remote)
    a1 = ids(root)["ex:a1"]
    sources(root, decision=None)
    hash_images(root, {a1: SHA})
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
        facts(root, remote, monkeypatch, "--send")
    assert not (root / "out" / "votes" / f"{a1 // PAGE}.json.gz").exists()
    # A publish waits for the unfinished load.
    with pytest.raises(SystemExit, match="crop facts load .* is unfinished"):
        run(publish, monkeypatch, voted(root / "c.sqlite", []))
    monkeypatch.setattr(remote, "send", sent)
    facts(root, remote, monkeypatch, "--send")
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
