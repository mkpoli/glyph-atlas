import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from glyph_atlas import images, registry, tables
from glyph_atlas.importers import tangut_coins
from glyph_atlas.schema import Dating, Document, Licence, Page, Rights

ROOT = Path(__file__).resolve().parents[1]


def record(url: str, sha256: str, width: int, height: int, size: int) -> images.ImageRecord:
    return images.ImageRecord(url=url, sha256=sha256, width=width, height=height, bytes=size,
                              fetched_at=datetime.now(UTC))


FIXTURE = """
id: babelstone-tangut-coins
name: Tangut Coins
publisher: Andrew West, BabelStone Blog
kind: image-collection
url: https://example.org/coins
licence: unknown
attribution: Andrew West, Tangut Coins
article_sha256: abc
coins:
  - id: fusheng
    title: 福聖承道 寶錢 (Fusheng / Bingde)
    label: Fusheng / Bingde Baoqian
    reign_period_as_reported: Fusheng Chengdao 福聖承道 [1053-1056]
    status: disputed
    note: West leaves the reading unresolved.
    readings:
      - label: Fusheng Baoqian 福聖寶錢
        inscription: 𗼃𗼕𘏨𘔭
      - label: Bingde Baoqian 稟德寶錢
        inscription: 𗣼𗧯𘏨𘔭
  - id: daan
    title: 大安 寶錢 (Da'an)
    label: Da'an Baoqian
    reign_period_as_reported: Da'an 大安 [1076-1085]
    status: clear-in-source
    readings:
      - label: Da'an Baoqian 大安寶錢
        inscription: 𘜶𗵐𘏨𘔭
images:
  - id: fusheng-photo
    coin: fusheng
    kind: object-photograph
    label: Photograph
    image: https://example.org/fusheng.jpg
    linked_from_image: http://www.zeno.ru/showphoto.php?photo=16068
    width: 272
    height: 271
    bytes: 38703
    sha256: aa
  - id: fusheng-diagram
    coin: fusheng
    kind: editorial-diagram
    label: Fusheng Baoqian
    image: https://example.org/fusheng-drawing.jpg
    reading_index: 0
    width: 232
    height: 232
    bytes: 13263
    sha256: bb
  - id: daan-diagram
    coin: daan
    kind: editorial-diagram
    label: Da'an Baoqian
    image: https://example.org/daan-drawing.jpg
    reading_index: 0
    width: 232
    height: 232
    bytes: 13789
    sha256: cc
"""


def fake_fetch(records: dict[str, images.ImageRecord]):
    seen = []

    def fetch(url, *, root=None):
        seen.append(url)
        return records[url]

    return fetch, seen


def test_collect_writes_a_document_per_coin_with_its_images_as_pages(tmp_path):
    source = tmp_path / "coins.yaml"
    source.write_text(FIXTURE, encoding="utf-8")
    fetched = {
        "https://example.org/fusheng.jpg": record("https://example.org/fusheng.jpg", "aa", 272, 271, 38703),
        "https://example.org/fusheng-drawing.jpg": record(
            "https://example.org/fusheng-drawing.jpg", "bb", 232, 232, 13263),
        "https://example.org/daan-drawing.jpg": record("https://example.org/daan-drawing.jpg", "cc", 232, 232, 13789),
    }
    fetch, seen = fake_fetch(fetched)
    out = tmp_path / "coins"
    summary = tangut_coins.collect(source, out, fetch=fetch)

    assert seen == list(fetched)
    assert (summary["documents"], summary["pages"]) == (2, 3)
    assert (summary["photographs"], summary["diagrams"]) == (1, 2)
    assert summary["unavailable"] == []
    documents = {document.id: document for document in tables.read(out / "documents.parquet", Document)}
    pages = {page.id: page for page in tables.read(out / "pages.parquet", Page)}
    assert set(documents) == {"babelstone-tangut-coins:fusheng", "babelstone-tangut-coins:daan"}
    assert set(pages) == {
        "babelstone-tangut-coins:fusheng:fusheng-photo",
        "babelstone-tangut-coins:fusheng:fusheng-diagram",
        "babelstone-tangut-coins:daan:daan-diagram",
    }
    fusheng = documents["babelstone-tangut-coins:fusheng"]
    assert fusheng.title == "福聖承道 寶錢 (Fusheng / Bingde)"
    assert (fusheng.image_kind, fusheng.production, fusheng.origin) == (
        "object-photograph", "inscribed/metal", "china")
    assert fusheng.meta["status"] == "disputed"
    assert [reading["inscription"] for reading in fusheng.meta["readings"]] == ["𗼃𗼕𘏨𘔭", "𗣼𗧯𘏨𘔭"]
    assert [image["kind"] for image in fusheng.meta["images"]] == ["object-photograph", "editorial-diagram"]
    assert documents["babelstone-tangut-coins:daan"].image_kind == "editorial-diagram"
    assert fusheng.image_rights.licence is Licence.UNKNOWN
    assert fusheng.image_rights.holder_terms is None
    assert fusheng.image_rights.evidence == "https://example.org/coins"
    assert fusheng.image_rights.attribution.startswith("Andrew West")
    first, second = pages["babelstone-tangut-coins:fusheng:fusheng-photo"], pages[
        "babelstone-tangut-coins:fusheng:fusheng-diagram"]
    assert (first.seq, first.width, first.height, first.sha256) == (0, 272, 271, "aa")
    assert first.image == "https://example.org/fusheng.jpg"
    assert first.meta == {"kind": "object-photograph", "label": "Photograph",
                          "linked_from_image": "http://www.zeno.ru/showphoto.php?photo=16068"}
    assert second.meta == {"kind": "editorial-diagram", "label": "Fusheng Baoqian", "reading_index": 0}
    manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == tables.SCHEMA_VERSION
    assert manifest["tables"] == {"documents": 2, "pages": 3}
    assert manifest["collection"]["photographs"] == 1


