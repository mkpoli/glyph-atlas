"""Tests of `scripts/build_mj_kanji_table.py` and of the `variants` overlay it feeds.

`build()` is tested against a tiny synthetic sheet, never the real 58,862-row workbook, so a rule is
checked against rows the real table may not exercise (a withdrawn 対応するUCS, several MJ figures on
one code point). The overlay in `refs._characters()` is tested the same way, against a synthetic
`mj-kanji.tsv` under a temporary vocabulary directory.
"""

from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

from glyph_atlas import refs
from glyph_atlas.schema import VariantRef

ROOT = Path(__file__).resolve().parents[1]
BUILT_BY = ROOT / "scripts" / "build_mj_kanji_table.py"
TABLE = ROOT / "data" / "vocab" / "mj-kanji.tsv"


def _module():
    spec = importlib.util.spec_from_file_location("build_mj_kanji_table", BUILT_BY)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_mj_kanji_table"] = module
    spec.loader.exec_module(module)
    return module


#: the workbook's header, as `data/sources/mj-kanji.yaml` pins it.
COLUMNS = [
    "図形ズケイ", "font", "MJ文字図形名", "対応するUCS", "実装したUCS",
    "実装したMoji_JohoコレクションIVS", "実装したSVS", "戸籍統一文字番号",
    "住基ネット統一文字コード", "入管正字コード", "入管外字コード", "漢字施策",
    "対応する互換漢字", "X0213", "X0213 包摂連番", "X0213 包摂区分", "X0212",
    "MJ文字図形バージョン", "登記統一文字番号(参考)", "部首1(参考)", "内画数1(参考)",
    "部首2(参考)", "内画数2(参考)", "部首3(参考)", "内画数3(参考)", "部首4(参考)",
    "内画数4(参考)", "総画数(参考)", "読み(参考)", "大漢和", "日本語漢字辞典",
    "新大字典", "大字源", "大漢語林", "更新履歴", "備考",
]


def sheet_row(mj: str, code_point: str = "", implemented: str = "", ivs: str = "") -> list[str]:
    """A row shaped like the real sheet: MJ図形名, 対応するUCS, 実装したUCS and IVS, rest blank."""
    row = [""] * len(COLUMNS)
    row[2], row[3], row[4], row[5] = mj, code_point, implemented, ivs
    return row


def test_build_keeps_every_figure_of_a_shared_code_point():
    module = _module()
    source = {"format": {"columns": COLUMNS}}
    rows = [
        COLUMNS,
        sheet_row("MJ000022", "U+342A", "U+342A", "342A_E0103"),
        sheet_row("MJ000023", "U+342A", "", "342A_E0101"),
    ]
    records = module.build(source, rows)
    assert [row["mj"] for row in records] == ["MJ000022", "MJ000023"]
    assert all(row["code_point"] == "U+342A" for row in records)
    assert records[0]["implemented_code_point"] == "U+342A"
    assert records[1]["implemented_code_point"] == ""
    assert records[0]["ivs"] == "342A_E0103"


def test_build_keeps_a_withdrawn_figure_with_no_code_point():
    module = _module()
    source = {"format": {"columns": COLUMNS}}
    rows = [COLUMNS, sheet_row("MJ037229")]
    records = module.build(source, rows)
    assert records == [{"mj": "MJ037229", "code_point": "", "implemented_code_point": "", "ivs": ""}]


def test_build_rejects_a_sheet_whose_columns_moved():
    module = _module()
    source = {"format": {"columns": COLUMNS}}
    wrong_header = list(COLUMNS)
    wrong_header[2], wrong_header[3] = wrong_header[3], wrong_header[2]
    with pytest.raises(SystemExit, match="unexpected columns"):
        module.build(source, [wrong_header])


