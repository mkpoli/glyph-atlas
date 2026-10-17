"""A publication records each page its crops were cut from, for the page view (0078)."""
import importlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from glyph_atlas import images
from glyph_atlas.schema import Page

SCHEMA = "\n".join(p.read_text() for p in sorted(Path("apps/cloudflare/migrations").glob("*.sql")))


@pytest.fixture
def scripts(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    return importlib.import_module("export_cloudflare"), importlib.import_module("cloudflare_schema")


@pytest.mark.parametrize(("image", "service"), [
    ("https://dl.ndl.go.jp/api/iiif/2540585/R0000003", "https://dl.ndl.go.jp/api/iiif/2540585/R0000003"),
    ("https://iiif.adeac.jp/iiif/3/0720215100%2F059_to_02%2F034.tif", "https://iiif.adeac.jp/iiif/3/0720215100%2F059_to_02%2F034.tif"),
    ("https://kokusho.nijl.ac.jp/api/iiif/100334875/v4/TSDT/TSDT-01855/TSDT-01855-00000.tif/full/5616,/0/default.jpg",
     "https://kokusho.nijl.ac.jp/api/iiif/100334875/v4/TSDT/TSDT-01855/TSDT-01855-00000.tif"),
    ("https://da2.library.ryukoku.ac.jp/image/?IIIF=/17%2F170161%2F170161-0001.tif/full/1350,/0/default.jpg",
     "https://da2.library.ryukoku.ac.jp/image/?IIIF=/17/170161/170161-0001.tif"),
    ("https://ourarchives.amane-project.jp/iipsrv/iipsrv.fcgi?IIIF=/img/a.tif/full/full/0/default.jpg",
     "https://ourarchives.amane-project.jp/iipsrv/iipsrv.fcgi?IIIF=/img/a.tif"),
    ("https://example.org/service/info.json", "https://example.org/service"),
    ("https://images.eap.bl.uk/EAP140/EAP140_1_52/5.jp2", "https://images.eap.bl.uk/EAP140/EAP140_1_52/5.jp2"),
    ("https://upload.wikimedia.org/wikipedia/commons/a/ab/Scan.jpg", None),
    ("", None),
])
def test_a_page_image_names_its_iiif_service(image, service):
    assert images.service_of(image) == service


def unit(identity, page, line="l", seq=0):
    return SimpleNamespace(id=identity, page_id=page, line_id=line, seq=seq)


def test_each_page_holding_a_crop_lists_its_crops_in_reading_order_and_its_neighbours(scripts):
    export, _ = scripts
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    pages = {f"doc:{n}": Page(id=f"doc:{n}", document_id="doc", seq=n, image="https://x.org/iiif/2/p", width=100, height=200)
             for n in range(4)}
    units = {"a": unit("a", "doc:0", "l2"), "b": unit("b", "doc:0", "l1", 1), "c": unit("c", "doc:0", "l1", 0),
             "d": unit("d", "doc:3"), "gone": unit("gone", "doc:2")}
    rights = SimpleNamespace(licence="pd", attribution="Holder", evidence="https://x.org/rights")
    documents = {"doc": SimpleNamespace(title="Book", holder="Holder", image_rights=rights)}
    export.page_rows(db, ["a", "b", "c", "d"], units, pages, documents)
    rows = {i: json.loads(d) for i, d in db.execute("SELECT id,data FROM pages")}
    assert set(rows) == {"doc:0", "doc:3"}
    assert rows["doc:0"]["crops"] == ["c", "b", "a"]
    assert (rows["doc:0"]["previous"], rows["doc:0"]["next"]) == (None, "doc:3")
    assert (rows["doc:3"]["previous"], rows["doc:3"]["next"]) == ("doc:0", None)
    assert rows["doc:3"] | {"crops": None} == {
        "id": "doc:3", "document": "doc", "title": "Book", "page_number": 4, "width": 100, "height": 200,
        "service": "https://x.org/iiif/2/p", "canvas": None, "holder": "Holder", "licence": "pd",
        "attribution": "Holder", "rights_url": "https://x.org/rights", "previous": "doc:0", "next": None, "crops": None}


def test_a_republished_page_keeps_the_crops_and_neighbours_another_dataset_put_on_it(scripts):
    _, schema = scripts
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    page = lambda title, crops, previous, following: json.dumps(
        {"title": title, "previous": previous, "next": following, "crops": crops})
    db.execute(schema.page_upsert("hk:p", "hk:d", page("it's", ["a", "b"], "hk:o", "hk:q")))
    db.execute(schema.page_upsert("hk:p", "hk:d", page("new", ["c", "a"], None, "hk:r")))
    assert json.loads(db.execute("SELECT data FROM pages").fetchone()[0]) == {
        "title": "new", "previous": "hk:o", "next": "hk:r", "crops": ["c", "a", "b"]}
    before = db.total_changes
    db.execute(schema.page_upsert("hk:p", "hk:d", page("new", ["c", "a", "b"], "hk:o", "hk:r")))
    assert db.total_changes == before
