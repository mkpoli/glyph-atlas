"""Tests of `scripts/build_ids_parts.py`: which components become parts, and how they are classed."""

from __future__ import annotations

import gzip
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


def _kanjivg(tmp_path, entries: dict[str, str]) -> Path:
    """A KanjiVG release file of `entries`: char → the inner XML of its <kanji> element."""
    ns = 'xmlns:kvg="http://kanjivg.tagaini.net"'
    body = "".join(
        f'<kanji id="kvg:kanji_{ord(char):05x}">{inner}</kanji>' for char, inner in entries.items()
    )
    path = tmp_path / "kanjivg.xml.gz"
    path.write_bytes(gzip.compress(f"<kanjivg {ns}>{body}</kanjivg>".encode()))
    return path


def _path(kind: str) -> str:
    return f'<path kvg:type="{kind}" d="M0,0"/>'


def test_first_strokes_need_three_witnesses_and_ninety_percent(tmp_path):
    dot, vertical, mouth = _path("㇔"), _path("㇑"), f'<g kvg:element="口">{_path("㇑")}</g>'
    path = _kanjivg(
        tmp_path,
        {
            # 𠂇 starts with 一 in its own entry, 左 and 友, and with 丿 in 右, 有 and 布.
            "𠂇": _path("㇐") + _path("㇒"),
            **{char: _path("㇐") + _path("㇒") for char in "左友"},
            **{char: _path("㇒") + _path("㇐") for char in "右有布"},
            # 口: its own entry, eight groups and the host 叫 start with a vertical; one group starts
            # with a dot, and a variant group, which is no witness, with another.
            "口": vertical + _path("㇕"),
            "品": mouth * 3,
            "器": mouth * 4,
            "呂": mouth,
            "叫": vertical,
            "擡": f'<g kvg:element="口">{dot}</g>',
            "簔": f'<g kvg:element="口" kvg:variant="true">{dot}</g>',
            # 亠: its own first stroke is a suffixed type, so only the hosts 交 and 亡 witness it.
            "亠": _path("㇑a") + _path("㇐"),
            "交": dot,
            "亡": dot,
        },
    )
    ids = {
        **{char: [f"⿸𠂇{rest}"] for char, rest in zip("左友右有布", "工又口月巾", strict=True)},
        "叫": ["⿰口丩"],
        "交": ["⿱亠父"],
        "亡": ["⿱亠𠃊"],
    }
    agreed = build.agreement(build.kanjivg_witnesses(path, ids))
    assert agreed["𠂇"] == (None, 3, 6)
    assert agreed["口"] == ("豎", 10, 11)
    assert agreed["亠"] == (None, 2, 2)
    first = build.kanjivg_first(path, ids)
    assert "𠂇" not in first and "亠" not in first and first["口"] == "豎"
