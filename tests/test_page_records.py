"""A publication records each page its crops were cut from, for the page view (0078)."""
import importlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from glyph_atlas import images
from glyph_atlas.review.media import MediaCache
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


def test_each_page_holding_a_crop_lists_its_crops_in_reading_order(scripts):
    export, _ = scripts
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    pages = {f"doc:{n}": Page(id=f"doc:{n}", document_id="doc", seq=n, image="https://x.org/iiif/2/p", width=100, height=200)
             for n in range(4)}
    units = {"a": unit("a", "doc:0", "l2"), "b": unit("b", "doc:0", "l1", 1), "c": unit("c", "doc:0", "l1", 0),
             "d": unit("d", "doc:3"), "gone": unit("gone", "doc:2")}
    rights = SimpleNamespace(licence="pd", attribution="Holder", evidence="https://x.org/rights")
    documents = {"doc": SimpleNamespace(title="Book", holder="Holder", image_rights=rights, source_refs={}, meta={})}
    export.page_rows(db, ["a", "b", "c", "d"], units, pages, documents)
    rows = {i: json.loads(d) for i, d in db.execute("SELECT id,data FROM pages")}
    assert set(rows) == {"doc:0", "doc:3"}
    assert rows["doc:0"]["crops"] == ["c", "b", "a"]
    assert rows["doc:3"] | {"crops": None} == {
        "id": "doc:3", "document": "doc", "title": "Book", "page_number": 4, "width": 100, "height": 200,
        "service": "https://x.org/iiif/2/p", "manifest": None, "canvas": None, "canvas_index": None, "catalogue_url": None, "holder": "Holder", "licence": "pd",
        "attribution": "Holder", "rights_url": "https://x.org/rights", "crops": None}


def test_a_republished_page_keeps_the_crops_another_dataset_put_on_it(scripts):
    _, schema = scripts
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    page = lambda title, crops: json.dumps({"title": title, "crops": crops})
    db.execute(schema.page_upsert("hk:p", "hk:d", page("it's", ["a", "b"])))
    db.execute(schema.page_upsert("hk:p", "hk:d", page("new", ["c", "a"])))
    assert json.loads(db.execute("SELECT data FROM pages").fetchone()[0]) == {"title": "new", "crops": ["c", "a", "b"]}
    before = db.total_changes
    db.execute(schema.page_upsert("hk:p", "hk:d", page("new", ["c", "a", "b"])))
    assert db.total_changes == before


class Packed:
    """The packs of an export, as far as a page photograph meets them."""
    def __init__(self):
        self.added, self.synced = {}, 0

    def add(self, key, path, content_type="image/webp"):
        self.added[key] = path

    def sync(self):
        self.synced += 1


@pytest.fixture
def scan(tmp_path, monkeypatch):
    """A page image in the image cache, registered under a plain URL no IIIF service serves."""
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path))
    file = tmp_path / "A0280017.jpg"
    Image.new("RGB", (239, 200), "white").save(file)
    held = images.register(file, "http://kanji.example/toho/L/A0280017.jpg")
    return Page(id="doc:0", document_id="doc", seq=0, image=held.url, width=411, height=344, sha256=held.sha256)


def book(licence="PD"):
    rights = SimpleNamespace(licence=licence, attribution="Holder", evidence="https://x.org/rights")
    return SimpleNamespace(title="Book", holder="Holder", image_rights=rights, source_refs={}, meta={})


def test_a_page_of_a_hosted_document_is_packed_whole_and_named_in_its_record(scripts, scan):
    export, _ = scripts
    packs = Packed()
    photo = export.PagePhotos(MediaCache(), packs, hosted={"doc"})
    url = photo(scan, book())
    key = url.removeprefix("/atlas/media/").removesuffix(".webp")
    assert set(packs.added) == {key}
    with Image.open(packs.added[key]) as picture:
        assert (picture.format, picture.size) == ("WEBP", (239, 200))
    # The same page asked again is the same image, so a second export packs nothing new.
    assert photo(scan, book()) == url
    # Another document photographed from the same file has a photograph of its own.
    other = export.PagePhotos(MediaCache(), Packed(), hosted={"twin"})(scan.model_copy(update={"document_id": "twin"}), book())
    assert other and other != url
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    export.page_rows(db, ["a"], {"a": unit("a", "doc:0")}, {"doc:0": scan}, {"doc": book()}, photo)
    record = json.loads(db.execute("SELECT data FROM pages").fetchone()[0])
    assert (record["photo"], record["service"], packs.synced) == (url, None, 1)


def test_a_page_keeps_no_photograph_unless_every_condition_holds(scripts, scan):
    export, _ = scripts
    packs = Packed()
    media = MediaCache()
    # A document no source names.
    assert export.PagePhotos(media, packs, hosted=set())(scan, book()) is None
    hosted = export.PagePhotos(media, packs, hosted={"doc"})
    # A page a IIIF service serves is shown from the service.
    served = scan.model_copy(update={"image": "https://x.org/iiif/2/p", "sha256": None})
    assert hosted(served, book()) is None
    # A licence the site serves no crops under.
    assert hosted(scan, book("CC-BY-NC-4.0")) is None
    # A page whose file is not in the image cache.
    assert hosted(scan.model_copy(update={"image": "http://kanji.example/other.jpg", "sha256": None}), book()) is None
    assert packs.added == {}
    assert "photo" not in export.page_record(scan, book(), [])