def test_a_changed_image_stops_the_import(tmp_path):
    source = tmp_path / "coins.yaml"
    source.write_text(FIXTURE, encoding="utf-8")
    fetch, _ = fake_fetch({
        "https://example.org/fusheng.jpg": record("https://example.org/fusheng.jpg", "zz", 272, 271, 38703),
    })
    with pytest.raises(tangut_coins.CoinSourceError, match="fusheng-photo is not the image"):
        tangut_coins.collect(source, tmp_path / "coins", fetch=fetch)


def test_object_photographs_and_drawings_keep_their_terms():
    terms = Rights(licence=Licence.UNKNOWN, attribution="Andrew West")
    coin = Document(id="coin", title="coin", dating=[Dating(start=1100)], image_kind="object-photograph",
                    image_rights=terms.model_copy())
    drawing = Document(id="drawing", title="drawing", dating=[Dating(start=1100)], image_kind="editorial-diagram",
                       image_rights=terms.model_copy())
    flat = Document(id="flat", title="flat", dating=[Dating(start=1100)], image_rights=terms.model_copy())
    assert coin.image_rights.licence is Licence.UNKNOWN
    assert drawing.image_rights.licence is Licence.UNKNOWN
    assert flat.image_rights.licence is Licence.PUBLIC_DOMAIN
    assert flat.image_rights.holder_terms is Licence.UNKNOWN


def test_a_failed_image_is_listed_and_leaves_the_other_coins(tmp_path):
    source = tmp_path / "coins.yaml"
    source.write_text(FIXTURE, encoding="utf-8")
    records = {
        "https://example.org/fusheng-drawing.jpg": record(
            "https://example.org/fusheng-drawing.jpg", "bb", 232, 232, 13263),
        "https://example.org/daan-drawing.jpg": record("https://example.org/daan-drawing.jpg", "cc", 232, 232, 13789),
    }

    def fetch(url, *, root=None):
        if url.endswith("fusheng.jpg"):
            raise images.ImageError("the host did not answer")
        return records[url]

    summary = tangut_coins.collect(source, tmp_path / "coins", fetch=fetch)
    assert summary["unavailable"] == [{"image": "fusheng-photo", "error": "the host did not answer"}]
    assert (summary["documents"], summary["pages"]) == (2, 2)
    assert (summary["photographs"], summary["diagrams"]) == (0, 2)
    documents = {document.id: document for document in tables.read(tmp_path / "coins" / "documents.parquet", Document)}
    assert documents["babelstone-tangut-coins:fusheng"].image_kind == "editorial-diagram"


def test_the_source_record_lists_every_coin_and_image():
    config = yaml.safe_load((ROOT / "data/sources/babelstone-tangut-coins.yaml").read_text(encoding="utf-8"))
    registered = {source.id: source for source in registry.load()}
    assert registered[tangut_coins.SOURCE_ID].name == "Tangut Coins"
    assert registered[tangut_coins.SOURCE_ID].licence is Licence.UNKNOWN
    assert config["id"] == tangut_coins.SOURCE_ID
    coins = {coin["id"]: coin for coin in config["coins"]}
    assert len(coins) == config["counts"]["coin_types"] == 6
    assert len(config["images"]) == 11
    assert sum(1 for image in config["images"] if image["kind"] == "object-photograph") == config["counts"]["photographs"] == 4
    assert sum(1 for image in config["images"] if image["kind"] == "editorial-diagram") == config["counts"]["editorial_diagrams"] == 7
    for image in config["images"]:
        assert image["coin"] in coins
        assert image["image"].startswith("https://www.babelstone.co.uk/Tangut/Images/")
        assert len(image["sha256"]) == 64
        assert image["width"] > 0 and image["height"] > 0 and image["bytes"] > 0
        assert image["label"]
        if image["kind"] == "editorial-diagram":
            assert 0 <= image["reading_index"] < len(coins[image["coin"]]["readings"])
        else:
            assert image.get("linked_from_image")
    assert {coin["id"] for coin in config["coins"]} == {image["coin"] for image in config["images"]}
