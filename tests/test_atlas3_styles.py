import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from test_atlas3_publish import crops, ids, layout, run  # noqa: F401 -- the fixture

from glyph_atlas import style

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "atlas3"))
styles = importlib.import_module("styles")

REVIEWED = "evidence:\n      - source: reviewer\n        reviewed: 2026-09-26"


def corpus(root, name, units, pages=()):
    directory = root / name
    directory.mkdir(parents=True)
    pq.write_table(pa.table({"id": [u[0] for u in units], "document_id": [u[1] for u in units]}), directory / "units.parquet")
    if pages:
        pq.write_table(pa.table({"id": [p[0] for p in pages], "document_id": [p[1] for p in pages],
                                 "style": [p[2] for p in pages]}), directory / "pages.parquet")


def test_a_document_in_no_corpus_is_refused(tmp_path):
    corpus(tmp_path, "codh", [("codh:1:a", "codh:1")])
    styles.corpus_styles({"codh:1"}, tmp_path)
    with pytest.raises(styles.Refused, match="codh:2"):
        styles.corpus_styles({"codh:1", "codh:2"}, tmp_path)


def test_a_crop_with_a_style_of_its_own_is_refused(tmp_path):
    directory = tmp_path / "codh"
    directory.mkdir()
    pq.write_table(pa.table({"id": ["codh:1:a", "codh:1:b"], "document_id": ["codh:1", "codh:1"],
                             "style": ["unassessed", "regular"]}), directory / "units.parquet")
    with pytest.raises(styles.Refused, match="codh:1:b"):
        styles.corpus_styles({"codh:1"}, tmp_path)


def test_a_page_with_its_own_style_is_found(tmp_path):
    corpus(tmp_path, "c", [("c:1:a", "c:1")], pages=[("c:1:p1", "c:1", "unassessed"), ("c:1:p2", "c:1", "regular")])
    assert styles.page_styles({"c:1"}, tmp_path) == {"c:1:p2": "regular"}


def test_each_confirmed_document_gives_its_style_and_mixed_gives_none(tmp_path, monkeypatch):
    confirmed = tmp_path / "document-styles.yaml"
    confirmed.write_text(f"documents:\n  codh:1:\n    style: cursive\n    {REVIEWED}\n"
                         f"  hk:2:\n    style: mixed\n    {REVIEWED}\n", encoding="utf-8")
    monkeypatch.setattr(style, "DOCUMENTS", confirmed)
    assert styles.book_styles(style.confirmed()) == [("codh:1", "cursive"), ("hk:2", "unassessed")]


def styled(path, rows):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE book_styles(document TEXT PRIMARY KEY, style TEXT NOT NULL)")
    db.executemany("INSERT INTO book_styles VALUES(?,?)", rows)
    db.commit()
    return path


def style_of(remote, cid):
    return remote.query("glyph-atlas-v3", f"SELECT k.key FROM crops c JOIN codes k ON k.field='style' AND k.code=c.style WHERE c.id={cid}")[0]["key"]


