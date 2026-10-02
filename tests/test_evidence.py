"""Crop evidence versions: the same id from Python and from the site's SQL, and a recrop keeps the old one."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from glyph_atlas import evidence
from glyph_atlas.schema import Box

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from cloudflare_schema import schema

PAGE, SOURCE = "a" * 64, "b" * 64


def site() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    schema(db)
    return db


def put(db, identity: str, data: dict, origin: str = "local") -> None:
    db.execute("INSERT INTO units(id,origin,character,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual) "
               "VALUES(?,?,'還','unknown','kanji','pending',0,1,1,1,?,'{}','{}','{}')", (identity, origin, json.dumps(data)))


def test_a_version_joins_the_crop_its_image_and_its_box():
    assert evidence.crop_version("hk:1", PAGE, Box(x=1, y=2, w=30, h=40)) == f"hk:1@{PAGE}@1,2,30,40"
    assert evidence.crop_version("hk:1", PAGE, None) == f"hk:1@{PAGE}@"
    assert evidence.crop_version("hk:1", None, Box(x=1, y=2, w=3, h=4)) is None
    assert evidence.record_version({"id": "codh:1", "source_revision": SOURCE, "box": {"x": 5, "y": 6, "w": 7, "h": 8}}) \
        == f"codh:1@{SOURCE}@5,6,7,8"
    with pytest.raises(TypeError):
        evidence.box_text({"x": "1", "y": 2, "w": 3, "h": 4})


@pytest.mark.parametrize("data", [
    {"id": "hk:1", "image_sha256": PAGE, "box": {"x": 1, "y": 2, "w": 30, "h": 40}},
    {"id": "hk:2", "image_sha256": PAGE, "box": None},
    {"id": "hk:3", "image_sha256": PAGE},
    {"id": "codh:1", "source_revision": SOURCE, "box": {"x": 911, "y": 1475, "w": 92, "h": 95}},
    {"id": "hk:4", "image_sha256": None, "source_revision": SOURCE, "box": {"x": 0, "y": 0, "w": 1, "h": 1}},
    {"id": "hk:5", "image_sha256": PAGE, "box": {"x": 1.5, "y": 2.0, "w": 3, "h": 4}},
    {"id": "hk:6", "box": {"x": 1, "y": 2, "w": 3, "h": 4}},
])
def test_python_names_a_version_as_the_site_column_does(data):
    db = site()
    put(db, data["id"], data)
    column, = db.execute("SELECT crop_version FROM units").fetchone()
    assert column == evidence.record_version(data)


def test_a_recrop_adds_a_version_and_keeps_the_one_reviewed_before():
    db = site()
    first = {"id": "hk:1", "image_sha256": PAGE, "box": {"x": 1, "y": 2, "w": 30, "h": 40}, "image": "/atlas/media/c.webp"}
    put(db, "hk:1", first)
    # A review rewrites the record and leaves its pixels: no new version.
    db.execute("UPDATE units SET data=? WHERE id='hk:1'", (json.dumps({**first, "state": "checked"}),))
    recut = {**first, "box": {"x": 1, "y": 2, "w": 31, "h": 40}, "image": "/atlas/media/d.webp"}
    db.execute("UPDATE units SET data=? WHERE id='hk:1'", (json.dumps(recut),))
    rows = db.execute("SELECT id,unit,pixels,box,image FROM crop_versions ORDER BY box").fetchall()
    assert rows == [(f"hk:1@{PAGE}@1,2,30,40", "hk:1", PAGE, "1,2,30,40", "/atlas/media/c.webp"),
                    (f"hk:1@{PAGE}@1,2,31,40", "hk:1", PAGE, "1,2,31,40", "/atlas/media/d.webp")]
    assert db.execute("SELECT crop_version FROM units").fetchone()[0] == f"hk:1@{PAGE}@1,2,31,40"
    # A retired crop deleted from the catalogue leaves its versions.
    db.execute("DELETE FROM units WHERE id='hk:1'")
    assert db.execute("SELECT count(*) FROM crop_versions").fetchone()[0] == 2


def test_a_version_is_never_changed_or_removed():
    db = site()
    put(db, "hk:1", {"id": "hk:1", "image_sha256": PAGE, "box": None})
    with pytest.raises(sqlite3.IntegrityError, match="crop_version_immutable"):
        db.execute("UPDATE crop_versions SET image='x'")
    with pytest.raises(sqlite3.IntegrityError, match="crop_version_immutable"):
        db.execute("DELETE FROM crop_versions")
    assert db.execute("SELECT box FROM crop_versions").fetchone() == (None,)


def test_a_crop_with_no_image_checksum_has_no_version():
    db = site()
    put(db, "hk:1", {"id": "hk:1", "box": {"x": 1, "y": 2, "w": 3, "h": 4}})
    assert db.execute("SELECT count(*) FROM crop_versions").fetchone()[0] == 0