def test_sheet_rows_reads_sparse_cells_by_their_own_column_reference(tmp_path):
    """A row that skips a blank cell, as Strict Open XML sheets do, is still read at the right column.

    The fixture is a two-column sheet whose row omits column A, and a wide row that reaches column
    AJ (the real sheet's last column, 36th), to check the multi-letter column reference too.
    """
    module = _module()
    workbook = tmp_path / "tiny.xlsx"
    shared_strings = (
        '<?xml version="1.0"?><sst xmlns="http://purl.oclc.org/ooxml/spreadsheetml/main" count="2" '
        'uniqueCount="2"><si><t>MJ000001</t></si><si><t>far</t></si></sst>'
    )
    sheet = (
        '<?xml version="1.0"?><worksheet xmlns="http://purl.oclc.org/ooxml/spreadsheetml/main">'
        "<sheetData>"
        '<row r="1"><c r="B1" t="s"><v>0</v></c></row>'
        '<row r="2"><c r="AJ2" t="s"><v>1</v></c></row>'
        "</sheetData></worksheet>"
    )
    with zipfile.ZipFile(workbook, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared_strings)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)

    rows = module.sheet_rows(workbook)
    assert rows[0] == [None, "MJ000001"]
    assert len(rows[1]) == 36 and rows[1][-1] == "far"


def test_the_generated_table_matches_its_pinned_counts():
    """The committed table is rebuilt by the same script; its header states the counts it printed."""
    header = "\n".join(line for line in TABLE.read_text(encoding="utf-8").splitlines() if line.startswith("#"))
    assert "rows: 58862, with a code point: 58859" in header
    assert "code points with several figures: 5072" in header
    assert "CC-BY-SA-2.1-JP" in header


# The overlay --------------------------------------------------------------------------------------


CHARACTERS_HEADER = "code_point\tchar\tname\talias\tscript\tcategory\tage\tblock\tjibo\treadings\tgrapheme\tconfusables\n"


def _characters_row(code_point: str, char: str) -> str:
    return f"{code_point}\t{char}\t\t\than\tLo\t1.1\tCJK Unified Ideographs\t\t\t{code_point}\t\n"


@pytest.fixture(autouse=True)
def fresh_cache():
    refs.clear_cache()
    yield
    refs.clear_cache()


def test_a_character_with_no_mj_kanji_table_has_no_variants(tmp_path, monkeypatch):
    monkeypatch.setattr(refs, "VOCAB", tmp_path)
    (tmp_path / "characters.tsv").write_text(
        CHARACTERS_HEADER + _characters_row("U+342A", "㐪"), encoding="utf-8"
    )
    assert refs.character("U+342A").variants == []


def test_a_code_point_with_several_mj_figures_gets_all_of_them(tmp_path, monkeypatch):
    monkeypatch.setattr(refs, "VOCAB", tmp_path)
    (tmp_path / "characters.tsv").write_text(
        CHARACTERS_HEADER + _characters_row("U+342A", "㐪") + _characters_row("U+3005", "々"),
        encoding="utf-8",
    )
    (tmp_path / "mj-kanji.tsv").write_text(
        "# MJ文字情報一覧表 Ver.006.02\n"
        "mj\tcode_point\timplemented_code_point\tivs\n"
        "MJ000023\tU+342A\t\t342A_E0101\n"
        "MJ000022\tU+342A\tU+342A\t342A_E0103\n"
        "MJ000001\tU+3005\tU+3005\t\n",
        encoding="utf-8",
    )
    row = refs.character("U+342A")
    assert row.variants == [
        VariantRef(scheme="mj", id="MJ000022", version="006.02"),
        VariantRef(scheme="mj", id="MJ000023", version="006.02"),
    ]
    assert refs.character("U+3005").variants == [VariantRef(scheme="mj", id="MJ000001", version="006.02")]


def test_a_withdrawn_figure_with_no_code_point_maps_to_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(refs, "VOCAB", tmp_path)
    (tmp_path / "characters.tsv").write_text(
        CHARACTERS_HEADER + _characters_row("U+342A", "㐪"), encoding="utf-8"
    )
    (tmp_path / "mj-kanji.tsv").write_text(
        "mj\tcode_point\timplemented_code_point\tivs\nMJ037229\t\t\t\n",
        encoding="utf-8",
    )
    assert refs.character("U+342A").variants == []