def test_a_book_style_reaches_every_crop_and_glyph_of_its_book_and_no_bundle(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    before = remote.query("glyph-atlas-v3", "SELECT id, rev FROM pages")
    path = styled(tmp_path / "s.sqlite", [("book:a", "cursive"), ("codh:b", "regular"), ("hl:never", "regular")])
    run(publish, monkeypatch, path, "--dry-run")
    figures = json.loads((max((root / "out" / "publish").glob("*-s")) / "styles.json").read_text())
    assert figures["crops"] == 4 and figures["index_entries"]["crop_char"] == 4
    with pytest.raises(SystemExit, match="--approve-styles 4"):
        run(publish, monkeypatch, path)
    assert remote.query("glyph-atlas-v3", "SELECT id, rev FROM pages") == before
    run(publish, monkeypatch, path, "--approve-styles", "4")
    got = {text: style_of(remote, cid) for text, cid in ids(root).items()}
    assert got == {"ex:a1": "cursive", "ex:a2": "cursive", "ex:b1": "cursive", "codh:g1": "regular"}
    assert remote.query("glyph-atlas-v3", "SELECT id, rev FROM pages") == before
    assert remote.uploaded == []
    revisions = {cid: r["revision"] for cid, r in crops(remote).items()}
    # Sent again, nothing differs and nothing is sent.
    run(publish, monkeypatch, styled(tmp_path / "again.sqlite", [("book:a", "cursive"), ("codh:b", "regular")]))
    assert {cid: r["revision"] for cid, r in crops(remote).items()} == revisions


def test_a_crop_reviewed_since_the_mirror_read_keeps_the_sites_row(layout, monkeypatch, tmp_path, capsys):  # noqa: F811
    root, publish, remote = layout
    a1 = ids(root)["ex:a1"]
    remote.dbs["glyph-atlas-v3"].execute(f"UPDATE crops SET revision=revision+1 WHERE id={a1}")
    remote.dbs["glyph-atlas-v3"].commit()
    run(publish, monkeypatch, styled(tmp_path / "s.sqlite", [("book:a", "cursive")]), "--approve-styles", "3")
    assert style_of(remote, a1) == "unassessed" and style_of(remote, ids(root)["ex:a2"]) == "cursive"
    assert "1 changes kept D1's newer review" in capsys.readouterr().out
    local = sqlite3.connect(root / "out" / "atlas.sqlite").execute(f"SELECT revision FROM crops WHERE id={a1}").fetchone()[0]
    assert local == 1


def cells(db):
    """The tally's collected-crop and corpus cells by style code, as {style: n}."""
    return {r[0]: r[1] for r in db.execute("SELECT style, sum(n) FROM tally WHERE kind<2 GROUP BY style") if r[1]}


def test_a_restyled_crop_moves_its_tally_cell_and_one_reviewed_since_does_not(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    site = remote.dbs["glyph-atlas-v3"]
    a1 = ids(root)["ex:a1"]
    site.execute(f"UPDATE crops SET revision=revision+1 WHERE id={a1}")
    site.commit()
    before = cells(site)
    run(publish, monkeypatch, styled(tmp_path / "s.sqlite", [("book:a", "cursive")]), "--approve-styles", "3")
    cursive = site.execute("SELECT code FROM codes WHERE field='style' AND key='cursive'").fetchone()[0]
    unassessed = site.execute("SELECT code FROM codes WHERE field='style' AND key='unassessed'").fetchone()[0]
    after = cells(site)
    # Two crops moved from unassessed to cursive; the one reviewed since kept its row and its cell.
    assert after.get(cursive, 0) - before.get(cursive, 0) == 2
    assert after.get(unassessed, 0) - before.get(unassessed, 0) == -2
    assert cells(sqlite3.connect(root / "out" / "atlas.sqlite")) == after


def test_a_crop_a_review_gave_a_style_keeps_it(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    a1, site = ids(root)["ex:a1"], remote.dbs["glyph-atlas-v3"]
    regular = site.execute("SELECT code FROM codes WHERE field='style' AND key='regular'").fetchone()
    regular = regular[0] if regular else 99
    # The style a review gave it on the site, where the local copy has not seen it yet.
    site.execute("INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,style) VALUES(?,0,0,0,0,0,0,0,?)", (a1, regular))
    site.commit()
    run(publish, monkeypatch, styled(tmp_path / "s.sqlite", [("book:a", "cursive")]), "--approve-styles", "3")
    assert style_of(remote, a1) == "unassessed" and style_of(remote, ids(root)["ex:a2"]) == "cursive"


def test_a_layout_crop_with_a_style_of_its_own_stops_the_catalogue(layout, tmp_path):  # noqa: F811
    root, _publish, _remote = layout
    mirror, atlas = sqlite3.connect(root / "out" / "mirror.sqlite"), sqlite3.connect(root / "out" / "atlas.sqlite")
    assert styles.layout_styles({"book:a"}, mirror, atlas) == []
    a2 = ids(root)["ex:a2"]
    atlas.execute("INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,style) VALUES(?,9,0,0,0,0,0,0,1)", (a2,))
    assert styles.layout_styles({"book:a", "hl:never"}, mirror, atlas) == [a2]
    assert styles.layout_styles({"codh:b"}, mirror, atlas) == []


def test_a_styles_catalogue_carries_nothing_else(layout, monkeypatch, tmp_path):  # noqa: F811
    _root, publish, remote = layout
    path = styled(tmp_path / "s.sqlite", [("book:a", "cursive")])
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE units(id)")
    db.commit()
    with pytest.raises(SystemExit, match="holds book_styles alone"):
        run(publish, monkeypatch, path, "--approve-styles", "3")
    assert remote.uploaded == []


def test_the_dry_run_counts_each_chunks_tally_rows(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, _remote = layout
    run(publish, monkeypatch, styled(tmp_path / "s.sqlite", [("book:a", "cursive")]), "--dry-run")
    figures = json.loads((max((root / "out" / "publish").glob("*-s")) / "styles.json").read_text())
    # Three crops of book:a: あ pending, い checked, う pending, each its own cell, each moved to cursive.
    assert figures["crops"] == 3 and figures["tally_rows"] == 6
