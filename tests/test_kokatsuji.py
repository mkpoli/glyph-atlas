"""Tests of the 古活字データセット importer.

The fixture is a small archive built in `tmp_path`: two corrected page images, and six blocks
covering the branches the importer has — one kana whose 字母 names one code point (は from 八), one
whose 字母 names several (な from 奈, three code points in the reference table), a 連彫活字 of three
characters, a 連彫活字 whose 字母 column is shorter than its characters, a kanji, and a kana whose
字母 no reference table carries (と from 止). Two blocks are written out of `count` order, so the
line text and the row order are not the same thing. The image cache is pointed at `tmp_path`
through `GLYPH_ATLAS_CACHE`, so nothing is read from `cache/` and no request is made.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from glyph_atlas import images, refs, tables
from glyph_atlas.importers import kokatsuji
from glyph_atlas.schema import Classification, Production, Register, Script, UnitKind

PAGE_SIZE = (130, 200)
COLUMNS = [*kokatsuji.FIELDS, "old_ID", "OCR"]

#: ID, character, jibo, block, page, x1, y1, x2, y2, line, count, old_ID, OCR
ROWS = [
    ["1", "は", "八", "001_001_2_01_02_000001.jpg", "001_001_2.jpg", 10, 10, 30, 40, 1, 2, "10", "は"],
    ["2", "な", "奈", "001_001_2_01_01_000002.jpg", "001_001_2.jpg", 10, 50, 30, 80, 1, 1, "11", "な"],
    ["3", "つれ〱", "徒連〱", "001_001_2_02_01_000003.jpg", "001_001_2.jpg", 50, 10, 110, 80, 2, 1, "12", "つれ〱"],
    ["4", "まこと", "末ヿ", "001_001_2_02_02_000004.jpg", "001_001_2.jpg", 50, 90, 110, 160, 2, 2, "13", "まこと"],
    ["6", "と", "止", "001_001_1_01_02_000005.jpg", "001_001_1.jpg", 40, 10, 60, 40, 1, 2, "14", "と"],
    ["5", "行", "行", "001_001_1_01_01_000006.jpg", "001_001_1.jpg", 10, 10, 30, 40, 1, 1, "15", "行"],
]


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the image cache at `tmp_path`, so no test reads or writes `cache/`."""
    root = tmp_path / "cache"
    monkeypatch.setenv(images.ENV_CACHE, str(root))
    return root


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    """The fixture archive: two corrected page images, six crops and `dataset.csv`."""
    path = tmp_path / "001.zip"
    with zipfile.ZipFile(path, "w") as z:
        for stem in ("001_001_1", "001_001_2"):
            z.writestr(f"001/page/{stem}.jpg", jpeg())
        for row in ROWS:
            z.writestr(f"001/block/{row[3]}", jpeg((8, 12)))
        z.writestr("001/dataset.csv", csv_text(ROWS, COLUMNS))
        z.writestr("001/README", "古活字データセット\n")
    return path


