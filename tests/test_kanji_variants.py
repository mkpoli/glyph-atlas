"""Tests of `scripts/build_kanji_variants.py` and of the table it writes.

Each reader is tested against a few synthetic lines in its upstream's format, so a rule is checked
on rows the real files may not exercise; the committed table is then checked for the invariants the
readers promise.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILT_BY = ROOT / "scripts" / "build_kanji_variants.py"
TABLE = ROOT / "data" / "vocab" / "kanji-variants.tsv"


def _module():
    spec = importlib.util.spec_from_file_location("build_kanji_variants", BUILT_BY)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_kanji_variants"] = module
    spec.loader.exec_module(module)
    return module


variants = _module()
Edge = variants.Edge


def rows(edges):
    return {(e.a, e.b, e.relation, e.source, e.detail) for e in edges}


def test_unihan_reads_old_and_traditional_fields_backwards():
    text = "# comment\nU+6548\tkJapaneseNewVariant\tU+52B9\nU+52B9\tkJapaneseOldVariant\tU+6548\nU+5023\tkSemanticVariant\tU+4EFF<kMatthews\nU+4ED3\tkTraditionalVariant\tU+5009\nU+5009\tkSimplifiedVariant\tU+4ED3 U+5009\nU+5009\tkRSUnicode\t9.8"
    assert rows(variants.unihan_edges(text)) == {
        ("效", "効", "shinjitai", "unihan", "效→効 kJapaneseNewVariant"),
        ("效", "効", "shinjitai", "unihan", "効→效 kJapaneseOldVariant"),
        ("仿", "倣", "semantic", "unihan", "倣→仿 kSemanticVariant<kMatthews"),
        ("倉", "仓", "simplified", "unihan", "仓→倉 kTraditionalVariant"),
        ("倉", "仓", "simplified", "unihan", "倉→仓 kSimplifiedVariant"),
    }


def test_compatibility_keeps_only_decompositions_to_one_unified_ideograph():
    text = "F900;CJK COMPATIBILITY IDEOGRAPH-F900;Lo;0;L;8C48;;;;N;;;;;\n2F00;KANGXI RADICAL ONE;So;0;ON;<compat> 4E00;;;;N;;;;;\n3280;CIRCLED IDEOGRAPH ONE;No;0;L;<circle> 4E00;;;1;N;;;;;\n0041;LATIN CAPITAL LETTER A;Lu;0;L;;;;;N;;;;0061;"
    assert {(e.a, e.b) for e in variants.compatibility_edges(text)} == {(chr(0xF900), "豈"), ("⼀", "一")}


def test_yitizi_csv_applies_its_exclusions_and_keeps_overlap_groups():
    text = "#字,全等,語義交疊,簡體,繁體\n仿,,倣髣,,\n蒙,懞,,,\n乾,干,,,\n傾,,,倾,\n"
    got = rows(variants.yitizi_edges("ytenx/JihThex.csv", text))
    assert ("仿", "倣", "overlap", "yitizi", "ytenx/JihThex.csv 仿倣髣") in got
    assert ("倣", "髣", "overlap", "yitizi", "ytenx/JihThex.csv 仿倣髣") in got
    assert ("傾", "倾", "simplified", "yitizi", "ytenx/JihThex.csv") in got
    assert not any("蒙" in (a, b) or {a, b} == {"乾", "干"} for a, b, *_ in got)


def test_yitizi_txt_reads_its_three_line_forms():
    text = "# 說明\n=來来 # 同\n閒閑\n艫>舮\n"
    assert rows(variants.yitizi_edges("yitizi.txt", text)) == {
        ("來", "来", "equivalent", "yitizi", "yitizi.txt 來来"),
        ("閑", "閒", "overlap", "yitizi", "yitizi.txt 閑閒"),
        ("艫", "舮", "simplified", "yitizi", "yitizi.txt"),
    }


def test_opencc_names_the_region_and_keeps_every_candidate():
    text = "# header\n僞\t偽\n峯\t峰 峯\n"
    assert rows(variants.opencc_edges("TWVariants.txt", text)) == {
        ("僞", "偽", "regional", "opencc", "TWVariants TW"),
        ("峯", "峰", "regional", "opencc", "TWVariants TW"),
    }


def test_opencc_reads_the_japanese_table_from_shinjitai_to_kyujitai():
    text = "# jp2t\n国\t國\n䀋\t䀋 鹽\n"
    assert rows(variants.opencc_edges("JPShinjitaiCharacters.txt", text)) == {
        ("國", "国", "shinjitai", "opencc", "JPShinjitaiCharacters"),
        ("鹽", "䀋", "shinjitai", "opencc", "JPShinjitaiCharacters (self first)"),
    }


def test_shrink_map_reads_each_table_from_the_figure_code_point():
    shrink = {
        "content": [
            {
                "MJ文字図形名": "MJ000328",
                "法務省告示582号別表第四": [
                    {"表": "一", "順位": "第1順位", "UCS": "U+4EFF"},
                    {"表": "一", "順位": "第2順位", "UCS": "U+65B9"},
                ],
            },
            {"MJ文字図形名": "MJ006557", "JIS包摂規準・UCS統合規則": [{"UCS": "U+4EFF"}]},
            {"MJ文字図形名": "MJ999999", "辞書類等による関連字": [{"UCS": "U+4EFF"}]},
        ]
    }
    figures = {"MJ000328": "㕫", "MJ006557": "仿"}
    assert rows(variants.shrink_map_edges(shrink, figures)) == {
        ("㕫", "仿", "reduction", "mj-shrink-map", "MJ000328: 法務省告示582号別表第四, 表 一, 順位 第1順位"),
        ("㕫", "方", "reduction", "mj-shrink-map", "MJ000328: 法務省告示582号別表第四, 表 一, 順位 第2順位"),
    }


def test_hng_relates_the_character_to_each_variant_in_its_column():
    text = "文字,異体字,統合ID\n世,丗,00025\n丁,,00002\n"
    assert rows(variants.hng_edges(text)) == {("世", "丗", "variant", "hng-basic-data", "00025")}


def test_wikidata_reads_specialized_semantic_and_keeps_the_rest_as_qids():
    def binding(**values):
        return {key: {"value": value} for key, value in values.items()}

    result = {
        "results": {
            "bindings": [
                binding(
                    s="http://www.wikidata.org/entity/statement/Q1-abc",
                    ac="傚",
                    bc="效",
                    stated="Q10427532",
                    kinds="Q126726325",
                ),
                binding(
                    s="http://www.wikidata.org/entity/statement/Q2-def",
                    ac="效",
                    bc="効",
                    stated="Q10427532",
                    kinds="Q59496158",
                    systems="Q1055887",
                ),
                binding(s="http://www.wikidata.org/entity/statement/Q3-ghi", ac="𠀀", bc="𠀀"),
            ]
        }
    }
    assert rows(variants.wikidata_edges(result)) == {
        ("傚", "效", "specialized-semantic", "wikidata", "Q1; P248 Q10427532"),
        ("効", "效", "variant", "wikidata", "Q2; P248 Q10427532; P1552 Q59496158; P282 Q1055887"),
    }


def test_merged_joins_the_details_of_one_claim_and_keeps_sources_apart():
    got = variants.merged(
        [
            Edge("効", "效", "variant", "wikidata", "Q2"),
            Edge("効", "效", "variant", "wikidata", "Q1"),
            Edge("効", "效", "variant", "hng-basic-data", "04251"),
        ]
    )
    assert [(e.source, e.detail) for e in got] == [("wikidata", "Q1 | Q2"), ("hng-basic-data", "04251")]


def test_the_committed_table_keeps_its_promises():
    lines = TABLE.read_text(encoding="utf-8").splitlines()
    header = [line for line in lines if line.startswith("#")]
    body = [line.split("\t") for line in lines if not line.startswith("#")]
    assert body[0] == variants.COLUMNS
    counts = dict(pair.rsplit(" ", 1) for pair in header[-1].removeprefix("# rows: ").split(", "))
    seen: dict[str, int] = {}
    keys = set()
    for a, b, relation, source, _detail in body[1:]:
        assert len(a) == len(b) == 1 and a != b
        assert relation in variants.RELATIONS and source in variants.SOURCE_IDS
        if relation in variants.SYMMETRIC:
            assert a < b
        assert (a, b, relation, source) not in keys
        keys.add((a, b, relation, source))
        seen[source] = seen.get(source, 0) + 1
    assert {source: int(count) for source, count in counts.items()} == seen
    for source in variants.SOURCE_IDS:
        assert (ROOT / "data" / "sources" / f"{source}.yaml").exists()
