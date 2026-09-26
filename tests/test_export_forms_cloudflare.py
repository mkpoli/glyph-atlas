"""The hosted forms publication loads into the migrated D1 schema, carries its tiles, and keeps decisions made online."""
import importlib
import json
import os
import sqlite3
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from glyph_atlas import forms

A, B, C = (f"codh:book:page:B0001:C000{i}" for i in range(3))
D = "hi:1"


def schema():
    db = sqlite3.connect(":memory:")
    for migration in sorted(Path("apps/cloudflare/migrations").glob("*.sql")):
        db.executescript(migration.read_text())
    return db


def load(db, out: Path):
    publication = json.loads((out / "publication.json").read_text())
    for sql in publication["sql"]:
        text = (out / sql).read_text()
        assert max(len(line.encode()) for line in text.splitlines()) < 100_000, "D1 refuses a statement over 100 KB"
        db.executescript(text)
    return publication


def publish(tmp_path, monkeypatch, name):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    module = importlib.import_module("export_forms_cloudflare")
    # Two glyphs per statement, so the three-glyph cluster decision is written in parts.
    monkeypatch.setattr(module, "UNITS_PER_STATEMENT", 2)
    return module.export(tmp_path / "corpus", tmp_path / name, workers=2)


def clustering(tmp_path, monkeypatch, form_corpora):
    """One cluster of は: A and B boxed on a held page, C on a page that is not held, D an HI Lab crop."""
    directory = Path(os.environ["ATLAS_FORM_CLUSTERS"])
    directory.mkdir(parents=True)
    members = [A, B, C, D]
    clusters = [{"id": "U+306F:one", "label": "Cluster 1", "count": 4, "coherence": 0.9, "representatives": members}]
    (directory / "clusters.json").write_text(json.dumps({"revision": "r1", "families": {"U+306F": {
        "family": "U+306F", "char": "は", "label": "は", "members": [], "count": 4, "clusters": clusters}}}))
    pq.write_table(pa.table({"id": members, "family": ["U+306F"] * 4, "cluster": ["U+306F:one"] * 4,
                             "similarity": [0.95, 0.9, 0.8, 0.7], "rank": [0, 1, 2, 3]}), directory / "units.parquet")
    np.save(directory / "embeddings.npy", np.array([[1, 0], [0.9, 0.1], [0, 1], [0.1, 0.9]], np.float16))


def test_the_publication_packs_tiles_and_resolves_decisions(tmp_path, monkeypatch, form_corpora):
    clustering(tmp_path, monkeypatch, form_corpora)
    forms.record("cluster", cluster="U+306F:one", form="𛂥")
    forms.record("glyph", units=[C], issue="character", character="テ")
    summary = publish(tmp_path, monkeypatch, "out")
    assert (summary["units"], summary["clusters"], summary["families"], summary["decisions"]) == (4, 1, 1, 2)
    assert (summary["tiles"], summary["tile_unheld"], summary["objects"]) == (3, 1, 1)
    db = schema()
    db.execute("INSERT INTO corpus_units(id,character,family,visual_group,shuffle,object,offset,size) VALUES(?,?,?,?,?,?,?,?)",
               (A, "は", "U+306F", None, 1, "x", 0, 1))
    publication = load(db, tmp_path / "out")
    rows = {r[0]: r[1:] for r in db.execute("SELECT id,cluster_form,glyph_set,glyph_form,form,length(split) FROM form_units")}
    assert rows[A] == ("𛂥", 0, None, "𛂥", 7) and rows[C] == ("𛂥", 1, None, None, 7)
    assert db.execute("SELECT form,assigned,rejected FROM form_clusters JOIN form_families ON family=code_point").fetchone() == ("𛂥", 3, 1)
    assert db.execute("SELECT glyph_issue,glyph_character,glyph_family FROM form_units WHERE id=?", (C,)).fetchone() == (
        "character", "テ", "U+3066")
    # Each tile the rows name is in a pack the publication uploads.
    images = {r[0]: r[1] for r in db.execute("SELECT id,image FROM form_units")}
    assert images[C] is None
    objects = {o["key"]: tmp_path / "out" / o["file"] for o in publication["objects"]}
    for identity in (A, B, D):
        key = images[identity].removeprefix("/atlas/media/").removesuffix(".webp")
        pack, offset, size = db.execute("SELECT object,offset,size FROM media WHERE key=?", (key,)).fetchone()
        assert objects[pack].read_bytes()[offset:offset + size][:4] == b"RIFF"
    # Search and Quick review's counts follow the form, and the character it had is kept.
    assert db.execute("SELECT character FROM corpus_units WHERE id=?", (A,)).fetchone() == ("𛂥",)
    assert db.execute("SELECT character,n FROM corpus_characters").fetchall() == [("𛂥", 1)]
    assert db.execute("SELECT character FROM form_bases WHERE id=?", (A,)).fetchone() == ("は",)