def jpeg(size: tuple[int, int] = PAGE_SIZE) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def csv_text(rows: list[list], columns: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return buffer.getvalue()


def fixture_records(tmp_path: Path, archive: Path):
    """Read the fixture archive into records, with the page images under `tmp_path/out/images`."""
    with pytest.warns(UserWarning, match="jibo"):
        return kokatsuji.read(archive, pages_dir=tmp_path / "out" / "images")


def test_document_carries_the_rights_of_the_source_file(tmp_path, cache, archive):
    document, _, _, _ = fixture_records(tmp_path, archive)
    source = kokatsuji.source_file()
    assert document.id == "codh-omt:001"
    assert document.title == "徒然草 2巻"
    assert document.holder == "国立国会図書館"
    assert document.production is Production.MOVABLE_TYPE
    assert document.text_register is Register.WABUN
    assert document.source_refs == {"codh-kokatsuji": "001", "ndl-pid": "2544701"}
    assert [(d.literal, d.start, d.end, d.kind) for d in document.dating] == [
        ("慶長・元和年間", 1596, 1624, "publication"),
    ]
    assert document.image_rights.licence.value == "CC-BY-4.0"
    assert document.image_rights.attribution == source["attribution"]
    assert document.image_rights.evidence == source["licence_evidence"]
    assert document.image_rights.holder == "国立国会図書館"
    assert document.text_rights == document.image_rights


def test_pages_are_registered_in_the_image_cache(tmp_path, cache, archive):
    document, pages, _, _ = fixture_records(tmp_path, archive)
    assert [page.id for page in pages] == ["codh-omt:001:001_001_1", "codh-omt:001:001_001_2"]
    assert [page.seq for page in pages] == [1, 2]
    for page in pages:
        stem = page.id.rsplit(":", 1)[1]
        extracted = tmp_path / "out" / "images" / f"{stem}.jpg"
        assert page.image == f"file:{extracted.as_posix()}"
        assert (page.width, page.height) == PAGE_SIZE
        assert page.sha256 == hashlib.sha256(extracted.read_bytes()).hexdigest()
        assert images.path_for(page.image) is not None
        assert page.document_id == document.id
        assert page.meta == {"spread": "codh-omt:001:001_001", "half": int(stem[-1])}


def test_a_char_block_takes_its_code_point_from_its_jibo(tmp_path, cache, archive):
    _, _, _, units = fixture_records(tmp_path, archive)
    by_id = {unit.id: unit for unit in units}
    assert sorted(by_id) == [f"codh-omt:001:{number}" for number in range(1, 7)]

    single = by_id["codh-omt:001:1"]  # は from 八: one code point carries that 字母
    assert single.granularity == "char" and single.classification is Classification.IDENTIFIED
    assert single.unicode == "U+1B09E" and refs.jibo_of_unit(single.unicode) == "八"
    assert single.script is Script.HENTAIGANA and single.kind is UnitKind.CHAR
    assert [(c.unicode, c.p) for c in single.candidates] == [("U+1B09E", 1.0)]
    assert refs.character("U+1B09E").jibo == ["八"]

    several = by_id["codh-omt:001:2"]  # な from 奈: three code points, all equally likely
    assert several.classification is Classification.AMBIGUOUS and several.unicode is None
    assert several.script is Script.HENTAIGANA
    assert [c.unicode for c in several.candidates] == ["U+1B080", "U+1B081", "U+1B082"]
    assert len({c.p for c in several.candidates}) == 1
    assert sum(c.p for c in several.candidates) == pytest.approx(1.0)

    unmatched = by_id["codh-omt:001:6"]  # と from 止: no code point of the table carries it
    assert unmatched.classification is Classification.UNASSESSED and unmatched.unicode == "U+3068"
    assert unmatched.candidates == [] and unmatched.script is Script.HIRAGANA
    # 止 is the 字母 the source states for と; no code point of the layer carries it, so the unit
    # names the transcribed code point and the upstream sequence keeps what the source wrote.
    assert unmatched.upstream["jibo_sequence"] == "止"
    assert refs.jibo_of_unit(unmatched.unicode) is None

    kanji = by_id["codh-omt:001:5"]
    assert kanji.classification is Classification.IDENTIFIED and kanji.unicode == "U+884C"
    assert refs.jibo_of_unit(kanji.unicode) is None and kanji.script is Script.HAN


def test_a_renji_block_keeps_its_aligned_jibo_sequence(tmp_path, cache, archive):
    document, _, _, units = fixture_records(tmp_path, archive)
    by_id = {unit.id: unit for unit in units}

    block = by_id["codh-omt:001:3"]  # 連彫活字 of three characters
    assert block.granularity == "block" and block.kind is UnitKind.LIGATURE
    assert block.classification is Classification.UNASSESSED
    assert block.text_source == block.reading == "つれ〱"
    assert block.unicode == "U+3064 U+308C U+3031" and block.candidates == []
    assert block.upstream == {
        "source": "codh-kokatsuji",
        "ref": "3",
        "block": "001_001_2_02_01_000003.jpg",
        "count": "1",
        "jibo_sequence": "徒連〱",
    }

    short = by_id["codh-omt:001:4"]  # まこと: four characters, three 字母, kept as given
    assert short.granularity == "block" and short.text_source == "まこと"
    assert short.upstream["jibo_sequence"] == "末ヿ"

    for unit in units:
        assert unit.document_id == document.id and unit.active and unit.method == "import"
        assert unit.review.value == "transcriber"
        assert unit.box is not None and unit.box.w > 0 and unit.box.h > 0


def test_units_carry_their_box_line_and_count(tmp_path, cache, archive):
    _, pages, _, units = fixture_records(tmp_path, archive)
    by_id = {unit.id: unit for unit in units}
    first = by_id["codh-omt:001:1"]
    assert first.box.iiif_region() == "10,10,20,30"
    # `seq` counts from zero along the line; the 1-based upstream `count` stays in `upstream`.
    assert first.seq == 1 and first.upstream["count"] == "2"
    assert first.line_id == "codh-omt:001:001_001_2:L1"
    assert first.page_id == "codh-omt:001:001_001_2"
    assert {unit.seq for unit in units if unit.line_id == first.line_id} == {0, 1}
    assert {unit.page_id for unit in units} == {page.id for page in pages}
    assert by_id["codh-omt:001:4"].box.iiif_region() == "50,90,60,70"


def test_lines_hold_their_blocks_in_count_order(tmp_path, cache, archive):
    _, _, lines, _ = fixture_records(tmp_path, archive)
    by_id = {line.id: line for line in lines}
    assert sorted(by_id) == [
        "codh-omt:001:001_001_1:L1",
        "codh-omt:001:001_001_2:L1",
        "codh-omt:001:001_001_2:L2",
    ]
    # the CSV lists は before な and と before 行; `count` puts them the other way round.
    assert by_id["codh-omt:001:001_001_2:L1"].text == "なは"
    assert by_id["codh-omt:001:001_001_2:L1"].box.iiif_region() == "10,10,20,70"
    assert by_id["codh-omt:001:001_001_2:L2"].text == "つれ〱まこと"
    assert by_id["codh-omt:001:001_001_2:L2"].box.iiif_region() == "50,10,60,150"
    assert by_id["codh-omt:001:001_001_1:L1"].text == "行と"
    assert by_id["codh-omt:001:001_001_1:L1"].box.iiif_region() == "10,10,50,30"
    assert by_id["codh-omt:001:001_001_1:L1"].seq == 1 and by_id["codh-omt:001:001_001_2:L2"].seq == 2
    assert all(line.text_raw == line.text and line.vertical for line in lines)
    assert by_id["codh-omt:001:001_001_2:L1"].page_id == "codh-omt:001:001_001_2"


def test_import_all_writes_the_tables(tmp_path, cache, archive):
    out = tmp_path / "work" / "kokatsuji"
    with pytest.warns(UserWarning, match="jibo"):
        counts = kokatsuji.import_all(out, zip_path=archive)
    assert counts == {"documents": 1, "pages": 2, "lines": 3, "units": 6}
    dataset = tables.Dataset(out)
    assert dataset.validate() == []
    assert len(dataset.read("units")) == 6
    assert len(dataset.read("lines")) == 3
    manifest = json.loads((out / tables.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["tables"] == counts
    assert manifest["command"] == f"atlas import kokatsuji --zip {archive}"
    assert len(images.index()) == 2


def test_a_second_import_writes_the_same_tables(tmp_path, cache, archive):
    out = tmp_path / "kokatsuji"
    with pytest.warns(UserWarning):
        first = kokatsuji.import_all(out, zip_path=archive)
    written = {name: (out / f"{name}.parquet").read_bytes() for name in first}
    with pytest.warns(UserWarning):
        second = kokatsuji.import_all(out, zip_path=archive)
    assert first == second
    assert {name: (out / f"{name}.parquet").read_bytes() for name in second} == written


def test_import_reports_a_missing_archive(tmp_path, cache):
    with pytest.raises(FileNotFoundError, match="古活字データセット"):
        kokatsuji.import_all(tmp_path / "out", zip_path=tmp_path / "001.zip")


def test_a_kana_jibo_never_resolves_to_an_alternate_katakana():
    """ね from 子 is the hentaigana U+1B098; 𛄧 shares the 字母 but is a katakana, not what was read."""
    labels = kokatsuji.labels_of("ね", "子")
    assert labels.classification is Classification.IDENTIFIED
    assert labels.unicode == "U+1B098" and labels.script is Script.HENTAIGANA
