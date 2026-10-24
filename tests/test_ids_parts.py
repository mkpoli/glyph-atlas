"""Tests of `scripts/build_ids_parts.py`: which components become parts, and how they are classed."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "ids-parts" / "han-ids.tsv"


def _module():
    spec = importlib.util.spec_from_file_location("build_ids_parts", ROOT / "scripts" / "build_ids_parts.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_ids_parts"] = module
    spec.loader.exec_module(module)
    return module


build = _module()
UNIHAN = {
    "kTotalStrokes": {
        "一": "1",
        "王": "4",
        "氵": "3",
        "工": "3",
        "元": "4",
        "二": "2",
        "儿": "2",
        "殳": "4",
        "𤣩": "4",
    },
    "kJoyoKanji": {"一": "", "王": "", "元": "", "二": "", "工": ""},
    "kTGH": {"儿": "2013:3501", "也": "2013:56"},
}


def _rows():
    ids = build.read_ids(FIXTURE)
    rows = build.palette(ids, atomic={"王"}, unihan=UNIHAN, first={"氵": "點", "王": "横"})
    return {
        row[0]: dict(zip(["part", "uses", "strokes", "derived", "first", "kind"], row, strict=True))
        for row in rows
    }


def test_parts_are_used_components_that_do_not_decompose():
    rows = _rows()
    # 王 has a compound sequence but hfhchan/ids leaves it whole; 一 丨 氵 乚 have no compound one.
    assert {"王", "一", "丨", "氵", "乚", "二", "儿", "殳", "𤣩", "⺼"} <= set(rows)
    # 元 splits into two parts that are not strokes; ？ and {12} are not components.
    assert not {"元", "？", "{", "1"} & set(rows)
    # 工 (⿱一⿱丨一) and 𠂉 (⿰㇒一) split into strokes only, 十 (⿻一丨) into a stroke and one part.
    # 土 is used only by 王, whose own sequence does not count, since hfhchan/ids keeps 王 whole.
    assert {"工", "十", "𠂉"} <= set(rows) and rows["𠂉"]["uses"] == 1 and "土" not in rows
    # 也's one compound sequence has an unknown component, so it stays a part.
    assert rows["也"]["uses"] == 1 and rows["也"]["kind"] == "c"
    assert rows["氵"]["uses"] == 2 and rows["一"]["uses"] == 4


def test_common_difficult_and_strokes():
    rows = _rows()
    assert rows["王"]["kind"] == "c" and rows["一"]["kind"] == "c"
    # 儿 sits at 3501 in the 通用规范汉字表, past the first level.
    assert rows["儿"]["kind"] == "d" and rows["氵"]["kind"] == "d"
    assert rows["㇐"]["kind"] == "s" and rows["㇐"]["uses"] == 0
    assert rows["㇐"]["first"] == "横" and rows["㇚"]["first"] == "豎" and rows["㇜"]["first"] == "折"
    assert rows["㇣"]["first"] is None


def test_much_used_components_are_parts(monkeypatch):
    monkeypatch.setattr(build, "USE_THRESHOLD", 1)
    assert "元" in _rows()


def test_joyo_pointers_count_both_forms():
    unihan = {"kJoyoKanji": {"一": "2010", "剥": "U+525D"}, "kTGH": {}}
    assert build.common_set(unihan) == {"一", "剥", "剝"}


def test_stroke_counts_are_unihan_derived_or_null():
    rows = _rows()
    assert (rows["王"]["strokes"], rows["王"]["derived"]) == (4, 0)
    assert (rows["㇐"]["strokes"], rows["㇐"]["derived"]) == (1, 1)
    # Unihan lacks 丨 here; its only sequence is itself, so nothing is derived.
    assert rows["丨"]["strokes"] is None and rows["⺼"]["strokes"] is None
    assert rows["氵"]["first"] == "點" and rows["丨"]["first"] is None


def test_derived_count_sums_a_compound_sequence():
    ids = {"甲": ["⿱一二"], "乙": ["⿰甲？"], "丙": ["⿰甲丁", "⿱甲一"]}
    counts = build.stroke_counts(["甲", "乙", "丙"], ids, {"一": "1", "二": "2"})
    # 丙's first sequence names 丁, which has no count; its second is used.
    assert counts == {"甲": (3, True), "乙": (None, False), "丙": (4, True)}


def test_kanjivg_types_map_to_five_classes():
    assert build.type_class("㇐") == "横" and build.type_class("㇀/㇐") == "横"
    assert build.type_class("㇔/㇏") == "點" and build.type_class("㇒/㇚") is None
    # A suffixed type names strokes of more than one class (㇑a is the dot of 亠 and the vertical of 山).
    assert build.type_class("㇑a") is None and build.type_class("㇐b/㇔") is None
    assert build.type_class("６") is None