def test_a_republication_keeps_decisions_made_online(tmp_path, monkeypatch, form_corpora):
    clustering(tmp_path, monkeypatch, form_corpora)
    forms.record("cluster", cluster="U+306F:one", form="𛂥")
    publish(tmp_path, monkeypatch, "first")
    db = schema()
    load(db, tmp_path / "first")
    # Someone on the site gives B its own form, later than the local cluster decision.
    db.execute("INSERT INTO form_decisions(id,at,actor,kind,family,form,cluster,revision,units,note) VALUES("
               "'online','2999-01-01T00:00:00+00:00','reviewer','glyph','U+306F','𛂞',NULL,'r1',?,'')", (json.dumps([B]),))
    publish(tmp_path, monkeypatch, "second")
    load(db, tmp_path / "second")
    assert db.execute("SELECT count(*) FROM form_decisions").fetchone() == (2,)
    assert db.execute("SELECT count(*) FROM form_loading").fetchone() == (0,), "the site takes decisions again"
    assert json.loads(db.execute("SELECT units FROM form_decisions WHERE kind='cluster'").fetchone()[0]) == [A, B, C, D], \
        "a decision written in parts is whole once, and a republication leaves it as it is"
    forms_now = dict(db.execute("SELECT id,form FROM form_units"))
    assert (forms_now[A], forms_now[B], forms_now[C], forms_now[D]) == ("𛂥", "𛂞", "𛂥", "𛂥")


def test_a_corpus_republication_keeps_the_forms(tmp_path, monkeypatch, form_corpora):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    from cloudflare_schema import CORPUS_REFRESH, corpus_upsert

    clustering(tmp_path, monkeypatch, form_corpora)
    forms.record("cluster", cluster="U+306F:one", form="𛂥")
    db = schema()
    db.execute("INSERT INTO corpus_units(id,character,family,visual_group,shuffle,object,offset,size) VALUES(?,?,?,?,?,?,?,?)",
               (A, "は", "U+306F", None, 1, "x", 0, 1))
    publish(tmp_path, monkeypatch, "out")
    load(db, tmp_path / "out")
    # A corpus publication writes the source's character back, then refreshes.
    db.executescript(corpus_upsert((A, "は", "U+306F", None, 1, "y", 0, 1, "unknown")) + "\n" + CORPUS_REFRESH)
    assert db.execute("SELECT character FROM corpus_units WHERE id=?", (A,)).fetchone() == ("𛂥",)
    assert db.execute("SELECT character,n FROM corpus_characters").fetchall() == [("𛂥", 1)]


def test_the_site_refuses_decisions_while_a_clustering_reloads():
    db = schema()
    db.execute("INSERT INTO form_loading VALUES('now')")
    try:
        db.execute("INSERT INTO form_decisions(id,at,actor,kind,family,form,cluster,revision,units,note) "
                   "VALUES('d','t','a','glyph','U+306F',NULL,NULL,'r1','[]','')")
    except sqlite3.IntegrityError as error:
        assert "republished" in str(error)
    else:
        raise AssertionError("a decision was recorded during a reload")


def test_a_republication_keeps_cluster_marks(tmp_path, monkeypatch, form_corpora):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    from cloudflare_schema import CORPUS_REFRESH, corpus_upsert

    clustering(tmp_path, monkeypatch, form_corpora)
    forms.record("cluster", cluster="U+306F:one", form="𛂥")
    forms.record("glyph", units=[B], form="𛂞")
    forms.record("cluster", cluster="U+306F:one", issue="character", character="テ")
    db = schema()
    db.execute("INSERT INTO corpus_units(id,character,family,visual_group,shuffle,object,offset,size) VALUES(?,?,?,?,?,?,?,?)",
               (A, "は", "U+306F", None, 1, "x", 0, 1))
    publish(tmp_path, monkeypatch, "out")
    load(db, tmp_path / "out")
    # The report reaches every glyph that follows the cluster; B keeps its own form.
    rows = {r[0]: r[1:] for r in db.execute("SELECT id,form,issue,issue_character,issue_family FROM form_units")}
    assert rows[A] == (None, "character", "テ", "U+3066") and rows[B] == ("𛂞", None, None, None)
    assert db.execute("SELECT form,issue,rejected FROM form_clusters JOIN form_families ON family=code_point").fetchone() == (
        None, "character", 3)
    db.executescript(corpus_upsert((A, "は", "U+306F", None, 1, "y", 0, 1, "unknown")) + "\n" + CORPUS_REFRESH)
    assert db.execute("SELECT character,family FROM corpus_units WHERE id=?", (A,)).fetchone() == ("テ", "U+3066")
    # Someone on the site then marks it mixed: nothing is named for its glyphs, and A's character comes back.
    db.execute("INSERT INTO form_decisions(id,at,actor,kind,family,form,cluster,revision,units,note,issue) VALUES("
               "'online','2999-01-01T00:00:00+00:00','reviewer','cluster','U+306F',NULL,'U+306F:one','r1',?,'','mixed')",
               (json.dumps([A, B, C, D]),))
    publish(tmp_path, monkeypatch, "second")
    load(db, tmp_path / "second")
    rows = {r[0]: r[1:] for r in db.execute("SELECT id,form,issue FROM form_units")}
    assert rows[A] == (None, None) and rows[B] == ("𛂞", None)
    assert db.execute("SELECT issue,rejected FROM form_clusters JOIN form_families ON family=code_point").fetchone() == ("mixed", 0)
    assert db.execute("SELECT character FROM corpus_units WHERE id=?", (A,)).fetchone() == ("は",)
