"""The hosted forms publication loads into the migrated D1 schema, carries its tiles, and keeps decisions made online."""
import importlib
import json
import os
import sqlite3
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image

from glyph_atlas import forms

A, B, C = (f"codh:book:page:B0001:C000{i}" for i in range(3))
PAGE = "https://example.org/iiif/page.tif"


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
    monkeypatch.setattr(module, "page_file", lambda page: tmp_path / "corpus" / "page.png" if page == PAGE else None)
    return module.export(tmp_path / "corpus", tmp_path / name, workers=2)


def clustering(tmp_path, monkeypatch):
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "cache"))
    directory = Path(os.environ["ATLAS_FORM_CLUSTERS"])
    directory.mkdir(parents=True)
    clusters = [{"id": "U+306F:one", "label": "Cluster 1", "count": 3, "coherence": 0.9, "representatives": [A, B, C]}]
    (directory / "clusters.json").write_text(json.dumps({"revision": "r1", "families": {"U+306F": {
        "family": "U+306F", "char": "は", "label": "は", "members": [], "count": 3, "clusters": clusters}}}))
    pq.write_table(pa.table({"id": [A, B, C], "family": ["U+306F"] * 3, "cluster": ["U+306F:one"] * 3,
                             "similarity": [0.95, 0.9, 0.8], "rank": [0, 1, 2]}), directory / "units.parquet")
    np.save(directory / "embeddings.npy", np.array([[1, 0], [0.9, 0.1], [0, 1]], np.float16))
    # A and B sit on a held page scan; C's page is not held and gets no tile.
    codh = tmp_path / "corpus" / "codh-full"
    codh.mkdir(parents=True)
    Image.new("RGB", (200, 100), "white").save(tmp_path / "corpus" / "page.png")
    pq.write_table(pa.table({"id": ["p1", "p2"], "image": [PAGE, "https://example.org/iiif/other.tif"]}),
                   codh / "pages.parquet")
    box = pa.struct([("x", pa.int64()), ("y", pa.int64()), ("w", pa.int64()), ("h", pa.int64())])
    pq.write_table(pa.table({"id": [A, B, C], "page_id": ["p1", "p1", "p2"],
                             "box": pa.array([{"x": 0, "y": 0, "w": 50, "h": 50}, {"x": 60, "y": 0, "w": 50, "h": 50},
                                              {"x": 0, "y": 0, "w": 50, "h": 50}], box)}), codh / "units.parquet")


def test_the_publication_packs_tiles_and_resolves_decisions(tmp_path, monkeypatch):
    clustering(tmp_path, monkeypatch)
    forms.record("cluster", cluster="U+306F:one", form="𛂥")
    forms.record("glyph", units=[C], form=None)
    summary = publish(tmp_path, monkeypatch, "out")
    assert (summary["units"], summary["clusters"], summary["families"], summary["decisions"]) == (3, 1, 1, 2)
    assert (summary["tiles"], summary["tile_unheld"], summary["objects"]) == (2, 1, 1)
    db = schema()
    db.execute("INSERT INTO corpus_units(id,character,family,visual_group,shuffle,object,offset,size) VALUES(?,?,?,?,?,?,?,?)",
               (A, "は", "U+306F", None, 1, "x", 0, 1))
    publication = load(db, tmp_path / "out")
    rows = {r[0]: r[1:] for r in db.execute("SELECT id,cluster_form,glyph_set,glyph_form,form,length(split) FROM form_units")}
    assert rows[A] == ("𛂥", 0, None, "𛂥", 7) and rows[C] == ("𛂥", 1, None, None, 7)
    assert db.execute("SELECT form,assigned FROM form_clusters JOIN form_families ON family=code_point").fetchone() == ("𛂥", 2)
    # Each tile the rows name is in a pack the publication uploads.
    images = {r[0]: r[1] for r in db.execute("SELECT id,image FROM form_units")}
    assert images[C] is None
    objects = {o["key"]: tmp_path / "out" / o["file"] for o in publication["objects"]}
    for identity in (A, B):
        key = images[identity].removeprefix("/atlas/media/").removesuffix(".webp")
        pack, offset, size = db.execute("SELECT object,offset,size FROM media WHERE key=?", (key,)).fetchone()
        assert objects[pack].read_bytes()[offset:offset + size][:4] == b"RIFF"
    # Search and Quick review's counts follow the form, and the character it had is kept.
    assert db.execute("SELECT character FROM corpus_units WHERE id=?", (A,)).fetchone() == ("𛂥",)
    assert db.execute("SELECT character,n FROM corpus_characters").fetchall() == [("𛂥", 1)]
    assert db.execute("SELECT character FROM form_bases WHERE id=?", (A,)).fetchone() == ("は",)


def test_a_republication_keeps_decisions_made_online(tmp_path, monkeypatch):
    clustering(tmp_path, monkeypatch)
    forms.record("cluster", cluster="U+306F:one", form="𛂥")
    publish(tmp_path, monkeypatch, "first")
    db = schema()
    load(db, tmp_path / "first")
    # Someone on the site gives B its own form, later than the local cluster decision.
    db.execute("INSERT INTO form_decisions VALUES('online','2999-01-01T00:00:00+00:00','reviewer','glyph','U+306F',"
               "'𛂞',NULL,'r1',?,'')", (json.dumps([B]),))
    publish(tmp_path, monkeypatch, "second")
    load(db, tmp_path / "second")
    assert db.execute("SELECT count(*) FROM form_decisions").fetchone() == (2,)
    assert json.loads(db.execute("SELECT units FROM form_decisions WHERE kind='cluster'").fetchone()[0]) == [A, B, C], \
        "a decision written in parts is whole once, and a republication leaves it as it is"
    forms_now = dict(db.execute("SELECT id,form FROM form_units"))
    assert (forms_now[A], forms_now[B], forms_now[C]) == ("𛂥", "𛂞", "𛂥")
