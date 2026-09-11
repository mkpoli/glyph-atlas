"""Tests for the koji markup parser.

`NOTATION_PAGE` holds every 入力例 of the platform's notation guide,
https://wiki.honkoku.org/doku.php?id=howto_markup (CC BY-SA 4.0), with the plain text the
Honkoku-Lines rule produces for it. The pairs in `tests/fixtures/koji-pairs.tsv` are 50 lines of
Honkoku-Lines v2.0: their `text` and their stored `plain_text`. No test reads the network or
`cache/`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kuzushiji_atlas import koji

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "koji-pairs.tsv"

NOTATION_PAGE = [
    (
        "嘉永七年寅の十一月／諸国（しょこく）大／地（ち）しんの巨細を…",
        "嘉永七年寅の十一月諸国大地しんの巨細を…",
    ),
    (
        "◯嘉永七年寅の十一月／諸国（しょこく）大／地（ち）しんの巨細を…",
        "◯嘉永七年寅の十一月諸国大地しんの巨細を…",
    ),
    (
        "嘉永七年寅の十一月《振り仮名：諸国｜しょこく》大《振り仮名：地｜ち》しんの巨細を…",
        "嘉永七年寅の十一月諸国しょこく大地ちしんの巨細を…",
    ),
    (
        "《振り仮名：獅子｜しし｜ライオン》と《振り仮名：火の鳥｜ひのとり｜フェニックス》",
        "獅子ししライオンと火の鳥ひのとりフェニックス",
    ),
    ("低￣レテ＿レ頭￣ヲ思￣フ＿二故郷￣ヲ＿一", "低頭思故郷"),
    ("昔々あるところに《割書：おじいさんと｜おばあさん》が…", "昔々あるところにおじいさんとおばあさんが…"),
    ("《題：大和国郷帳》", "大和国郷帳"),
    ("《割書：《題：割書の一行目》｜《題：割書の二行目》》", "題割書の一行目割書の二行目"),
    ("《箱：大和国郷帳》", "大和国郷帳"),
    ("《見せ消ち：元の文章｜新しい文章》。", "元の文章新しい文章。"),
    ("《圏点：対象となる文字｜◯》。", "対象となる文字◯。"),
    ("この部分に【注記が入ります】。", "この部分に。"),
    ("吉原は町家在□ともゆりくづれ□□□は津浪にて…", "吉原は町家在ともゆりくづれは津浪にて…"),
    ("《場所：江戸》ゟ之御用翰昨夜到来", "江戸ゟ之御用翰昨夜到来"),
]


def leaves(nodes):
    for node in nodes:
        if isinstance(node, koji.Text):
            yield node
        else:
            yield from leaves(node.children)


def spans(nodes) -> list[tuple[int, int]]:
    return [(leaf.start, leaf.end) for leaf in leaves(nodes)]


def elements(parsed: koji.Parsed) -> dict[int, koji.Element]:
    return {element.id: element for element in koji.walk(parsed.nodes)}


def assert_offsets(text: str, parsed: koji.Parsed) -> None:
    """Every character maps back into the raw line, and the nodes cover it without a gap."""
    for char in parsed.chars:
        assert text[char.start : char.end] == char.text
    flat = spans(parsed.nodes)
    cursor = 0
    for start, end in flat:
        assert start == cursor
        assert end > start
        cursor = end
    assert cursor == len(text)


def fixture_pairs() -> list[tuple[str, str]]:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    pairs = []
    for line in lines:
        if not line or line.startswith("#"):
            continue
        text, plain = line.split("\t")
        pairs.append((text, plain))
    return pairs


def test_fixture_header_names_source_and_licence():
    header = FIXTURE.read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith("#")
    assert "Honkoku-Lines v2.0" in header
    assert "CC BY-SA 4.0" in header
    assert len(fixture_pairs()) == 50


@pytest.mark.parametrize(("text", "expected"), NOTATION_PAGE)
def test_notation_page_examples(text: str, expected: str):
    parsed = koji.parse(text)
    assert parsed.plain == expected
    assert parsed.plain == koji.plain(text)
    assert not parsed.malformed
    assert_offsets(text, parsed)


@pytest.mark.parametrize(("text", "plain_text"), fixture_pairs())
def test_honkoku_lines_pairs(text: str, plain_text: str):
    parsed = koji.parse(text)
    assert parsed.plain == plain_text
    assert_offsets(text, parsed)


def test_short_form_furigana():
    text = "嘉永（かえい）／元年（がんねん）"
    parsed = koji.parse(text)
    assert parsed.plain == "嘉永元年"
    assert [(char.text, char.role) for char in parsed.chars] == [
        ("嘉", "main"),
        ("永", "main"),
        ("か", "ruby"),
        ("え", "ruby"),
        ("い", "ruby"),
        ("元", "main"),
        ("年", "main"),
        ("が", "ruby"),
        ("ん", "ruby"),
        ("ね", "ruby"),
        ("ん", "ruby"),
    ]
    assert [node.kind for node in parsed.nodes if isinstance(node, koji.Element)] == ["ruby", "ruby"]
    assert all(node.attrs["form"] == "legacy" for node in parsed.nodes if isinstance(node, koji.Element))


def test_short_form_furigana_carries_a_left_reading():
    parsed = koji.parse("未（いまだ｜ズ）")
    assert parsed.plain == "未"
    assert [(char.text, char.role) for char in parsed.chars] == [
        ("未", "main"),
        ("い", "ruby"),
        ("ま", "ruby"),
        ("だ", "ruby"),
        ("ズ", "ruby-left"),
    ]


def test_bracket_furigana_keeps_the_base_and_marks_the_readings():
    parsed = koji.parse("《振り仮名：獅子｜しし｜ライオン》")
    (ruby,) = parsed.nodes
    assert ruby.kind == "ruby"
    assert ruby.attrs == {"form": "bracket"}
    assert [(char.text, char.role) for char in parsed.chars] == [
        ("獅", "main"),
        ("子", "main"),
        ("し", "ruby"),
        ("し", "ruby"),
        ("ラ", "ruby-left"),
        ("イ", "ruby-left"),
        ("オ", "ruby-left"),
        ("ン", "ruby-left"),
    ]


def test_okurigana_and_kaeriten():
    parsed = koji.parse("低￣レテ＿レ頭￣ヲ思￣フ＿二故郷￣ヲ＿一")
    assert parsed.plain == "低頭思故郷"
    assert [(char.text, char.role) for char in parsed.chars] == [
        ("低", "main"),
        ("レ", "okurigana"),
        ("テ", "okurigana"),
        ("レ", "kaeriten"),
        ("頭", "main"),
        ("ヲ", "okurigana"),
        ("思", "main"),
        ("フ", "okurigana"),
        ("二", "kaeriten"),
        ("故", "main"),
        ("郷", "main"),
        ("ヲ", "okurigana"),
        ("一", "kaeriten"),
    ]


def test_kaeriten_in_braces_and_with_ascii_underscore():
    text = "讀｛＿レ｝＿二_上"
    parsed = koji.parse(text)
    assert parsed.plain == "讀"
    assert [(char.text, char.role) for char in parsed.chars] == [
        ("讀", "main"),
        ("レ", "kaeriten"),
        ("二", "kaeriten"),
        ("上", "kaeriten"),
    ]
    assert [node.kind for node in koji.walk(parsed.nodes)] == ["kaeriten"] * 4


def test_warigaki_columns_are_numbered_in_reading_order():
    parsed = koji.parse("《割書：一行目｜二行目｜三行目》")
    assert parsed.plain == "一行目二行目三行目"
    found = elements(parsed)
    columns: dict[int, list[str]] = {}
    for char in parsed.chars:
        assert char.role == "warigaki"
        column = found[char.path[-1]]
        assert column.kind == "warigaki"
        columns.setdefault(column.attrs["column"], []).append(char.text)
    assert {number: "".join(chars) for number, chars in columns.items()} == {
        1: "一行目",
        2: "二行目",
        3: "三行目",
    }


def test_warigaki_that_starts_mid_line_returns_to_full_width():
    text = "昔々あるところに《割書：おじいさんと｜おばあさん》が…"
    parsed = koji.parse(text)
    assert parsed.plain == "昔々あるところにおじいさんとおばあさんが…"
    opened, closed = text.index("《"), text.index("》")
    before = [char for char in parsed.chars if char.end <= opened]
    inside = [char for char in parsed.chars if opened < char.start and char.end <= closed]
    after = [char for char in parsed.chars if char.start > closed]
    assert {char.role for char in before} == {"main"}
    assert {char.role for char in inside} == {"warigaki"}
    assert {char.role for char in after} == {"main"}
    assert "".join(char.text for char in before) == "昔々あるところに"
    assert "".join(char.text for char in after) == "が…"


def test_furigana_inside_warigaki():
    text = "《割書：《振り仮名：峰｜みね》の字｜《見せ消ち：旧｜《振り仮名：新｜しん》》》"
    parsed = koji.parse(text)
    assert not parsed.malformed
    assert parsed.plain == "振り仮名峰みねの字旧振り仮名新しん"
    found = elements(parsed)
    assert [(char.text, char.role) for char in parsed.chars] == [
        ("峰", "warigaki"),
        ("み", "ruby"),
        ("ね", "ruby"),
        ("の", "warigaki"),
        ("字", "warigaki"),
        ("旧", "cancelled"),
        ("新", "inserted"),
        ("し", "ruby"),
        ("ん", "ruby"),
    ]
    kinds = [[found[id_].kind for id_ in char.path] for char in parsed.chars]
    assert kinds[0] == ["warigaki", "warigaki", "ruby"]
    assert kinds[-1] == ["warigaki", "warigaki", "misekechi", "ruby"]


def test_nested_bracket_plain_rule_applies_once():
    # Honkoku-Lines strips one level, so the inner tag name survives in plain text.
    text = "《割書：《題：割書の一行目》｜《題：割書の二行目》》"
    parsed = koji.parse(text)
    assert parsed.plain == "題割書の一行目割書の二行目"
    outer = parsed.nodes[0]
    assert outer.kind == "warigaki"
    columns = [child for child in outer.children if isinstance(child, koji.Element)]
    assert [column.attrs["column"] for column in columns] == [1, 2]
    titles = [node for column in columns for node in column.children if isinstance(node, koji.Element)]
    assert [title.kind for title in titles] == ["title", "title"]
    assert [title.attrs["form"] for title in titles] == ["bracket", "bracket"]


def test_misekechi_fields_are_cancelled_and_inserted():
    parsed = koji.parse("《見せ消ち：古い｜新しい》。")
    assert parsed.plain == "古い新しい。"
    roles = [char.role for char in parsed.chars]
    assert roles == ["cancelled", "cancelled", "inserted", "inserted", "inserted", "main"]


def test_gap_and_unreadable_runs_give_one_char_each():
    parsed = koji.parse("在□□□と■い")
    assert parsed.plain == "在とい"
    assert [(char.text, char.role) for char in parsed.chars] == [
        ("在", "main"),
        ("□", "gap"),
        ("□", "gap"),
        ("□", "gap"),
        ("と", "main"),
        ("■", "unreadable"),
        ("い", "main"),
    ]
    gaps = [node for node in parsed.nodes if isinstance(node, koji.Element)]
    assert [(node.kind, node.attrs["mark"], node.attrs["count"]) for node in gaps] == [
        ("gap", "□", 3),
        ("gap", "■", 1),
    ]


def test_legacy_wrappers_and_reference():
    text = "〔日本橋〕｛大石内蔵助｝＜安政二年＞＃１０"
    parsed = koji.parse(text)
    # The rule of step 3 removes ｛…｝ with its content, and keeps 〔…〕.
    assert parsed.plain == "〔日本橋〕安政二年＃１０"
    assert "".join(char.text for char in parsed.chars) == "日本橋大石内蔵助安政二年"
    assert [node.kind for node in parsed.nodes if isinstance(node, koji.Element)] == [
        "place",
        "person",
        "date",
        "reference",
    ]
    reference = parsed.nodes[-1]
    assert reference.attrs["number"] == 10
    assert not [char for char in parsed.chars if char.start >= reference.start]


def test_annotation_content_has_the_note_role():
    parsed = koji.parse("この部分に【注記が入ります】。")
    assert parsed.plain == "この部分に。"
    assert [char.role for char in parsed.chars] == ["main"] * 5 + ["note"] * 7 + ["main"]


def test_unknown_bracket_keeps_its_content_as_main():
    parsed = koji.parse("《？：江戸》と《題》")
    assert parsed.plain == "江戸と題"
    unknown = [node for node in parsed.nodes if isinstance(node, koji.Element)]
    assert [node.kind for node in unknown] == ["unknown", "unknown"]
    assert unknown[0].attrs["unknown"] == "？"
    assert unknown[1].attrs["unknown"] is None
    assert {char.role for char in parsed.chars} == {"main"}


def test_known_bracket_with_the_wrong_field_count_is_malformed():
    parsed = koji.parse("《割書：一｜二｜三｜四｜五》")
    assert parsed.malformed
    (node,) = parsed.nodes
    assert node.kind == "unknown"
    assert node.attrs == {"unknown": "割書", "malformed": True}
    assert parsed.plain == "一二三四五"


@pytest.mark.parametrize(
    "text",
    [
        "《割書：一｜二",
        "《題：大和国郷帳",
        "【未閉じ",
        "未】",
        "かるわざ》",
        "（かな",
        "ニ付テハ不明）",
        "〔江戸",
        "｛内蔵助",
        "＜安政二年",
    ],
)
def test_unbalanced_brackets_stay_literal_text(text: str):
    parsed = koji.parse(text)
    assert parsed.malformed
    assert_offsets(text, parsed)
    brackets = [char for char in parsed.chars if char.text in "《》【】〔〕｛｝＜＞（）"]
    assert brackets
    assert {char.role for char in brackets} == {"main"}


def test_balanced_brackets_are_not_malformed():
    for text in ("《割書：一｜二》", "【注記】", "未（かな）", "〔江戸〕", "｛内蔵助｝", "＜安政二年＞"):
        assert koji.parse(text).malformed is False


def test_ascii_and_fullwidth_separators_are_both_accepted():
    for text in ("《割書：甲｜乙》", "《割書:甲|乙》", "《割書：甲|乙》", "《割書:甲｜乙》"):
        parsed = koji.parse(text)
        assert not parsed.malformed
        (node,) = parsed.nodes
        assert node.kind == "warigaki"
        found = elements(parsed)
        columns = {found[char.path[-1]].attrs["column"]: char.text for char in parsed.chars}
        assert columns == {1: "甲", 2: "乙"}
    for text in ("《題：国》", "《題:国》"):
        (node,) = koji.parse(text).nodes
        assert node.kind == "title"


def test_ascii_separators_survive_the_plain_rule():
    # The rule looks for the full-width characters only, so the ASCII ones stay in place.
    assert koji.plain("《割書:甲|乙》") == "割書:甲|乙"
    assert koji.plain("《割書：甲｜乙》") == "甲乙"
    assert koji.plain("《題:国:名》") == "題:国:名"
    assert koji.parse("《題:国:名》").plain == "題:国:名"


def test_plain_rule_steps():
    assert koji.plain("【注記】本文") == "本文"
    assert koji.plain("本文（かな）") == "本文"
    assert koji.plain("｛＿レ｝本文") == "本文"
    assert koji.plain("《題：題名》") == "題名"
    assert koji.plain("《割書：一｜二｜三》") == "一二三"
    assert koji.plain("文＿レ字") == "文字"
    assert koji.plain("文￣テ字") == "文字"
    assert koji.plain("■□〓") == ""
    assert koji.plain("ＡＢＣ abc １２３") == "ＡＢＣ１２３"
    assert koji.plain("草木性譜　　　地") == "草木性譜地"
    assert koji.plain("＃１０") == "＃１０"
    assert koji.plain("#10") == ""
    assert koji.plain("〔江戸〕") == "〔江戸〕"
    assert koji.plain("＜安政二年＞") == "安政二年"


def test_empty_line():
    parsed = koji.parse("")
    assert parsed.plain == ""
    assert parsed.nodes == []
    assert parsed.chars == []
    assert not parsed.malformed


def test_offsets_and_paths_hold_for_every_notation_example():
    samples = [text for text, _ in NOTATION_PAGE]
    samples += [text for text, _ in fixture_pairs()]
    samples += [
        "《割書：a｜b》",
        "《割書:一|二》",
        "《割書：一｜二｜三｜四｜五》",
        "【右丁】一の面",
        "％字下げ一",
        "％表紙",
        "<TATE>と<TATE>",
        "あ■□〓い",
        "《割書：《振り仮名：峰｜みね》の字｜《見せ消ち：旧｜《振り仮名：新｜しん》》》",
        "《割書：一｜二",
        "《振り仮名：漢字｜かんじ｜kanji》",
    ]
    for text in samples:
        parsed = koji.parse(text)
        assert_offsets(text, parsed)
        found = elements(parsed)
        assert len(found) == len(list(koji.walk(parsed.nodes)))
        for char in parsed.chars:
            for element_id in char.path:
                element = found[element_id]
                assert element.start <= char.start and char.end <= element.end
