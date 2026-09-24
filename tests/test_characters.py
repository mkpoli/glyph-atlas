"""Tests of the character layer: `characters.tsv`, the table that knows what a character is.

Three layers, three questions, and this module tests the middle one. A grapheme is one shape as the
writing system distinguishes shapes; a character is one encoded identity, named by a code point; the
字母 is metadata on a character. `data/vocab/characters.tsv` is built from the cached UCD files by
`scripts/build_character_table.py`, and the tests below build a second, tiny release from fixtures,
so that a rule is tested against characters the real table may never meet.

The Unicode 18.0 additions are the reason the layer exists, so they are tested twice: once as data
in the generated table, and once as a rule in the fixture build.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

from glyph_atlas import refs

ROOT = Path(__file__).resolve().parents[1]
VOCAB = ROOT / "data" / "vocab"
TABLE = VOCAB / "characters.tsv"
MJ_TABLE = VOCAB / "mj-hentaigana.tsv"
BUILT_BY = ROOT / "scripts" / "build_character_table.py"
RELEASE = "18.0.0"

#: The characters Unicode 18.0 added to Kana Extended-A and Small Kana Extension.
NEW = {
    "U+1B123": ("HIRAGANA DIGRAPH KOTO", "hiragana"),
    "U+1B124": ("KATAKANA DIGRAPH TOKI", "katakana"),
    "U+1B125": ("KATAKANA DIGRAPH TOTE", "katakana"),
    "U+1B126": ("KATAKANA DIGRAPH YORI", "katakana"),
    "U+1B127": ("KATAKANA LETTER ALTERNATE NE", "katakana"),
    "U+1B128": ("KATAKANA LETTER ALTERNATE WI", "katakana"),
    "U+1B168": ("KATAKANA LETTER SMALL ARCHAIC YE", "katakana"),
}


def rows_of(path: Path) -> list[dict[str, str]]:
    """The data rows of a table."""
    with path.open(encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


@pytest.fixture(autouse=True)
def fresh_tables():
    """Every test reads the tables from disk, so one test cannot seed another."""
    refs.clear_cache()
    yield
    refs.clear_cache()


def table() -> dict[str, dict[str, str]]:
    return {row["code_point"]: row for row in rows_of(TABLE)}


# The table --------------------------------------------------------------------------------------


def test_the_table_is_one_row_per_code_point():
    rows = rows_of(TABLE)
    assert len(rows) == len({row["code_point"] for row in rows})
    assert all(row["code_point"].startswith("U+") and row["char"] for row in rows)
    for row in rows:
        assert len(row["char"]) == 1
        assert int(row["code_point"].removeprefix("U+"), 16) == ord(row["char"])


def test_the_table_holds_the_kana_and_the_kanji():
    rows = rows_of(TABLE)
    scripts = {row["script"] for row in rows}
    assert scripts == {"han", "hiragana", "hentaigana", "katakana", "symbol"}
    assert len([row for row in rows if row["script"] == "hentaigana"]) == 285
    assert len([row for row in rows if row["script"] == "han"]) > 90_000


def test_the_unicode_18_0_additions_are_in_the_table_with_their_age():
    rows = table()
    for code_point, (name, script) in NEW.items():
        row = rows[code_point]
        assert row["name"] == name and row["script"] == script
        assert row["age"] == "18.0" and row["block"] != ""


def test_the_alternate_ne_carries_the_letter_and_the_reading_the_proposal_states():
    """L2/25-151R §2: the alternate NE is derived from 子; the chart gives it no reading note."""
    row = refs.character("U+1B127")
    assert row.name == "KATAKANA LETTER ALTERNATE NE"
    assert row.jibo == ["子"] and row.readings == ["ね"]
    assert row.age == "18.0" and row.block == "Kana Extended-A" and row.script.value == "katakana"
    assert row.confusables == ["U+5B50"], "Unicode's confusables table pairs it with 子"


def test_the_alternate_wi_carries_the_letter_and_the_reading_the_proposal_states():
    row = refs.character("U+1B128")
    assert row.jibo == ["井"] and row.readings == ["ゐ"] and row.age == "18.0"
    assert row.confusables == ["U+4E95"]


def test_a_character_that_is_no_kana_is_its_own_grapheme():
    for code_point in ("U+5B50", "U+4E95", "U+2B81E"):  # 子, 井, an ideograph of Extension D
        assert refs.grapheme(code_point) == code_point, code_point


def test_the_table_keeps_the_derivation_note_unicode_writes_for_a_form():
    """`* derived from 5E74` in the chart is the 字母 of HENTAIGANA LETTER NE-1."""
    row = refs.character("U+1B092")
    assert row.jibo == ["年"] and row.readings == ["ね"]
    assert row.name == "HENTAIGANA LETTER NE-1"


def test_a_second_name_is_kept_only_where_it_differs_from_the_unicode_name():
    """MJ figures U+1B001 as E-1 and calls it 江; Unicode calls it ARCHAIC YE and gives it no 字母."""
    row = refs.character("U+1B001")
    assert row.name == "HIRAGANA LETTER ARCHAIC YE" and row.alias == "HENTAIGANA LETTER E-1"
    assert row.jibo == ["江"] and row.readings == ["𛀁", "え"]
    assert refs.character("U+1B092").alias is None, "MJ names it what Unicode already names it"


def test_every_hentaigana_of_the_kana_tables_is_a_form_of_a_grapheme():
    for row in table().values():
        if row["script"] != "hentaigana":
            continue
        assert row["grapheme"] and row["jibo"], row["code_point"]
        assert refs.character(row["grapheme"]) is not None


def test_every_hentaigana_hangs_under_a_modern_hiragana():
    """The 変体仮名 are forms of a hiragana of the modern block, one group per 音価.

    This is the check that a transcription review needs: a reader of い finds every form of い. It
    is also the check that catches a grapheme named for the wrong character, which is what happened
    while the 音価 map listed the archaic katakana 𛄠 before the hiragana.
    """
    rows = table()
    hentaigana = [row for row in rows.values() if row["script"] == "hentaigana"]
    assert len(hentaigana) == 285
    for row in hentaigana:
        grapheme = rows[row["grapheme"]]
        assert grapheme["script"] == "hiragana", row["code_point"]
        assert 0x3041 <= int(row["grapheme"].removeprefix("U+"), 16) <= 0x3096, row["code_point"]
    groups = {row["grapheme"] for row in hentaigana}
    assert len(groups) == 47, "one group per 音価 a hentaigana is written for"


def test_the_archaic_vowel_letters_are_forms_of_their_hiragana():
    """𛄠 is YI and reads 𛄠 and い, so it is a form of U+3044 and not the other way round."""
    for archaic, hiragana in (("U+1B120", "U+3044"), ("U+1B121", "U+3048"), ("U+1B122", "U+3046")):
        row = refs.character(archaic)
        assert row.readings == [row.char, refs.to_char(hiragana)], "an archaic letter reads as itself"
        assert refs.grapheme(archaic) == hiragana, "its own shape is not the grapheme it belongs to"
    group = set(refs.graphemes()["U+3044"])
    assert {"U+3044", "U+30A4", "U+1B006", "U+1B120"} <= group


def test_a_small_kana_is_not_a_form_of_the_kana_it_is_small():
    """っ is not つ. A small kana is a letter, so it is its own grapheme, as is its katakana."""
    pairs = {
        "U+3041": "U+30A1",  # ぁ ァ
        "U+3043": "U+30A3",  # ぃ ィ
        "U+3045": "U+30A5",  # ぅ ゥ
        "U+3047": "U+30A7",  # ぇ ェ
        "U+3049": "U+30A9",  # ぉ ォ
        "U+3063": "U+30C3",  # っ ッ
        "U+3083": "U+30E3",  # ゃ ャ
        "U+3085": "U+30E5",  # ゅ ュ
        "U+3087": "U+30E7",  # ょ ョ
        "U+308E": "U+30EE",  # ゎ ヮ
        "U+3095": "U+30F5",  # ゕ ヵ
        "U+3096": "U+30F6",  # ゖ ヶ
    }
    for hiragana, katakana in pairs.items():
        assert refs.grapheme(hiragana) == hiragana, hiragana
        assert refs.grapheme(katakana) == hiragana, f"the katakana {katakana} is a form of ぁ"
    # The small kana of the historic blocks are their own graphemes on both sides: nothing states
    # that 𛅤 is the katakana of 𛅐, and a guess is not a grapheme.
    for point in ("U+1B150", "U+1B151", "U+1B152", "U+1B164", "U+1B165", "U+1B166", "U+1B167"):
        assert refs.grapheme(point) == point, point
    assert refs.grapheme("U+1B168") == "U+1B168", "the small archaic YE is neither 𛀁 nor え"
    assert refs.grapheme("U+1B132") == "U+1B132" and refs.grapheme("U+1B155") == "U+1B155"


def test_no_hentaigana_is_filed_under_a_small_kana():
    for grapheme, members in refs.graphemes().items():
        kinds = {refs.character(member).name for member in members}
        if any(name and "HENTAIGANA" in name for name in kinds):
            assert not any(name and "SMALL" in name for name in kinds), grapheme


# The three layers -------------------------------------------------------------------------------


def test_three_code_points_one_grapheme():
    """ね, ネ and the alternate NE of Unicode 18.0 are three characters and one shape."""
    assert refs.grapheme("U+306D") == "U+306D"
    assert refs.grapheme("U+30CD") == "U+306D"
    assert refs.grapheme("U+1B127") == "U+306D"
    assert refs.character("U+1B127").code_point != refs.character("U+30CD").code_point
    assert refs.character("U+1B127").jibo == refs.character("U+1B098").jibo


def test_a_grapheme_lists_every_form_of_it():
    forms = refs.graphemes()["U+306D"]
    assert {"U+306D", "U+30CD", "U+1B127", "U+1B092", "U+1B098"} <= set(forms)
    assert all(refs.grapheme(code_point) == "U+306D" for code_point in forms)


def test_cited_kanji_families_keep_character_identity_separate():
    modern, old = refs.character("仮"), refs.character("假")
    assert modern.code_point == "U+4EEE" and old.code_point == "U+5047"
    assert modern.readings == old.readings == []
    assert refs.grapheme("仮") == refs.grapheme("假") == "U+4EEE"
    family = refs.grapheme_info("假")
    assert family["label"] == "仮 = 假"
    assert family["members"] == [{"code_point": "U+4EEE", "char": "仮"}, {"code_point": "U+5047", "char": "假"}]
    assert family["relation"] == "shinjitai-kyujitai"
    assert family["evidence"][0]["title"].startswith("文化庁")
    assert len(family["evidence"][0]["sha256"]) == 64
    assert refs.grapheme("国") == refs.grapheme("國") == "U+56FD"
    # Neither homophones nor broader alignment unifications define browsing families.
    assert refs.grapheme("加") != refs.grapheme("仮")
    assert len({refs.grapheme(char) for char in "弁辨辯瓣"}) == 4
    assert refs.grapheme("子") != refs.grapheme("𛄧")
    assert refs.grapheme("𪜈") not in {refs.grapheme("ト"), refs.grapheme("モ")}
    assert refs.grapheme_info("U+FFFF") is None


def test_every_declared_family_agrees_with_generated_character_table():
    document = yaml.safe_load((VOCAB / "graphemes.yaml").read_text())
    assert len(document["families"]) == 271
    for head, family in document["families"].items():
        points = [refs.to_code_point(char) for char in family["members"]]
        assert set(refs.graphemes()[head]) == set(points)
        assert all(refs.grapheme(point) == head for point in points)
        assert refs.grapheme_info(head)["evidence"]


def test_a_kanji_is_not_a_form_of_the_kana_it_is_confusable_with():
    """𛄧 and 子 are one shape to the eye and two characters, and neither is a form of the other."""
    assert refs.grapheme("U+5B50") == "U+5B50"
    assert refs.grapheme("U+1B127") == "U+306D"
    assert refs.character("U+1B127").jibo == ["子"]
    assert refs.character("U+5B50").jibo == []


def test_the_letter_is_read_from_the_character_the_unit_names():
    assert refs.jibo_of_unit("U+1B127") == "子"
    assert refs.jibo_of_unit("U+1B098") == "子"
    assert refs.jibo_of_unit("U+304B U+3099") is None  # が: neither code point carries one
    assert refs.jibo_of_unit(None) is None


def test_a_character_layer_absent_from_the_table_answers_nothing():
    for code_point in ("U+0041", "U+3005"):  # A, 々: outside the blocks the table covers
        assert refs.character(code_point) is None
        assert refs.grapheme(code_point) is None
        assert refs.readings(code_point) == []
        assert refs.jibo(code_point) is None


# The build --------------------------------------------------------------------------------------


def write_release(directory: Path) -> Path:
    """A tiny Unicode release that holds only the characters the build rules are tested on."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "Blocks.txt").write_text(
        "3040..309F; Hiragana\n30A0..30FF; Katakana\n4E00..9FFF; CJK Unified Ideographs\n"
        "1B000..1B0FF; Kana Supplement\n1B100..1B12F; Kana Extended-A\n",
        encoding="utf-8",
    )
    (directory / "Scripts.txt").write_text(
        "3041..3096 ; Hiragana # Lo\n3099..309A ; Inherited # Mn\n30A1..30FA ; Katakana # Lo\n"
        "4E00..9FFF ; Han # Lo\n1B001..1B11F ; Hiragana # Lo\n1B120..1B12F ; Katakana # Lo\n",
        encoding="utf-8",
    )
    (directory / "DerivedAge.txt").write_text(
        "3041..3096 ; 1.1 # Lo\n30A1..30FA ; 1.1 # Lo\n4E00..9FFF ; 1.1 # Lo\n"
        "1B001..1B11F ; 10.0 # Lo\n1B127..1B128 ; 18.0 # Lo\n",
        encoding="utf-8",
    )
    (directory / "UnicodeData.txt").write_text(
        "304B;HIRAGANA LETTER KA;Lo;0;L;;;;;N;;;;;\n"
        "306D;HIRAGANA LETTER NE;Lo;0;L;;;;;N;;;;;\n"
        "3099;COMBINING KATAKANA-HIRAGANA VOICED SOUND MARK;Mn;230;NSM;;;;;N;;;;;\n"
        "30CD;KATAKANA LETTER NE;Lo;0;L;;;;;N;;;;;\n"
        "5B50;CJK UNIFIED IDEOGRAPH-5B50;Lo;0;L;;;;;N;;;;;\n"
        "4E00;<CJK Ideograph, First>;Lo;0;L;;;;;N;;;;;\n"
        "9FFF;<CJK Ideograph, Last>;Lo;0;L;;;;;N;;;;;\n"
        "3041;HIRAGANA LETTER SMALL A;Lo;0;L;;;;;N;;;;;\n"
        "30A1;KATAKANA LETTER SMALL A;Lo;0;L;;;;;N;;;;;\n"
        "1B006;HENTAIGANA LETTER I-1;Lo;0;L;;;;;N;;;;;\n"
        "1B092;HENTAIGANA LETTER NE-1;Lo;0;L;;;;;N;;;;;\n"
        "1B098;HENTAIGANA LETTER NE-KO;Lo;0;L;;;;;N;;;;;\n"
        "1B120;KATAKANA LETTER ARCHAIC YI;Lo;0;L;;;;;N;;;;;\n"
        "1B127;KATAKANA LETTER ALTERNATE NE;Lo;0;L;;;;;N;;;;;\n"
        "1B128;KATAKANA LETTER ALTERNATE WI;Lo;0;L;;;;;N;;;;;\n",
        encoding="utf-8",
    )
    (directory / "NamesList.txt").write_text(
        "3041\tHIRAGANA LETTER SMALL A\n304B\tHIRAGANA LETTER KA\n306D\tHIRAGANA LETTER NE\n"
        "3099\tCOMBINING KATAKANA-HIRAGANA VOICED SOUND MARK\n30A1\tKATAKANA LETTER SMALL A\n"
        "30CD\tKATAKANA LETTER NE\n5B50\tCJK UNIFIED IDEOGRAPH-5B50\n"
        "1B006\tHENTAIGANA LETTER I-1\n\t* derived from 4EE5\n"
        "1B092\tHENTAIGANA LETTER NE-1\n\t* derived from 5E74\n"
        "1B098\tHENTAIGANA LETTER NE-KO\n\t* derived from 5B50\n"
        "1B127\tKATAKANA LETTER ALTERNATE NE\n1B128\tKATAKANA LETTER ALTERNATE WI\n",
        encoding="utf-8",
    )
    (directory / "confusables.txt").write_text(
        "# confusables.txt\n1B127 ;\t5B50 ;\tMA\t# ( 𛄧 → 子 ) KATAKANA LETTER ALTERNATE NE → CJK UNIFIED IDEOGRAPH-5B50\n",
        encoding="utf-8",
    )
    return directory


