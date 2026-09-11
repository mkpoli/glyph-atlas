"""Tests for the reference tables and the matching policies. No test reaches the network."""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from kuzushiji_atlas import refs

VOCAB = Path(__file__).resolve().parents[1] / "data" / "vocab"
MJ_TABLE = VOCAB / "mj-hentaigana.tsv"
EQUIVALENTS = VOCAB / "kanji-equivalents.tsv"


def rows_of(path: Path) -> list[dict[str, str]]:
    """The data rows of a table, with the `#` header comments dropped."""
    with path.open(encoding="utf-8") as handle:
        lines = [line for line in handle if not line.startswith("#")]
    return [dict(row) for row in csv.DictReader(lines, delimiter="\t")]


@pytest.fixture(autouse=True)
def fresh_tables():
    """Every test reads the tables from disk, so one test cannot seed another."""
    refs.clear_cache()
    yield
    refs.clear_cache()


def test_candidates_of_ka_start_with_the_ordinary_kana():
    candidates = refs.candidates("か")
    assert len(candidates) == 13
    assert candidates[0] == "U+304B"
    assert {"U+1B019", "U+1B01A", "U+1B022"} <= set(candidates)  # KA-3, KA-4, KA-KE


def test_candidates_are_in_code_point_order_after_the_ordinary_kana():
    candidates = refs.candidates("か")
    assert candidates[1:] == sorted(candidates[1:])


def test_candidates_include_shared_reading_letters():
    assert "U+1B022" in refs.candidates("け")  # KA-KE reads か and け


def test_candidates_map_katakana_through_hiragana():
    assert refs.candidates("カ") == refs.candidates("か")


def test_candidates_of_a_reading_no_table_has():
    assert refs.candidates("あき") == []
    assert refs.candidates("x") == []
    assert refs.candidates("") == []


def test_candidates_of_an_ordinary_kana_without_hentaigana():
    assert refs.candidates("ゔ") == ["U+3094"]


def test_jibo_and_readings_of_ne_ko():
    assert refs.jibo("U+1B098") == "子"
    assert refs.readings("U+1B098") == ["ね", "こ"]


def test_jibo_and_readings_accept_a_code_point_without_the_prefix():
    assert refs.jibo("1b098") == "子"
    assert refs.readings("1B098") == ["ね", "こ"]


def test_jibo_and_readings_of_an_unknown_code_point():
    assert refs.jibo("U+0041") is None
    assert refs.readings("U+0041") == []


def test_archaic_kana_have_a_row_without_a_hentaigana_name():
    rows = {row["code_point"]: row for row in refs.hentaigana()}
    ye, wu = rows["U+1B001"], rows["U+1B11F"]
    assert ye["unicode_name"] == "HIRAGANA LETTER ARCHAIC YE"
    assert ye["mj_name"] == "HENTAIGANA LETTER E-1"
    assert ye["jibo"] == "江"
    assert ye["readings"] == ["𛀁", "え"]
    assert wu["unicode_name"] == "HIRAGANA LETTER ARCHAIC WU"
    assert wu["mj"] is None
    assert wu["jibo"] == "汙"


def test_hentaigana_joins_both_tables_on_the_code_point():
    rows = refs.hentaigana()
    assert len(rows) == 287
    assert {row["code_point"] for row in rows} == {row["code_point"] for row in rows_of(VOCAB / "hentaigana.tsv")}
    assert [row["code_point"] for row in rows if row["mj"] is None] == ["U+1B11F"]
    ne_ko = next(row for row in rows if row["code_point"] == "U+1B098")
    assert ne_ko["mj"] == "MJ090151"
    assert ne_ko["readings"] == ["ね", "こ"]
    assert ne_ko["mj_readings"] == ["ね", "こ"]
    assert ne_ko["koseki"] is None
    assert ne_ko["gakujutsu"] == "240010010"
    assert ne_ko["ninjal_url"].startswith("https://cid.ninjal.ac.jp/kana/detail/")


def test_mj_table_has_299_rows_and_286_code_points():
    rows = rows_of(MJ_TABLE)
    assert len(rows) == 299
    assert sum(1 for row in rows if row["code_point"]) == 286
    unified = [row for row in rows if not row["code_point"]]
    assert len(unified) == 13
    assert all(re.fullmatch(r"MJ\d{6}へ統合", row["note"]) for row in unified)
    assert all(row["jibo"] and row["readings"] for row in rows)


def test_mj_table_header_names_source_version_and_licence():
    text = MJ_TABLE.read_text(encoding="utf-8")
    header = "\n".join(line for line in text.splitlines() if line.startswith("#"))
    assert "MJ文字情報一覧表 変体仮名編 Ver.002.01" in header
    assert "CC-BY-SA-2.1-JP" in header and "IPA" in header
    assert "音価１" in header and "変体仮名番号" in header


def test_equivalents_of_characters_that_share_a_reading():
    assert refs.same("か", "𛀙", "align-v1")
    assert refs.same("が", "𛀙", "align-v1")
    assert refs.same("え", "𛀁", "align-v1")
    assert not refs.same("か", "き", "align-v1")


def test_equivalents_keep_distinct_readings_distinct():
    assert not refs.same("あ", "を", "align-v1")  # 𛀅 reads both, あ and を do not
    assert refs.same("𛀅", "あ", "align-v1")
    assert refs.same("𛀅", "を", "align-v1")


