import json
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest
import yaml
from typer.testing import CliRunner

from glyph_atlas import images, registry, tables
from glyph_atlas.cli import app
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
licence_evidence: https://example.org/terms
attribution: Andrew West, Tangut Coins (fixture)
checked: "2026-11-07"
article_sha256: abc
notes:
  - Worn coins and fakes are described; no authenticity determination is made.
reading_order: top-right-bottom-left
reading_system: Li Fanwen (1997)
coins:
  - id: fusheng
    title: 福聖寶錢 / 稟德寶錢 (Fusheng / Bingde)
    label: Fusheng / Bingde Baoqian
    reign_period_as_reported: Fusheng Chengdao 福聖承道 [1053-1056]
    status: disputed
    note: West leaves the reading unresolved.
    example_url: http://www.zeno.ru/showphoto.php?photo=16068
    readings:
      - label: Fusheng Baoqian 福聖寶錢
        inscription: 𗼃𗼕𘏨𘔭
      - label: Bingde Baoqian 稟德寶錢
        inscription: 𗣼𗧯𘏨𘔭
  - id: zhenguan
    title: 貞觀寶錢 / 觀德寶錢 (Zhenguan / Guande)
    label: Zhenguan / Guande Baoqian
    reign_period_as_reported: Zhenguan 貞觀 [1102-1114]
    status: disputed
    readings:
      - label: Zhenguan Baoqian 貞觀寶錢
        inscription: 𗣼𘝯𘏨𘔭
  - id: daan
    title: 大安寶錢 (Da'an)
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
  - id: zhenguan-photo
    coin: zhenguan
    kind: object-photograph
    label: Photograph
    image: https://example.org/zhenguan.jpg
    linked_from_image: https://example.org/forum
    width: 290
    height: 291
    bytes: 27329
    sha256: bb
"""

DIAGRAM = """
  - id: fusheng-diagram
    coin: fusheng
    kind: editorial-diagram
    label: Fusheng Baoqian
    image: https://example.org/fusheng-drawing.jpg
    reading_index: 0
    width: 232
    height: 232
    bytes: 13263
    sha256: cc
"""


def fake_fetch(records: dict[str, images.ImageRecord]):
    seen = []

    def fetch(url, *, root=None):
        seen.append(url)
        return records[url]

    return fetch, seen


def test_collect_imports_photographs_and_skips_types_without_one(tmp_path):
    source = tmp_path / "coins.yaml"
    source.write_text(FIXTURE, encoding="utf-8")
    fetched = {
        "https://example.org/fusheng.jpg": record("https://example.org/fusheng.jpg", "aa", 272, 271, 38703),
        "https://example.org/zhenguan.jpg": record("https://example.org/zhenguan.jpg", "bb", 290, 291, 27329),
    }
    fetch, seen = fake_fetch(fetched)
    out = tmp_path / "coins"
    summary = tangut_coins.collect(source, out, fetch=fetch)

    assert seen == list(fetched)
    assert (summary["documents"], summary["pages"], summary["photographs"]) == (2, 2, 2)
    assert summary["skipped"] == ["daan"]
    documents = {document.id: document for document in tables.read(out / "documents.parquet", Document)}
    pages = {page.id: page for page in tables.read(out / "pages.parquet", Page)}
    assert set(documents) == {"babelstone-tangut-coins:fusheng", "babelstone-tangut-coins:zhenguan"}
    assert set(pages) == {"babelstone-tangut-coins:fusheng:fusheng-photo",
                          "babelstone-tangut-coins:zhenguan:zhenguan-photo"}
    fusheng = documents["babelstone-tangut-coins:fusheng"]
    assert fusheng.title == "福聖寶錢 / 稟德寶錢 (Fusheng / Bingde)"
    assert (fusheng.image_kind, fusheng.production, fusheng.origin) == (
        "object-photograph", "inscribed/metal", "china")
    assert fusheng.meta["status"] == "disputed"
    assert fusheng.meta["example_url"] == "http://www.zeno.ru/showphoto.php?photo=16068"
    assert fusheng.meta["reading_order"] == "top-right-bottom-left"
    assert fusheng.meta["reading_system"] == "Li Fanwen (1997)"
    assert fusheng.meta["source_notes"] == ["Worn coins and fakes are described; no authenticity determination is made."]
    assert fusheng.meta["article_sha256"] == "abc"
    assert [reading["inscription"] for reading in fusheng.meta["readings"]] == ["𗼃𗼕𘏨𘔭", "𗣼𗧯𘏨𘔭"]
    assert [image["id"] for image in fusheng.meta["images"]] == ["fusheng-photo"]
    assert fusheng.image_rights.licence is Licence.UNKNOWN
    assert fusheng.image_rights.holder_terms is None
    assert fusheng.image_rights.evidence == "https://example.org/terms"
    assert fusheng.image_rights.attribution == "Andrew West, Tangut Coins (fixture)"
    assert fusheng.image_rights.checked == date(2026, 11, 7)
    first = pages["babelstone-tangut-coins:fusheng:fusheng-photo"]
    assert (first.seq, first.width, first.height, first.sha256) == (0, 272, 271, "aa")
    assert (first.image, first.image_kind) == ("https://example.org/fusheng.jpg", "object-photograph")
    assert first.meta == {"label": "Photograph",
                          "linked_from_image": "http://www.zeno.ru/showphoto.php?photo=16068"}
    manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == tables.SCHEMA_VERSION == 7
    assert manifest["tables"] == {"documents": 2, "pages": 2}
    assert manifest["command"] == "atlas import tangut-coins"
    assert manifest["collection"]["skipped"] == ["daan"]


def test_a_drawing_in_the_record_is_refused(tmp_path):
    source = tmp_path / "coins.yaml"
    source.write_text(FIXTURE.replace("images:\n", "images:" + DIAGRAM + "\n", 1), encoding="utf-8")
    with pytest.raises(tangut_coins.CoinSourceError, match="editorial-diagram; only coin photographs"):
        tangut_coins.collect(source, tmp_path / "coins", fetch=lambda url, *, root=None: None)


def test_a_changed_image_stops_the_import_before_any_table(tmp_path):
    source = tmp_path / "coins.yaml"
    source.write_text(FIXTURE, encoding="utf-8")
    fetch, _ = fake_fetch({
        "https://example.org/fusheng.jpg": record("https://example.org/fusheng.jpg", "zz", 272, 271, 38703),
    })
    out = tmp_path / "coins"
    with pytest.raises(tangut_coins.CoinSourceError, match="fusheng-photo is not the image"):
        tangut_coins.collect(source, out, fetch=fetch)
    assert not (out / "documents.parquet").exists()


def test_a_failed_image_stops_the_import_before_any_table(tmp_path):
    source = tmp_path / "coins.yaml"
    source.write_text(FIXTURE, encoding="utf-8")

    def fetch(url, *, root=None):
        raise images.ImageError("the host did not answer")

    out = tmp_path / "coins"
    with pytest.raises(tangut_coins.CoinSourceError, match="fusheng-photo: the host did not answer"):
        tangut_coins.collect(source, out, fetch=fetch)
    assert not (out / "documents.parquet").exists()


def test_object_photographs_and_drawings_keep_their_terms():
    terms = Rights(licence=Licence.UNKNOWN, attribution="Andrew West")
    coin = Document(id="coin", title="coin", dating=[Dating(start=1100)], image_kind="object-photograph",
                    image_rights=terms.model_copy())
    drawing = Document(id="drawing", title="drawing", dating=[Dating(start=1100)], image_kind="editorial-diagram",
                       image_rights=terms.model_copy())
    mixed = Document(id="mixed", title="mixed", dating=[Dating(start=1100)], image_kind="mixed",
                     image_rights=terms.model_copy())
    flat = Document(id="flat", title="flat", dating=[Dating(start=1100)], image_rights=terms.model_copy())
    assert coin.image_rights.licence is Licence.UNKNOWN
    assert drawing.image_rights.licence is Licence.UNKNOWN
    assert mixed.image_rights.licence is Licence.UNKNOWN
    assert flat.image_rights.licence is Licence.PUBLIC_DOMAIN
    assert flat.image_rights.holder_terms is Licence.UNKNOWN


def test_a_non_flat_document_restores_a_holder_statement_moved_to_licence():
    terms = Rights(licence=Licence.PUBLIC_DOMAIN, holder_terms=Licence.UNKNOWN, attribution="Andrew West")
    document = Document(id="coin", title="coin", image_kind="object-photograph", image_rights=terms)
    assert document.image_rights.licence is Licence.UNKNOWN
    assert document.image_rights.holder_terms is None


def test_tables_written_before_the_field_read_as_flat(tmp_path):
    documents = tmp_path / "documents.parquet"
    document = Document(id="old", title="old", image_rights=Rights(licence=Licence.UNKNOWN, attribution="old"))
    tables.write(documents, [document], Document)
    pq.write_table(pq.read_table(documents).drop(["image_kind"]), documents)
    assert tables.read(documents, Document)[0].image_kind == "flat-reproduction"
    pages = tmp_path / "pages.parquet"
    page = Page(id="old:1", document_id="old", seq=0, image="https://example.org/1.jpg", width=10, height=10)
    tables.write(pages, [page], Page)
    pq.write_table(pq.read_table(pages).drop(["image_kind"]), pages)
    assert tables.read(pages, Page)[0].image_kind == "flat-reproduction"


def test_the_command_refuses_a_bad_source_and_writes_nothing(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: not-the-coin-source\n", encoding="utf-8")
    out = tmp_path / "out"
    result = CliRunner().invoke(app, ["import", "tangut-coins", "--source", str(bad), "--out", str(out)])
    assert result.exit_code == 1
    assert "not the babelstone-tangut-coins source record" in result.output
    assert not (out / "documents.parquet").exists()


def test_the_source_record_lists_photographs_and_the_excluded_drawings():
    config = yaml.safe_load((ROOT / "data/sources/babelstone-tangut-coins.yaml").read_text(encoding="utf-8"))
    assert config["id"] == tangut_coins.SOURCE_ID
    registered = {source.id: source for source in registry.load()}
    assert registered[tangut_coins.SOURCE_ID].name == "Tangut Coins"
    assert registered[tangut_coins.SOURCE_ID].licence is Licence.UNKNOWN
    assert (registered[tangut_coins.SOURCE_ID].attribution
            == "Andrew West, “Tangut Coins”, BabelStone Blog, 29 January 2007 (revised 1 January 2017)")
    coins = {coin["id"]: coin for coin in config["coins"]}
    assert len(coins) == config["counts"]["coin_types"] == 6
    assert len(config["images"]) == config["counts"]["photographs"] == 4
    assert all(image["kind"] == "object-photograph" for image in config["images"])
    assert "editorial_diagrams" not in config["counts"]
    assert config["checked"] == "2026-11-07"
    assert config["reading_order"] == "top-right-bottom-left"
    assert sum(1 for coin in config["coins"] if coin.get("example_url")) == 5
    assert any("white field" in note and "excluded" in note for note in config["notes"])
    for image in config["images"]:
        assert image["coin"] in coins
        assert image["image"].startswith("https://www.babelstone.co.uk/Tangut/Images/")
        assert len(image["sha256"]) == 64
        assert image["width"] > 0 and image["height"] > 0 and image["bytes"] > 0
        assert image["label"]
        assert image.get("linked_from_image")
    assert {coin["id"] for coin in config["coins"]} == {image["coin"] for image in config["images"]} | {"daan", "qianyou"}
    assert coins["fusheng"]["title"] == "福聖寶錢 / 稟德寶錢 (Fusheng / Bingde)"


def test_the_real_record_composes_without_network(tmp_path):
    source = ROOT / "data/sources/babelstone-tangut-coins.yaml"
    config = yaml.safe_load(source.read_text(encoding="utf-8"))
    fetched = {
        image["image"]: record(image["image"], image["sha256"], image["width"], image["height"], image["bytes"])
        for image in config["images"]
    }
    out = tmp_path / "coins"
    summary = tangut_coins.collect(source, out, fetch=lambda url, *, root=None: fetched[url])
    assert (summary["documents"], summary["pages"], summary["photographs"]) == (4, 4, 4)
    assert summary["skipped"] == ["daan", "qianyou"]
    documents = {document.id: document for document in tables.read(out / "documents.parquet", Document)}
    pages = list(tables.read(out / "pages.parquet", Page))
    assert set(documents) == {f"babelstone-tangut-coins:{coin}" for coin in ("fusheng", "zhenguan", "zhengde", "tianqing")}
    assert all(document.image_kind == "object-photograph" for document in documents.values())
    assert all(page.image_kind == "object-photograph" for page in pages)
    assert all(document.image_rights.licence is Licence.UNKNOWN for document in documents.values())
