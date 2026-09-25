"""Tests for the 漢字字体規範史データセット (HNG) importer. The clone is built in `tmp_path`."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from urllib.parse import quote

import pytest
from PIL import Image

from glyph_atlas import tables
from glyph_atlas.importers import hng
from glyph_atlas.schema import Classification, Document, Licence, Production, Unit

JOU = "01_誠實論卷八（P.2179）"
SKJ = "80_十誦律巻四十六(開宝蔵)"
FIXED = ["見出し文字", "異体字", "統合ID", "大字典", "大漢和", "部首", "JIS文字", "JIS包摂", "UCS", "備考", "字体"]


def write_csv(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows(rows)


def bmp(path: Path, shade: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("L", (8, 8), shade).save(path, "BMP")


def build(clone: Path) -> Path:
    """Two sources: jou through the index, skj through its folder table.

    jou: 七 and two 字体 of 不, one crop that two rows claim (龜, which the card table confirms, and
    末), one cell whose crop is missing and one crop no cell names. skj: 伏, a card with two 字体
    and no letter, a key only the Daijiten table knows, and a 〓 with a y-suffixed key.
    """
    body = [
        ["七", "", "00004", "00004", "M00004", "001", "七", "", "4E03", "", "", "0001", "0", "1", "", "", ""],
        ["不", "", "00013", "00013", "M00013", "001", "不", "", "4E0D", "", "", "0002a", "2", "164", "", "", ""],
        ["不", "", "00013b", "00013", "M00013", "001", "不", "", "4E0D", "", "＊", "0002b", "2", "1", "", "", ""],
        ["龜", "亀", "14887", "14887", "M48847", "213", "龜", "", "9F9C", "", "", "0003", "0", "1", "", "", ""],
        ["末", "", "04712", "04712", "M14421", "075", "末", "", "672B", "", "", "0003", "0", "1", "", "", ""],
        ["丈", "", "00006", "00006", "M00011", "001", "丈", "", "4E08", "", "", "0004", "0", "1", "", "", ""],
        ["伏", "", "00217", "00217", "M00456", "009", "伏", "", "4F0F", "", "", "", "", "", "", "", ""],
        ["乞", "", "00108", "00108", "M00210", "005", "乞", "", "4E5E", "", "", "", "", "", "", "", ""],
        ["乞", "", "00108b", "00108", "M00210", "005", "乞", "", "4E5E", "", "＊", "", "", "", "", "", ""],
        ["鎣", "", "12592", "12592", "M40767", "167", "", "", "93A3", "", "", "", "", "", "", "", ""],
    ]
    labels = [""] * len(FIXED) + ["jou_P2179"] * 3 + ["khh_宝篋天理"] * 3
    headers = FIXED + ["代表字形ID", "字体数", "用例数"] * 2
    write_csv(clone / hng.INDEX, [labels, headers, *body])
    write_csv(clone / hng.DAIJITEN, [
        ["字種コード", "大字典番号", "部首番号", "文字", "x0208", "包摂・備考", "UCS", "大漢和番号"],
        ["06324", "06324", "085", "", "", "", "6E27", "M17772"],
    ])
    for index, name in enumerate(["jou0001.bmp", "jou0002a.bmp", "jou0002b.bmp", "jou0003.bmp", "jou0099.bmp"]):
        bmp(clone / JOU / "glyphs" / "BMP" / name, 20 * index)
    write_csv(clone / JOU / hng.CARD_TABLE, [["カード番号", "文字"], ["0003", "龜"]])
    write_csv(clone / SKJ / hng.FOLDER_INDEX, [
        ["統合ID", "文字", "カード番号", "字体数", "用例数", "部首"],
        ["00217", "伏", "0001", "0", "1", "009"],
        ["00108", "乞", "0002", "2", "5", "005"],
        ["06324", "06324", "0003", "0", "1", "085"],
        ["12592y", "〓", "0004", "0", "1", "167"],
    ])
    write_csv(clone / SKJ / hng.CARD_TABLE, [
        ["カード番号", "文字", "字体数", "用例数1", "用例数2"],
        ["0002", "乞", "2", "4", "1"],
    ])
    for index, name in enumerate(["0001.bmp", "0002a.bmp", "0002b.bmp", "0003.bmp", "0004.bmp"]):
        bmp(clone / SKJ / "glyphs" / "BMP" / name, 30 + 20 * index)
    return clone


@pytest.fixture
def imported(tmp_path: Path) -> tuple[dict[str, int], dict[str, Unit], dict[str, Document], Path]:
    clone = build(tmp_path / "clone")
    counts = hng.import_all(tmp_path / "out", clone=clone, sources=["jou", "skj"])
    units = {unit.id: unit for unit in tables.read(tmp_path / "out" / "units.parquet", Unit)}
    documents = {doc.id: doc for doc in tables.read(tmp_path / "out" / "documents.parquet", Document)}
    return counts, units, documents, clone


def test_counts(imported) -> None:
    counts, units, _, _ = imported
    assert counts["documents"] == 2
    assert counts["units"] == len(units) == 9
    assert counts["missing"] == 1  # 丈 names jou0004, which the folder lacks
    assert counts["unused"] == 1  # jou0099
    assert counts["conflicts"] == 1  # 末 claims 龜's crop
    assert counts["unlisted"] == 1  # khh has a column and no folder
    assert counts["unencoded"] == 1


def test_index_units(imported) -> None:
    _, units, _, clone = imported
    first = units["hng:jou:0001"]
    assert (first.unicode, first.text_source, first.classification) == ("U+4E03", "七", Classification.IDENTIFIED)
    assert first.document_id == "hng:jou" and first.page_id is None and first.box is None
    path = f"{JOU}/glyphs/BMP/jou0001.bmp"
    assert first.crop == f"https://raw.githubusercontent.com/chise/hng-basic-data/{hng.source_file()['revision']}/{quote(path)}"
    assert first.crop_sha256 == hashlib.sha256((clone / path).read_bytes()).hexdigest()
    second = units["hng:jou:0002b"]
    assert second.unicode == "U+4E0D"
    assert second.upstream["integrated_id"] == "00013b" and second.upstream["mark"] == "＊"
    assert units["hng:jou:0002a"].upstream["occurrences"] == "164"
    assert units["hng:jou:0003"].unicode == "U+9F9C"


def test_folder_units(imported) -> None:
    _, units, _, _ = imported
    assert units["hng:skj:0001"].unicode == "U+4F0F"
    assert {units[f"hng:skj:0002{x}"].unicode for x in "ab"} == {"U+4E5E"}
    forms = [units[f"hng:skj:0002{x}"].upstream for x in "ab"]
    assert [(f["integrated_id"], f["occurrences"]) for f in forms] == [("00108", "4"), ("00108b", "1")]
    assert (units["hng:skj:0003"].unicode, units["hng:skj:0003"].text_source) == ("U+6E27", "渧")
    unencoded = units["hng:skj:0004"]
    assert unencoded.unicode is None and unencoded.classification == Classification.UNENCODED


def test_documents(imported) -> None:
    _, _, documents, _ = imported
    jou = documents["hng:jou"]
    assert (jou.holder, jou.shelfmark, jou.production) == ("Bibliothèque nationale de France", "P.2179", Production.MANUSCRIPT)
    assert (jou.dating[0].literal, jou.dating[0].start, jou.dating[0].end) == ("514", 514, 514)
    assert jou.image_rights.licence == Licence.CC_BY_SA_4
    assert documents["hng:skj"].production == Production.WOODBLOCK


def test_every_listed_document_parses() -> None:
    raw = hng.source_file()
    documents = [hng.document_of(entry, raw) for entry in raw["documents"]]
    assert len(documents) == raw["counts"]["sources"] == 63
    assert all(doc.dating and doc.dating[0].start is not None for doc in documents)


@pytest.mark.parametrize(
    ("literal", "years"),
    [
        ("514", (514, 514)),
        ("754-755", (754, 755)),
        ("740頃", (730, 750)),
        ("970代", (970, 979)),
        ("10C", (901, 1000)),
        ("12C初", (1101, 1133)),
        ("7C末", (667, 700)),
        ("9-10C", (801, 1000)),
        ("初唐", (618, 712)),
        ("北宋期か", (960, 1127)),
        ("鎌倉書紀", (None, None)),
    ],
)
def test_interval(literal: str, years: tuple[int | None, int | None]) -> None:
    assert hng.interval(literal) == years


def test_production() -> None:
    assert hng.production_of("南北朝写本") == Production.MANUSCRIPT
    assert hng.production_of("韓国印刻本") == Production.WOODBLOCK
    assert hng.production_of("開成石経") == Production.UNKNOWN


def test_revision_mismatch(tmp_path: Path) -> None:
    clone = build(tmp_path / "clone")
    (clone / ".git").mkdir()
    (clone / ".git" / "HEAD").write_text("0" * 40 + "\n")
    with pytest.raises(hng.RevisionError):
        hng.import_all(tmp_path / "out", clone=clone, sources=["jou"])


@pytest.mark.parametrize(
    ("glyph", "forms", "present", "found"),
    [
        ("1010c", "3", {"hos1010ｃ.bmp"}, "hos1010ｃ.bmp"),
        ("0407a", "1", {"hos0407.bmp"}, "hos0407.bmp"),
        ("0309", "1", {"hos0309a.bmp"}, "hos0309a.bmp"),
        ("0960", "1", {"hos0960a.bmp", "hos0960b.bmp"}, None),
        ("0624a", "2", {"hos0624.bmp"}, None),
        ("0001", "0", {"0001.bmp"}, "0001.bmp"),
    ],
)
def test_crop_file(glyph: str, forms: str, present: set[str], found: str | None) -> None:
    assert hng.crop_file("hos", glyph, forms, present) == found


def test_crop_file_prefers_the_exact_name() -> None:
    assert hng.crop_file("myz", "0197", "1", {"myz019７.bmp", "myz0197.bmp"}) == "myz0197.bmp"