def test_equivalents_of_kanji_under_align_v1():
    assert refs.same("国", "國", "align-v1")
    assert refs.same("高", "髙", "align-v1")
    assert refs.same("學", "学", "align-v1")
    assert "國" in refs.equivalents("国", "align-v1")


def test_equivalents_of_voiced_and_small_kana():
    assert refs.same("は", "ば", "align-v1")
    assert refs.same("は", "ぱ", "align-v1")
    assert refs.same("っ", "つ", "align-v1")
    assert refs.same("か", "カ", "align-v1")


def test_strict_keeps_only_compatibility():
    assert not refs.same("は", "ば", "strict")
    assert not refs.same("国", "國", "strict")
    assert not refs.same("か", "𛀙", "strict")
    assert refs.same("塚", "塚", "strict")
    assert refs.equivalents("国", "strict") == {"国"}


def test_same_is_reflexive_and_symmetric():
    assert refs.same("か", "か", "align-v1")
    assert refs.same("か", "𛀙", "align-v1") == refs.same("𛀙", "か", "align-v1")
    assert refs.same("は", "ば", "strict") == refs.same("ば", "は", "strict")


def test_same_of_strings_of_several_characters():
    assert refs.same("かな", "かな", "align-v1")
    assert not refs.same("かな", "カナ", "align-v1")


def test_equivalents_include_the_character_itself():
    assert refs.equivalents("か", "align-v1") >= {"か"}
    assert refs.equivalents("x", "align-v1") == {"x"}


def test_policies_compose_known_relations():
    align = refs.policy("align-v1")
    assert align["version"] == 1
    assert align["relations"] == [
        "kana-reading",
        "compatibility",
        "shinji-kyuji",
        "itaiji",
        "voicing",
        "small-kana",
    ]
    strict = refs.policy("strict")
    assert strict["relations"] == ["compatibility"]
    assert all(relation in refs.RELATIONS for relation in align["relations"] + strict["relations"])
    assert align["description"] and strict["description"]


def test_unknown_policy_names_the_file():
    with pytest.raises(KeyError, match="equivalence-policies"):
        refs.policy("align-v2")


def test_code_points_round_trip():
    assert refs.to_code_points("か𛀙") == ["U+304B", "U+1B019"]
    assert refs.from_code_points(["U+304B", "U+1B019"]) == "か𛀙"
    assert refs.from_code_points(refs.to_code_points("𛄟")) == "𛄟"


def test_equivalence_table_kinds_and_sources():
    rows = rows_of(EQUIVALENTS)
    kinds = {row["kind"] for row in rows}
    assert kinds == {"compatibility", "shinji-kyuji", "itaiji"}
    assert {(row["a"], row["b"]) for row in rows if row["kind"] == "compatibility"} >= {("塚", "塚")}
    assert {(row["a"], row["b"]) for row in rows if row["kind"] == "shinji-kyuji"} >= {("國", "国")}
    assert {(row["a"], row["b"]) for row in rows if row["kind"] == "itaiji"} >= {("高", "髙")}
    assert all(len(row["a"]) == 1 and len(row["b"]) == 1 and row["a"] != row["b"] for row in rows)


def test_every_equivalence_row_comes_from_a_source_with_a_stated_licence():
    lines = EQUIVALENTS.read_text(encoding="utf-8").splitlines()
    header = "\n".join(line for line in lines if line.startswith("#"))
    rows = [line.split("\t") for line in lines if not line.startswith("#")][1:]
    sources = {row[3] for row in rows}
    assert sources == {"unicode-ucd", "mj-shrink-map", "mj-main-table"}
    for source in sources:
        assert source in header
    assert "Unicode-3.0" in header
    assert "CC-BY-SA-2.1-JP" in header
    assert "by-sa/2.1/jp" in header


def test_equivalence_header_records_the_columns_that_were_read():
    header = "\n".join(
        line for line in EQUIVALENTS.read_text(encoding="utf-8").splitlines() if line.startswith("#")
    )
    assert "対応するUCS" in header
    assert "X0213" in header
    assert "漢字施策" in header
    assert "法務省戸籍法関連通達・通知" in header
    assert "種別" in header
    assert "Decomposition" in header


def test_a_missing_table_names_the_script_that_writes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(refs, "VOCAB", tmp_path)
    with pytest.raises(refs.MissingTable, match="build_hentaigana_table.py"):
        refs.readings("U+1B098")
    (tmp_path / "hentaigana.tsv").write_bytes((VOCAB / "hentaigana.tsv").read_bytes())
    with pytest.raises(refs.MissingTable, match="build_mj_table.py"):
        refs.readings("U+1B098")
    (tmp_path / "mj-hentaigana.tsv").write_bytes(MJ_TABLE.read_bytes())
    with pytest.raises(refs.MissingTable, match="equivalence-policies.yaml"):
        refs.policy("strict")
    (tmp_path / "equivalence-policies.yaml").write_bytes((VOCAB / "equivalence-policies.yaml").read_bytes())
    with pytest.raises(refs.MissingTable, match="build_kanji_equivalents.py"):
        refs.equivalents("国", "strict")
    (tmp_path / "kanji-equivalents.tsv").write_bytes(EQUIVALENTS.read_bytes())
    assert refs.same("国", "國", "align-v1")
