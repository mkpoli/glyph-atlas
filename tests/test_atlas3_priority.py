import sqlite3

import pytest
from test_atlas3_publish import catalogue, ids, layout, run, unit  # noqa: F401 -- layout is a fixture

BOX = {"x": 0, "y": 0, "w": 10, "h": 10}


def crops(db):
    return dict(db.execute("SELECT id, priority FROM crops"))


def test_a_build_gives_each_collected_crop_its_unit_s_priority_and_each_corpus_glyph_1(layout):  # noqa: F811
    root, _publish, _remote = layout
    atlas = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert set(crops(atlas).values()) == {1}


def test_a_publish_carries_the_catalogue_s_priority(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", BOX, "あ", priority=0)]))
    a1 = ids(root)["ex:a1"]
    assert crops(remote.dbs["glyph-atlas-v3"])[a1] == 0
    assert crops(sqlite3.connect(root / "out" / "atlas.sqlite"))[a1] == 0


def test_a_publish_refuses_a_layout_the_fill_has_not_given_the_column(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    atlas = sqlite3.connect(root / "out" / "atlas.sqlite")
    atlas.executescript("DROP INDEX crop_listing; DROP INDEX crop_deal; DROP INDEX crop_flagged; ALTER TABLE crops DROP COLUMN priority;")
    atlas.commit()
    with pytest.raises(SystemExit, match="fill.py --send"):
        run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", BOX, "あ", priority=0)]))
    assert remote.uploaded == []