def build(directory: Path, vocab: Path):
    """Run the build script's `build` over a release directory and a vocabulary directory."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("build_character_table", BUILT_BY)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_character_table"] = module
    spec.loader.exec_module(module)
    return {ord(row.char): row for row in module.build(directory, vocab)}


@pytest.fixture
def tiny(tmp_path: Path) -> tuple[Path, Path]:
    """A release directory and a vocabulary directory holding only what the rules need."""
    release = write_release(tmp_path / "ucd")
    vocab = tmp_path / "vocab"
    vocab.mkdir()
    (vocab / "mj-hentaigana.tsv").write_text(
        "mj\tcode_point\tname\tjibo\tjibo_code_point\treadings\tkoseki\tgakujutsu\tninjal_url\tnote\n"
        "MJ090152\tU+1B092\tHENTAIGANA LETTER NE-1\t年\tU+5E74\tね\t\t\t\t\n"
        "MJ090151\tU+1B098\tHENTAIGANA LETTER NE-KO\t子\tU+5B50\tね/こ\t\t\t\t\n",
        encoding="utf-8",
    )
    (vocab / "graphemes.yaml").write_text(
        "kana:\n  U+306D: ね\n  U+304B: か\n  U+3044: い\n  U+1B120: い\n"
        "  U+3041: ぁ\n  U+30A1: ぁ\n"
        "characters:\n  U+1B127:\n    jibo: [子]\n  U+1B128:\n    jibo: [井]\n",
        encoding="utf-8",
    )
    return release, vocab


def test_the_build_reads_the_age_script_and_block_of_a_code_point(tiny):
    rows = build(*tiny)
    row = rows[0x1B127]
    assert (row.name, row.script, row.category, row.age, row.block) == (
        "KATAKANA LETTER ALTERNATE NE", "katakana", "Lo", "18.0", "Kana Extended-A"
    )
    assert rows[0x3099].script == "symbol", "a combining mark belongs to no one script"
    assert rows[0x5B50].script == "han", "Unicode calls the script of a kanji Han"


def add_fixture_family(vocab: Path, *, members=None, sources=None):
    path = vocab / "graphemes.yaml"
    document = yaml.safe_load(path.read_text())
    document["sources"] = {"official": {"title": "Official character table", "url": "https://example.org/table"}}
    document["families"] = {"U+4EEE": {"members": members or ["仮", "假"],
        "relation": "shinjitai-kyujitai", "sources": ["official"] if sources is None else sources}}
    path.write_text(yaml.safe_dump(document, allow_unicode=True))
    return path


def test_builder_applies_cited_families_without_changing_character_fields(tiny):
    release, vocab = tiny
    add_fixture_family(vocab)
    rows = build(release, vocab)
    assert rows[ord("仮")].grapheme == rows[ord("假")].grapheme == "U+4EEE"
    assert rows[ord("假")].char == "假" and rows[ord("假")].readings == []
    assert rows[ord("假")].script == "han"


@pytest.mark.parametrize("members,sources,error", [
    (["仮", "假"], [], "cited sources"),
    (["仮", "假"], ["missing"], "valid source"),
    (["假", "仮"], None, "representative"),
    (["仮", "仮"], None, "unique members"),
    (["仮", "假仮"], None, "single-character"),
])
def test_builder_rejects_unsupported_family_claims(tiny, members, sources, error):
    release, vocab = tiny
    add_fixture_family(vocab, members=members, sources=sources)
    with pytest.raises(ValueError, match=error):
        build(release, vocab)


def test_builder_rejects_overlapping_families(tiny):
    release, vocab = tiny
    path = add_fixture_family(vocab)
    document = yaml.safe_load(path.read_text())
    document["families"]["U+5047"] = {"members": ["假", "加"], "relation": "unsupported", "sources": ["official"]}
    path.write_text(yaml.safe_dump(document, allow_unicode=True))
    with pytest.raises(ValueError, match="overlapping families"):
        build(release, vocab)


def test_the_build_names_the_ideographs_of_a_range_and_not_the_holes(tiny):
    release, vocab = tiny
    rows = build(release, vocab)
    assert rows[0x5B50].name == "CJK UNIFIED IDEOGRAPH-5B50"
    assert rows[0x4E00].name == "CJK UNIFIED IDEOGRAPH-4E00", "a First/Last pair names its ends"
    assert rows[0x9FFE].name == "CJK UNIFIED IDEOGRAPH-9FFE", "an ideograph is named by its code point"
    assert rows[0x9FFE].category == "Lo", "the category comes from the range too"
    # A code point of the block that no First/Last range covers is not a character.
    (release / "UnicodeData.txt").write_text(
        (release / "UnicodeData.txt").read_text(encoding="utf-8").replace("9FFF;<CJK Ideograph, Last>", "9FFD;<CJK Ideograph, Last>"),
        encoding="utf-8",
    )
    narrowed = build(release, vocab)
    assert 0x9FFE not in narrowed and 0x9FFF not in narrowed and 0x9FFD in narrowed


def test_the_build_takes_the_letter_from_the_chart_note_then_from_the_curated_row(tiny):
    rows = build(*tiny)
    assert rows[0x1B092].jibo == ["年"], "the NamesList note is Unicode's own statement"
    assert rows[0x1B098].jibo == ["子"]
    assert rows[0x1B127].jibo == ["子"], "the curated row states what the chart does not"
    assert rows[0x1B128].jibo == ["井"]


def test_the_build_reads_the_kana_a_name_spells(tiny):
    rows = build(*tiny)
    assert rows[0x1B092].readings == ["ね"], "HENTAIGANA LETTER NE-1"
    assert rows[0x1B098].readings == ["ね", "こ"], "HENTAIGANA LETTER NE-KO reads both"
    assert rows[0x1B127].readings == ["ね"], "KATAKANA LETTER ALTERNATE NE"
    assert rows[0x1B128].readings == ["ゐ"], "KATAKANA LETTER ALTERNATE WI names the kana ゐ"
    assert rows[0x5B50].readings == [], "a kanji is not a kana"


def test_the_build_puts_the_forms_of_one_kana_in_one_grapheme(tiny):
    rows = build(*tiny)
    assert rows[0x306D].grapheme == "U+306D"
    assert rows[0x30CD].grapheme == "U+306D", "a katakana letter is a form of its hiragana"
    assert rows[0x1B092].grapheme == "U+306D", "the hentaigana of 年 is a form of ね"
    assert rows[0x1B127].grapheme == "U+306D", "the alternate NE is a form of ね"
    assert rows[0x5B50].grapheme == "U+5B50", "子 is a kanji: its own grapheme"


def test_the_build_names_a_grapheme_for_the_modern_hiragana(tiny):
    """Two characters of the 音価 map read い: the hiragana names the grapheme, not 𛄠."""
    rows = build(*tiny)
    assert rows[0x1B006].readings == ["い"], "HENTAIGANA LETTER I-1"
    assert rows[0x1B006].grapheme == "U+3044"
    assert rows[0x1B120].readings == ["𛄠", "い"], "KATAKANA LETTER ARCHAIC YI reads 𛄠 and い, not yi"
    assert rows[0x1B120].grapheme == "U+3044", "the historic letter is a form of the hiragana"


def test_the_build_files_a_small_kana_under_a_small_kana(tiny):
    """ァ and ぁ are one grapheme; ぁ and あ are not, and neither is ァ and あ."""
    rows = build(*tiny)
    assert rows[0x3041].grapheme == "U+3041"
    assert rows[0x30A1].grapheme == "U+3041", "the small katakana is a form of the small hiragana"
    assert 0x3042 not in rows, "the fixture states no full-size あ, so ァ cannot have reached one"


def test_the_build_refuses_a_small_kana_under_a_full_size_one(tiny):
    """The check the build runs: a small kana whose grapheme is not small fails the build.

    Nothing in the fixture release states such a grouping, so a curated row states one and the
    build refuses it. That is the mistake the check exists for: a `grapheme` row written by hand
    can say anything, and ァ under あ would make the review dialog offer あ as a form of ァ.
    """
    release, vocab = tiny
    (vocab / "graphemes.yaml").write_text(
        (vocab / "graphemes.yaml").read_text(encoding="utf-8")
        + "  U+30A1:\n    grapheme: U+30A2\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="wrong grapheme"):
        build(release, vocab)


def test_the_build_records_a_confusable_pair_on_both_characters(tiny):
    rows = build(*tiny)
    assert rows[0x1B127].confusables == ["U+5B50"]
    assert rows[0x5B50].confusables == ["U+1B127"], "the relation is symmetric for the reader"


def test_search_answers_the_five_things_a_reviewer_has():
    """A code point, the character, a reading, a 字母 and a name all find the same character.

    The review dialog needs one box, not five, so the query decides how it is read: `U+1B127`, 𛄧,
    ね and 子 all have to reach the alternate NE, and a reader who does not know the code point is
    the ordinary case rather than the exception.
    """
    for term in ("U+1B127", "𛄧", "ネ", "ね", "子", "KATAKANA LETTER ALTERNATE NE"):
        found = {row.code_point for row in refs.search(term)}
        assert "U+1B127" in found, f"{term!r} did not reach the alternate NE"


def test_a_reading_returns_every_form_in_the_order_a_reviewer_wants():
    """ね is written ten ways, the ordinary kana first and the katakana last.

    The order is `forms`', which is what every other caller of the layer already sees: a reviewer
    asking about a reading gets the kana a modern text would print before the hentaigana.
    """
    found = refs.search("ね")
    assert len(found) == 10
    assert found[0].code_point == "U+306D", "the ordinary hiragana leads"
    assert found[-1].code_point == "U+30CD", "the katakana comes last"
    assert {row.code_point for row in found} == set(refs.forms("ね"))


def test_the_character_searched_for_leads_the_answer():
    """Searching ネ leads with the katakana and searching 子 leads with the kanji.

    A reader who typed a character is asking about that character; the other forms of its grapheme
    are what the answer adds, not what it says first.
    """
    assert refs.search("ネ")[0].code_point == "U+30CD"
    assert refs.search("子")[0].code_point == "U+5B50"


def test_a_word_of_kana_is_answered_by_its_characters():
    """The layer records a 音価 per character, so a word is not a row and is not a dead end."""
    found = {row.code_point for row in refs.search("ねこ")}
    assert {"U+306D", "U+3053"} <= found, "each character of the word is answered"


def test_search_of_something_the_layer_does_not_hold_is_empty():
    """A query nothing matches answers `[]`, as `character` answers `None` rather than raising."""
    assert refs.search("zzz") == []
    assert refs.search("") == []
    assert refs.search("   ") == []


def test_a_name_search_can_be_asked_for_explicitly():
    """`like` searches a word of a name; the whole-name form is recognised on its own."""
    whole = refs.search("KATAKANA LETTER ALTERNATE NE")
    assert [row.code_point for row in whole] == ["U+1B127"]
    many = refs.search("", like="HENTAIGANA LETTER NE")
    assert many and all("HENTAIGANA LETTER NE" in (row.name or "") for row in many)


def test_search_respects_kana_forms_that_a_range_list_would_miss():
    """The reason the layer exists: 𛄧 is written for ね, and 子 is what it derives from.

    A search built on a hiragana range would answer ね with ね and stop.
    """
    ne = {row.code_point for row in refs.search("ね")}
    assert "U+1B127" in ne, "the Unicode 18.0 alternate NE is a form of ね"
    assert "U+1B098" in ne, "the hentaigana of 子 is a form of ね"
    assert refs.character("U+5B50").name == "CJK UNIFIED IDEOGRAPH-5B50"


def test_full_refresh_rebuilds_reading_and_jibo_reverse_lookups(monkeypatch):
    from functools import cache

    original = refs.character("U+1B127")
    rows = {original.code_point: original.model_copy(update={"jibo": ["古"], "readings": ["ふるい"]})}

    @cache
    def characters():
        return rows

    monkeypatch.setattr(refs, "_characters", characters)
    assert refs.derived("古") == [original.code_point]
    assert any(row.code_point == original.code_point for row in refs.search("ふるい"))
    rows[original.code_point] = original.model_copy(update={"jibo": ["今"], "readings": ["あたらしい"]})
    refs.clear_cache()
    assert refs.derived("古") == []
    assert refs.derived("今") == [original.code_point]
    assert refs.search("ふるい") == []
    assert any(row.code_point == original.code_point for row in refs.search("あたらしい"))
