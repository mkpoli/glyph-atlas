"""The forms national standards prescribe, in the grapheme of their full form or apart from it (glyph_atlas.shape_families)."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from glyph_atlas import refs
from glyph_atlas.shape_families import Decision, Headwords, families, rulings, standard_forms

ROOT = Path(__file__).resolve().parents[1]
VOCAB = ROOT / "data" / "vocab"


def table(name: str) -> list[dict[str, str]]:
    lines = [line for line in (VOCAB / name).read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
    head = lines[0].split("\t")
    return [dict(zip(head, line.split("\t"), strict=True)) for line in lines[1:]]


def same(a: str, b: str) -> bool:
    return refs.grapheme(refs.to_code_point(a)) == refs.grapheme(refs.to_code_point(b))


# --- the rule, on small tables ------------------------------------------------------------------------


def edge(a: str, b: str, relation: str = "simplified", source: str = "unihan", detail: str = "x") -> dict[str, str]:
    return {"a": a, "b": b, "relation": relation, "source": source, "detail": detail}


def forms(**rows: list[tuple[str, str]]) -> dict:
    """The 通用规范汉字表 of the given characters, each with the (繁体字, 异体字) rows it prints."""
    found = [{"char": char, "full": full, "variant": variant} for char, pairs in rows.items() for full, variant in pairs]
    return standard_forms(found, {char: n for n, char in enumerate(rows, 1)})


def headwords(*rows: tuple[str, str, str, str]) -> Headwords:
    return Headwords({"dictionary": dictionary, "entry": entry, "char": char, "role": "head", "reading": reading}
                     for dictionary, entry, char, reading in rows)


def ruled(*rows: tuple[str, str, str]) -> dict:
    return rulings({"form": form, "full": full, "ruling": ruling, "by": "owner", "date": "2026-11-25", "note": ""} for form, full, ruling in rows)


def decided(variants, standard, *, words=None, curated=None, figures=None, familiar="", hand=None):
    chars = {char for row in variants for char in (row["a"], row["b"])} | {c for one in standard.values() for c in {one.char, *one.full}}
    found: list[Decision] = []
    joined = families(variants, (), chars, figures or {}, set(), curated, familiarity={char: 0 for char in familiar},
                      standard=standard, headwords=words, decisions=found, rulings=hand)
    return ({family.head: "".join(sorted(family.members)) for family in joined if len(family.members) > 1},
            {one.form: one.reasons for one in found})


#: 动 is the simplified 動, and other tables also relate it to 働 and 甲, so the 異寫字 pass joins it to none.
DONG = [edge("動", "动"), edge("働", "动", "variant", "wikidata"), edge("动", "甲", "variant", "wikidata"),
        edge("甲", "乙", "variant", "wikidata"), edge("働", "丙", "variant", "wikidata")]


def test_a_ruling_settles_a_pair_whatever_the_rules_say_of_it():
    table, several = forms(动=[("動", "")]), forms(发=[("發", ""), ("髮", "")])
    variants = [edge("發", "发"), edge("髮", "发")]
    assert decided(DONG, table)[1] == {"动": []}
    assert decided(DONG, table, hand=ruled(("动", "動", "two")))[1] == {"动": ["ruled two characters: 動, owner 2026-11-25"]}
    joined, reasons = decided(variants, several)
    assert joined == {} and reasons == {"发": ["several characters: 發髮"]}
    joined, reasons = decided(variants, several, hand=ruled(("发", "發", "one")))
    assert sorted(joined.values()) == ["发發"] and reasons == {"发": []}


def test_a_ruling_that_settles_nothing_stops_the_build():
    with pytest.raises(ValueError, match="动甲"):
        decided(DONG, forms(动=[("動", "")]), hand=ruled(("动", "甲", "two")))
    # A pair an earlier pass joined is none of this one's.
    with pytest.raises(ValueError, match="动動"):
        decided([edge("動", "动")], forms(动=[("動", "")]), hand=ruled(("动", "動", "two")))
    with pytest.raises(ValueError, match="once a pair"):
        ruled(("动", "動", "perhaps"))


def test_a_form_of_one_character_joins_whatever_else_relates_it():
    assert decided(DONG, {})[0] == {"乙": "乙甲", "丙": "丙働"}
    found, reasons = decided(DONG, forms(动=[("動", "動")]))
    assert found["动" if "动" in found else "動"] == "动動" and reasons == {"动": []}


def test_without_the_table_the_pass_does_not_run():
    chars = {char for row in DONG for char in (row["a"], row["b"])}
    assert not any({"动", "動"} <= family.members for family in families(DONG, (), chars, {}, set()))


def test_the_joined_family_cites_the_table_and_every_source_that_states_the_pair():
    rows = [*DONG, edge("動", "动", "simplified", "opencc", "STCharacters | TSCharacters")]
    chars = {char for row in rows for char in (row["a"], row["b"])}
    family, = [one for one in families(rows, (), chars, {}, set(), standard=forms(动=[("動", "動")])) if "动" in one.members]
    assert {(one.source, one.relation, one.detail) for one in family.edges} == {
        ("tongyong-guifan", "simplified", "0001 动（動）"), ("unihan", "simplified", "x"), ("opencc", "simplified", "STCharacters | TSCharacters")}


@pytest.mark.parametrize(("variants", "standard", "words", "reason"), [
    # 后 stands for itself and for 後 (～), by the table or by OpenCC.
    ([edge("後", "后")], forms(后=[("后", "后"), ("後", "後")]), None, "the table gives it for itself too"),
    ([edge("價", "价"), edge("價", "价", "simplified", "opencc", "STCharacters (also itself) | TSCharacters")],
     forms(价=[("價", "價")]), None, "OpenCC gives it for itself too"),
    # 发 is for 發 and for 髮.
    ([edge("發", "发"), edge("髮", "发")], forms(发=[("發", "發"), ("髮", "髮")]), None, "several characters: 發髮"),
    # OpenCC gives 录 for 彔 too, which no source relates to 録.
    ([edge("録", "录"), edge("彔", "录", "simplified", "opencc", "STCharacters")], forms(录=[("録", "録")]), None, "a further character: 彔"),
    # 著 is in the table itself.
    ([edge("著", "着")], forms(着=[], 著=[]), None, "the table lists the full form too: 著"),
    # 听 is a headword read otherwise.
    ([edge("聽", "听")], forms(听=[("聽", "聽")]), headwords(("guangyun", "1", "听", "疑"), ("guangyun", "2", "聽", "透")),
     "a headword of the guangyun with another reading"),
    ([edge("從", "从")], forms(从=[("從", "從")]), headwords(("shuowen", "1", "从", "疾容"), ("shuowen", "2", "從", "慈用")),
     "a headword of the shuowen with another reading"),
    ([edge("離", "离"), edge("离", "離", "borrowed", "cjkvi-variants", "离→離 hydcd/borrowed")], forms(离=[("離", "離")]), None,
     "a loan or a substitute"),
    ([edge("檁", "檩"), edge("檁", "檩", "non-cognate", "cjkvi-variants", "檁→檩 cjkvi/non-cognate")], forms(檩=[("檁", "檁")]), None,
     "a non-cognate homograph"),
    ([edge("鈾", "铀"), edge("鈾", "宙", "z")], forms(铀=[("鈾", "鈾")], 宙=[]), None,
     "its full form is of the grapheme of 宙, another character of the table"),
])
def test_a_form_stays_apart(variants, standard, words, reason):
    familiar = "宙" if "宙" in standard else ""
    # Something else each is related to, so that the 異寫字 pass would not join it either.
    form = next(iter(standard))
    others = [edge(form, "甲", "variant", "wikidata"), edge("甲", "乙", "variant", "wikidata")]
    found, reasons = decided([*variants, *others], standard, words=words, familiar=familiar)
    assert reason in reasons[form]
    assert not any(form in members and one.full & set(members) for members in found.values() for one in [standard[form]])


#: 骂 is for 駡, 罵 is printed beside it, and other tables relate 骂 to 甲.
MA = [edge("駡", "骂"), edge("罵", "骂"), edge("骂", "甲", "variant", "wikidata"), edge("甲", "乙", "variant", "wikidata")]
MA_TABLE = [("駡", "駡"), ("駡", "罵")]
#: Two sources that call the full forms variants, which other passes take for variants of 丙 and 丁 too.
SAME = [edge("駡", "罵", "variant", "wikidata"), edge("駡", "罵", "semantic"), edge("駡", "丙", "variant", "wikidata"),
        edge("罵", "丁", "variant", "wikidata"), edge("丙", "戊", "variant", "wikidata"), edge("丁", "己", "variant", "wikidata")]


def apart_in_both(*chars: str) -> list[tuple[str, str, str, str]]:
    return [(book, f"{book}{char}", char, char) for book in ("shuowen", "guangyun") for char in chars]


def test_the_full_forms_of_one_form_join_where_they_are_one_character():
    found, reasons = decided([*MA, *SAME], forms(骂=MA_TABLE), familiar="罵")
    assert found["罵"] == "罵駡骂" and reasons == {"骂": []}


@pytest.mark.parametrize(("rows", "standard", "words"), [
    (SAME[1:], forms(骂=MA_TABLE), None),  # one source alone calls them variants
    ([*SAME, edge("駡", "罵", "substitute", "cjkvi-variants", "駡→罵 jp/borrowed")], forms(骂=MA_TABLE), None),
    ([*SAME, edge("駡", "罵", "non-cognate", "cjkvi-variants", "駡→罵 cjkvi/non-cognate")], forms(骂=MA_TABLE), None),
    (SAME, forms(骂=MA_TABLE), apart_in_both("駡", "罵")),
    (SAME, forms(骂=MA_TABLE, 傌=[("罵", "罵")]), None),  # 罵 has a standard form of its own
    (SAME, forms(骂=MA_TABLE, 罵=[]), None),
])
def test_a_form_stays_apart_from_the_full_forms_that_are_two(rows, standard, words):
    found, reasons = decided([*MA, *rows], standard, words=headwords(*words) if words else None)
    assert "a further character: 罵" in reasons["骂"]
    assert not any({"駡", "罵"} <= set(members) or {"骂", "駡"} <= set(members) for members in found.values())


def test_a_form_the_table_gives_for_two_characters_joins_neither_to_the_other():
    """鍾 and 鐘 are both printed full forms of 钟, whatever calls them variants; 鈡 is a variant of either."""
    rows = [edge("鍾", "钟"), edge("鐘", "钟"), edge("鈡", "钟", "simplified", "opencc", "STCharacters"),
            edge("鍾", "鐘", "variant", "wikidata"), edge("鍾", "鐘", "semantic"), edge("鍾", "鈡", "variant", "wikidata"),
            edge("鍾", "鈡", "equivalent", "yitizi"), edge("鈡", "鐘", "variant", "cjkvi-variants", "鈡→鐘 cjkvi/variant")]
    found, reasons = decided(rows, forms(钟=[("鍾", "鍾"), ("鐘", "鐘")]))
    assert found == {} and reasons["钟"][0] == "several characters: 鍾鐘"


def test_full_forms_do_not_join_over_a_member_held_apart():
    """The members each full form brings are compared, not the two alone."""
    rows = [*MA, *SAME, edge("駡", "庚", "z"), edge("罵", "辛", "z")]
    assert "庚" in next(members for members in decided(rows, forms(骂=MA_TABLE), familiar="罵")[0].values() if "罵" in members)
    found, reasons = decided(rows, forms(骂=MA_TABLE), words=headwords(*apart_in_both("庚", "辛")))
    assert not any({"駡", "罵"} <= set(members) for members in found.values()) and reasons["骂"] == ["a further character: 罵"]


def test_a_ruling_on_two_full_forms_settles_whether_they_join():
    rows = [*MA, *SAME]
    assert decided(rows, forms(骂=MA_TABLE), hand=ruled(("罵", "駡", "two")))[1]["骂"] == ["a further character: 罵"]
    found, reasons = decided([*MA, *SAME[2:]], forms(骂=MA_TABLE), hand=ruled(("駡", "罵", "one")))
    assert any({"骂", "駡", "罵"} <= set(members) for members in found.values()) and reasons["骂"] == []


def test_a_further_form_the_table_does_not_print_is_none_where_a_source_calls_it_a_variant():
    """Unihan gives 葯 for 药, which the table prints for 藥 alone; 閒 is a headword apart from 閑."""
    rows = [edge("藥", "药"), edge("葯", "药"), edge("药", "甲", "variant", "wikidata"), edge("甲", "乙", "variant", "wikidata")]
    assert decided(rows, forms(药=[("藥", "藥")]))[1] == {"药": ["a further character: 葯"]}
    rows.append(edge("藥", "葯", "variant", "wikidata"))
    assert decided(rows, forms(药=[("藥", "藥")]))[1].get("药", []) == []
    assert decided(rows, forms(药=[("藥", "藥")]), words=headwords(*apart_in_both("藥", "葯")))[1] == {"药": ["a further character: 葯"]}


def test_a_reading_of_a_third_character_carries_no_join():
    """The 廣韻 reads 岭 來青平 and 嶺 來清上, and 阾, a variant of 嶺, both ways."""
    rows = [edge("嶺", "岭"), edge("嶺", "阾", "z"), edge("岭", "甲", "variant", "wikidata"), edge("甲", "乙", "variant", "wikidata")]
    read = [("guangyun", "1", "岭", "來青平"), ("guangyun", "2", "嶺", "來清上"), ("guangyun", "3", "阾", "來青平"), ("guangyun", "4", "阾", "來清上")]
    assert decided(rows, forms(岭=[("嶺", "嶺")]), words=headwords(*read))[1] == {"岭": ["a headword of the guangyun with another reading"]}
    # Where the dictionary does not read the full form, the rest of its family speaks for it; where it reads none, nothing does.
    assert decided(rows, forms(岭=[("嶺", "嶺")]), words=headwords(read[0], *read[2:]))[1] == {"岭": []}
    assert decided(rows, forms(岭=[("嶺", "嶺")]), words=headwords(read[0], read[3]))[1] == {"岭": ["a headword of the guangyun with another reading"]}
    assert decided(rows, forms(岭=[("嶺", "嶺")]), words=headwords(read[0]))[1] == {"岭": []}


def test_a_fanqie_is_compared_whatever_form_it_is_spelled_in():
    rows = [{"dictionary": "shuowen", "entry": char, "char": char, "role": "head", "reading": reading} for char, reading in (("启", "康禮"), ("啟", "康礼"))]
    assert Headwords(rows).compared("启", "啟") == {"shuowen": False}
    assert Headwords(rows, {"禮": "礼", "礼": "礼"}).compared("启", "啟") == {"shuowen": True}


def test_what_the_dictionaries_hold_apart_stays_behind():
    """𦬕, a variant of 茧 in one table, is read otherwise than 繭 in both dictionaries; 𽆢 hangs on it alone."""
    rows = [edge("繭", "茧"), edge("茧", "𦬕", "z"), edge("𦬕", "𽆢", "z"), edge("茧", "𮈀", "z"), edge("茧", "甲", "variant", "wikidata"),
            edge("甲", "乙", "variant", "wikidata"), edge("繭", "丙", "variant", "wikidata"), edge("丙", "丁", "variant", "wikidata")]
    for familiar in ("", "茧", "繭", "𦬕"):
        found, reasons = decided(rows, forms(茧=[("繭", "繭")]), words=headwords(*apart_in_both("繭", "𦬕")), familiar=familiar)
        assert {frozenset(members) for members in found.values()} >= {frozenset("繭茧𮈀"), frozenset("𦬕𽆢")} and reasons == {"茧": []}
    assert frozenset("繭茧𮈀𦬕𽆢") in {frozenset(members) for members in decided(rows, forms(茧=[("繭", "繭")]))[0].values()}


def test_the_same_tables_always_give_the_same_families():
    """The families of characters in no other table are told apart, and no set order reaches the result."""
    table = forms(发=[("發", "發"), ("髮", "髮")])
    assert {tuple(decided([], table)[1]["发"]) for _ in range(50)} == {("several characters: 發髮",)}
    script = (
        "import csv, hashlib, pathlib\n"
        "from glyph_atlas.shape_families import Headwords, families, standard_forms\n"
        f"vocab = pathlib.Path({str(VOCAB)!r})\n"
        "def table(name):\n"
        "    return list(csv.DictReader([l for l in (vocab / name).open(encoding='utf-8') if l[0] != '#'], delimiter='\\t', quoting=csv.QUOTE_NONE))\n"
        "rows = table('kanji-variants.tsv'); chars = {c for r in rows for c in (r['a'], r['b'])}; found = []\n"
        "standard = standard_forms(table('tongyong-guifan-forms.tsv'), {r['char']: int(r['position']) for r in table('tongyong-guifan.tsv')})\n"
        "made = families(rows, table('kanji-equivalents.tsv'), chars, {}, set(), standard=standard, headwords=Headwords(table('headword-readings.tsv')), decisions=found)\n"
        "text = repr([(f.head, sorted(f.members), f.edges, sorted(f.writers.items())) for f in made]) + repr([(d.form, d.reasons, d.edges) for d in found])\n"
        "print(len(made), hashlib.sha256(text.encode()).hexdigest())\n")
    made = {subprocess.run([sys.executable, "-c", script], env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT / "src")},
                           check=True, capture_output=True, text=True).stdout for seed in ("1", "2", "3")}
    assert len(made) == 1 and int(made.pop().split()[0]) > 10000


def test_a_form_the_shuowen_gives_under_the_full_form_is_that_character():
    words = headwords(("shuowen", "7", "達", "徒葛"), ("shuowen", "7", "达", "徒葛"), ("guangyun", "1", "达", "透"), ("guangyun", "2", "達", "定"))
    rows = [edge("達", "达"), edge("达", "甲", "variant", "wikidata"), edge("甲", "乙", "variant", "wikidata")]
    assert decided(rows, forms(达=[("達", "達")]), words=words)[1] == {"达": []}


def test_a_form_the_shuowen_gives_under_another_character_and_the_guangyun_reads_so_stays_apart():
    """箓 is the 或體 of 簏 and read as it; 仅 is a 古文 of 奴 the 廣韻 does not hold."""
    rows = [edge("籙", "箓"), edge("箓", "甲", "variant", "wikidata"), edge("甲", "乙", "variant", "wikidata")]
    words = [("shuowen", "1", "簏", "盧谷"), ("shuowen", "1", "箓", "盧谷"), ("guangyun", "1", "簏", "來")]
    made = [{"dictionary": d, "entry": e, "char": c, "role": "form" if (d, c) == ("shuowen", "箓") else "head", "reading": r} for d, e, c, r in words]
    assert decided(rows, forms(箓=[("籙", "籙")]), words=Headwords(made))[1] == {"箓": []}
    made.append({"dictionary": "guangyun", "entry": "2", "char": "箓", "role": "head", "reading": "來"})
    assert decided(rows, forms(箓=[("籙", "籙")]), words=Headwords(made))[1] == {"箓": ["a 重文 of another character: 簏"]}


@pytest.mark.parametrize(("relation", "claim"), [("non-cognate", "cjkvi/non-cognate"), ("borrowed", "hydcd/borrowed"), ("substitute", "jp/borrowed")])
def test_a_tag_its_own_database_contradicts_keeps_nothing_apart(relation, claim):
    said = edge("温", "溫", relation, "cjkvi-variants", f"温→溫 {claim}")
    others = [edge("温", "甲", "variant", "wikidata"), edge("甲", "乙", "variant", "wikidata")]
    why = "a non-cognate homograph" if relation == "non-cognate" else "a loan or a substitute"
    assert decided([edge("溫", "温"), said, *others], forms(温=[]))[1]["温"] == [why]
    for tag in ("ucs-scs/variant", "joyo/variant", "hydzd/variant", "cjkvi/simplified", "cjkvi/variant"):
        rows = [edge("溫", "温"), said, edge("温", "溫", "variant", "cjkvi-variants", f"温→溫 {tag}"), *others]
        assert decided(rows, forms(温=[]))[1]["温"] == [], tag
        # Where the 說文解字 heads an entry with each, it stands, but for a pair the 常用漢字表 gives.
        entries = headwords(("shuowen", "1", "温", "a"), ("shuowen", "2", "溫", "a"))
        assert decided(rows, forms(温=[]), words=entries)[1]["温"] == ([] if tag == "joyo/variant" else [why]), tag
    # Another database's word is its own.
    assert decided([edge("溫", "温"), {**said, "source": "wikidata"}, edge("温", "溫", "variant", "cjkvi-variants", "温→溫 hydzd/variant"), *others],
                   forms(温=[]))[1]["温"] == [why]


def test_a_pair_of_the_jouyou_table_is_one_character_though_the_new_form_is_a_headword():
    rows = [edge("戻", "戾", "variant", "cjkvi-variants", "戻→戾 joyo/variant"), edge("戻", "甲", "variant", "wikidata"), edge("甲", "乙", "variant", "wikidata"),
            edge("戾", "丙", "variant", "wikidata"), edge("丙", "丁", "variant", "wikidata")]
    words = headwords(*((book, f"{book}{n}", char, reading) for book in ("shuowen", "guangyun")
                        for n, (char, reading) in enumerate((("戻", "a"), ("戾", "b")))))
    found, reasons = decided(rows, {}, words=words, familiar="戻")
    assert reasons == {"戻": []} and found["戻"] == "戻戾"
    pan = [edge("弁", old, "variant", "cjkvi-variants", f"弁→{old} joyo/variant") for old in "辨瓣辯"]
    pan += [edge(old, other, "variant", "wikidata") for old, other in zip("辨瓣辯", "甲乙丙", strict=True)]
    pan += [edge(one, other, "variant", "wikidata") for one, other in zip("甲乙丙", "丁戊己", strict=True)]
    assert decided(pan, {}, familiar="弁")[1]["弁"][0] == "several characters: 瓣辨辯"


def test_a_form_leaves_the_family_another_pass_put_it_in():
    """备 was reduced to 留, with 𤲢 hanging on 备 alone: both go to 備, and 留 keeps its own."""
    detail = "{}: 法務省戸籍法関連通達・通知, 種別 戸籍統一文字情報 親字・正字, ホップ数 1"
    rows = [{"a": "备", "b": "留", "relation": "reduction", "source": "mj-shrink-map", "detail": detail.format("MJ1")},
            edge("留", "畱", "z"), edge("备", "𤲢", "z"), edge("備", "备"), edge("備", "俻", "z"),
            edge("備", "甲", "variant", "wikidata"), edge("甲", "乙", "variant", "wikidata"),
            edge("留", "丙", "variant", "wikidata"), edge("丙", "丁", "variant", "wikidata")]
    found, reasons = decided(rows, forms(备=[("備", "備")]), figures={"MJ1": "备"}, familiar="留備")
    assert found == {"留": "留畱", "備": "俻備备𤲢", "乙": "乙甲", "丁": "丁丙"} and reasons == {"备": []}


def test_what_heads_a_family_takes_it_along_and_the_head_is_one_of_the_two():
    rows = [edge("黄", "黃"), edge("黃", "黄", "variant", "cjkvi-variants", "黄→黃 ucs-scs/variant"), edge("黄", "𨝴", "z"), edge("黃", "𡕛", "z")]
    found, _ = decided(rows, forms(黄=[]), familiar="黄")
    assert found == {"黄": "".join(sorted("黄黃𨝴𡕛"))}


# --- the tables as they are ---------------------------------------------------------------------------


def test_the_headwords_are_the_two_dictionaries():
    rows = table("headword-readings.tsv")
    assert Counter((row["dictionary"], row["role"]) for row in rows) == {
        ("guangyun", "head"): 25327, ("shuowen", "head"): 9837, ("shuowen", "form"): 1248}
    read = {(row["dictionary"], row["char"]): row["reading"] for row in rows}
    assert read["shuowen", "从"] == "疾容" and read["shuowen", "從"] == "慈用" and read["guangyun", "听"].startswith("疑")


def test_the_table_of_forms_is_whole():
    rows = table("tongyong-guifan-forms.tsv")
    assert len(rows) == 3670 and len({row["char"] for row in rows}) == 3119
    assert {row["char"] for row in rows} <= {row["char"] for row in table("tongyong-guifan.tsv")}
    assert [row["full"] for row in rows if row["char"] == "后"] == ["后", "後"]


def test_the_collation_of_a_headword_is_read():
    spec = importlib.util.spec_from_file_location("build_headword_readings", ROOT / "scripts/build_headword_readings.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    assert [build.headword(text) for text in ("東", "［東］", "甲〈乙〉", "｛丙｝", "⿰木木")] == ["東", "東", "乙", None, None]


def test_the_forms_joined_and_the_forms_apart_are_pinned():
    apart = table("grapheme-apart.tsv")
    joined = {row["b"] for row in table("grapheme-families.tsv") if row["source"] == "tongyong-guifan"}
    assert (len(joined), len(apart)) == (357, 183)
    assert Counter(reason.partition(":")[0].partition(" of ")[0] for row in apart for reason in row["reasons"].split(" | ")) == {
        "a headword": 114, "OpenCC gives it for itself too": 96, "the table gives it for itself too": 71, "several characters": 29,
        "a loan or a substitute": 11, "the table lists the full form too": 14, "a further character": 27, "its full form is": 6, "a 重文": 5}


def test_every_form_the_rule_names_stays_apart():
    for row in table("grapheme-apart.tsv"):
        assert not any(same(row["form"], full) for full in row["full"]), row


def test_the_forms_that_write_another_character_stay_apart():
    pairs = ("后後 从從 价價 仆僕 征徵 几幾 于於 舍捨 宁寧 干乾 干幹 里裏 面麵 只隻 只衹 松鬆 云雲 谷穀 斗鬥 发發 发髮 弁辨 弁瓣 弁辯 "
             "广廣 厂廠 听聽 胜勝 气氣 离離 闲閑 闲閒 录録 着著 了瞭 借藉 丑醜 才纔 合閤 卷捲 咸鹹 表錶 出齣 制製 准準 台檯 台颱 复復 复複 钟鐘 钟鍾 铀鈾")
    assert [pair for pair in pairs.split() if same(*pair)] == []


def test_the_forms_that_write_one_character_join():
    pairs = ("对對 对対 动動 备備 实實 实実 还還 这這 无無 网網 个個 开開 电電 风風 门門 内內 没沒 黄黃 温溫 虚虛 状狀 涙淚 即卽 併倂 戻戾 概槪 錬鍊 餅餠 "
             "达達 壳殼 启啓 净淨 艺藝 滨濱 归歸 阁閣 场場 识識")
    assert [pair for pair in pairs.split() if not same(*pair)] == []


def test_the_full_forms_of_one_form_are_one_grapheme_or_the_form_is_in_neither():
    one = ("骂駡 骂罵 钩鈎 钩鉤 绣綉 绣繡 蕴藴 蕴蘊 赃贜 赃贓 奖奬 奖獎 鉴鑒 鉴鑑 镰鐮 镰鎌 鳄鰐 鳄鱷 启啓 启啟 酝醖 酝醞")
    assert [pair for pair in one.split() if not same(*pair)] == []
    two = "叹嘆 叹歎 嘆歎 夹夾 夹袷 夾袷 赞贊 赞讚 枪槍 枪鎗 炼煉 炼鍊 鍾鐘 曆歷 當噹 壇罎"
    assert [pair for pair in two.split() if same(*pair)] == []
    assert refs.grapheme("U+9A82") == refs.to_code_point("罵")


def test_a_join_holds_for_what_it_brings():
    assert not same("岭", "嶺") and same("茧", "繭") and not same("𦬕", "繭") and not same("𦬕", "茧")


def test_one_rule_for_loans_and_non_cognates():
    assert [pair for pair in "涩澀 衅釁 糁糝 凭憑 檩檁 廪廩".split() if not same(*pair)] == []
    assert not same("离", "離")


def test_a_form_leaves_what_it_was_filed_under():
    assert not same("备", "留") and not same("壳", "賣") and not same("橱", "厨")


def test_a_form_joined_by_the_table_is_cited_to_it():
    info = refs.grapheme_info("動")
    cited = {entry["id"]: entry for entry in info["evidence"]}
    assert {"a": "動", "b": "动", "relation": "simplified", "role": "merge", "detail": "0353 动（動）"} in cited["tongyong-guifan"]["edges"]
    assert {"unihan", "opencc"} <= cited.keys()
