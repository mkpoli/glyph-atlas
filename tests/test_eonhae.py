"""Pure tests for 老乞大諺解 cutting: no model, GPU or network."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from glyph_atlas import eonhae
from glyph_atlas.schema import Box

CIRCLE = "U+25CB"


def source(text: str, *, title: str = "노걸대언해", revid: str = "1") -> eonhae.WikiSource:
    return eonhae.WikiSource(title, revid, "https://example.test", text)


def api_json(title: str, revid: str, content: str) -> str:
    return json.dumps({
        "query": {
            "pages": [
                {
                    "title": title,
                    "revisions": [
                        {"revid": int(revid), "slots": {"main": {"content": content}}},
                    ],
                },
            ],
        },
    })


def test_load_sources_fetches_missing_pinned_upstream_files(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def download(url: str, dest: Path, *, expected: str | None = None) -> Path:
        query = parse_qs(urlsplit(url).query)
        revid = query["revids"][0]
        calls.append((urlsplit(url).netloc, revid, expected))
        if revid == eonhae.KO_REVID:
            dest.write_text(api_json("노걸대언해", revid, "== 老乞大諺解上 ==\n# 말"), encoding="utf-8")
        else:
            dest.write_text(api_json("老乞大", revid, "大哥"), encoding="utf-8")
        return dest

    ko_source, zh_source = eonhae.load_sources(tmp_path, "sang", downloader=download)

    assert ko_source.revid == eonhae.KO_REVID
    assert zh_source.revid == eonhae.VOLUMES["sang"]["zh_revid"]
    assert calls == [
        ("ko.wikisource.org", "431023", "json"),
        ("zh.wikisource.org", "2551540", "json"),
    ]
    assert (tmp_path / eonhae.KO_FILE).is_file()
    assert (tmp_path / eonhae.VOLUMES["sang"]["zh_file"]).is_file()


def test_load_sources_refuses_upstream_file_with_wrong_revid(tmp_path: Path) -> None:
    (tmp_path / eonhae.KO_FILE).write_text(api_json("노걸대언해", "9", "bad"), encoding="utf-8")
    (tmp_path / eonhae.VOLUMES["sang"]["zh_file"]).write_text(
        api_json("老乞大", eonhae.VOLUMES["sang"]["zh_revid"], "大哥"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected 431023"):
        eonhae.load_sources(tmp_path, "sang")


def test_text_parsers_keep_old_hangul_clusters_and_printed_templates() -> None:
    ko = source(
        """
== 老乞大諺解上 ==
# 老乞大諺解上
# 내 高麗ㅅ ᄯᅡ흐로셔 오롸
# 하ᄂᆞᆯ이 어엿비 너기샤/몸이 편안ᄒᆞ면
== 老乞大諺解下 ==
# 읍ᄒᆞ노이다
"""
    )
    lines = eonhae.ko_lines(ko, "sang")
    assert [line.text for line in lines] == ["내高麗ㅅᄯᅡ흐로셔오롸", "하ᄂᆞᆯ이어엿비너기샤몸이편안ᄒᆞ면"]
    assert "ᄒᆞ" in lines[1].chars
    phrases = eonhae.ko_phrases(ko, "sang")
    assert [(phrase.line_index, phrase.phrase_index, phrase.text) for phrase in phrases] == [
        (0, 0, "내高麗ㅅᄯᅡ흐로셔오롸"),
        (1, 0, "하ᄂᆞᆯ이어엿비너기샤"),
        (1, 1, "몸이편안ᄒᆞ면"),
    ]

    zh = source("{{Header2|title=老乞大}}\n大哥{{著}}[[:w:漢兒言語|漢兒言語]]？")
    assert "".join(eonhae.zh_stream(zh)) == "大哥著漢兒言語"


def top(label: str) -> list[str]:
    return [label]


def test_column_grid_fits_ten_equal_columns_from_large_hanja_centres() -> None:
    boxes: list[Box] = []
    labels: list[list[str]] = []
    for k in range(10):
        x = 160 + k * 196
        for y in (500, 900):
            boxes.append(Box(x=x - 62 + (k % 2), y=y, w=124, h=132))
            labels.append(top("U+5929"))
    boxes.append(Box(x=930, y=700, w=90, h=42))
    labels.append(top("U+4EBA"))

    grid = eonhae.fit_column_grid(boxes, labels, 2150)
    assert grid is not None
    assert round(grid.pitch) == 196
    assert len(grid.centers) == 10
    assert grid.centers[0] > grid.centers[-1]


def test_page_layout_separates_readings_and_orders_eonhae_subcolumns() -> None:
    grid = eonhae.PageGrid(
        centers=(250, 150),
        pitch=100,
        spans=((200, 300), (100, 200)),
    )
    boxes = [
        Box(x=220, y=10, w=60, h=60),   # large hanja
        Box(x=248, y=66, w=18, h=18),   # reading, not emitted
        Box(x=225, y=66, w=18, h=18),   # reading, not emitted
        Box(x=225, y=96, w=55, h=55),   # circle
        Box(x=254, y=145, w=18, h=20),  # eonhae right sub-column top
        Box(x=254, y=180, w=18, h=20),  # eonhae right sub-column bottom
        Box(x=214, y=120, w=18, h=20),  # eonhae left sub-column top
        Box(x=214, y=155, w=18, h=20),  # eonhae left sub-column bottom
        Box(x=120, y=5, w=60, h=60),    # next column large hanja ends the run
    ]
    labels = [["U+5929"], ["x"], ["x"], [CIRCLE], ["h"], ["h"], ["h"], ["h"], ["U+5730"]]
    layout = eonhae.page_layout(boxes, labels, grid)
    assert [(event.kind, event.box.y) for event in layout.events] == [
        ("hanja", 10),
        ("circle", 96),
        ("eonhae", 145),
        ("eonhae", 180),
        ("eonhae", 120),
        ("eonhae", 155),
        ("hanja", 5),
    ]
    assert layout.reading_boxes == 2
    assert not layout.end_in_eonhae


def test_page_layout_splits_wide_eonhae_pair_at_column_centre() -> None:
    grid = eonhae.PageGrid(centers=(250,), pitch=100, spans=((200, 300),))
    boxes = [
        Box(x=230, y=10, w=45, h=60),
        Box(x=225, y=96, w=55, h=55),
        Box(x=232, y=150, w=36, h=22),
    ]
    labels = [top("U+5929"), top(CIRCLE), top("other")]
    layout = eonhae.page_layout(boxes, labels, grid)
    eonhae_boxes = [event.box for event in layout.events if event.kind == "eonhae"]
    assert eonhae_boxes == [
        Box(x=250, y=150, w=18, h=22),
        Box(x=232, y=150, w=18, h=22),
    ]


def test_pitch_relative_kind_rules_do_not_promote_wide_reading_pairs() -> None:
    assert eonhae.kind(Box(x=0, y=0, w=120, h=96), "U+5929", 200) == "hanja"
    assert eonhae.kind(Box(x=0, y=0, w=115, h=120), "U+5929", 200) == "small"
    assert eonhae.kind(Box(x=0, y=0, w=80, h=40), "U+5929", 200) == "small"
    assert eonhae.kind(Box(x=0, y=0, w=106, h=100), CIRCLE, 200) == "circle"
    assert eonhae.kind(Box(x=0, y=0, w=70, h=92), CIRCLE, 200) == "small"


def phrase(index: int, text: str) -> eonhae.KoPhrase:
    return eonhae.KoPhrase(index, index, 0, text, text, tuple(text))


def shape_index(classes: list[str]) -> eonhae.HangulShapeIndex:
    return eonhae.hangul_shape_index(classes, "fake/e0")


JAMO_CLASSES = ["U+1100", "U+1102", "U+1103", "U+1105", "U+1106", "U+1107", "U+1109", "U+110B", "other"]


def reads(*order: str) -> list[float]:
    """A model row reading the given jamo, most likely first; every other class barely."""
    row = [0.001] * len(JAMO_CLASSES)
    for rank, char in enumerate(order):
        row[JAMO_CLASSES.index(f"U+{ord(char):04X}")] = 0.5 / (rank + 1)
    return row


def test_eonhae_classifier_assignment_finds_phrase_after_cursor_drift() -> None:
    index = shape_index(["U+1100", "U+1102", "U+1103", "U+1105", "other"])
    phrases = [
        phrase(0, "ᄀᄀ"),
        phrase(1, "ᄀᄂ"),
        phrase(2, "ᄂᄀ"),
        phrase(3, "ᄅᄀ"),
        phrase(4, "ᄀᄅ"),
        phrase(5, "ᄂᄃ"),
    ]
    rows = [
        [0.02, 0.92, 0.03, 0.01, 0.02],
        [0.01, 0.02, 0.93, 0.02, 0.02],
    ]

    assigned = eonhae.assign_eonhae_run(rows, phrases, 0, index, window=6, margin=1.0, min_evidence=2)

    assert assigned.accepted
    assert assigned.phrase == phrases[5]
    assert assigned.margin is not None and assigned.margin >= 1.0


def test_eonhae_cursor_reacquisition_finds_far_phrase_after_drops() -> None:
    index = shape_index(["U+1100", "U+1102", "U+1103", "other"])
    phrases = [
        phrase(0, "ᄀᄀ"),
        phrase(20, "ᄂᄃ"),
    ]
    rows = [
        [0.02, 0.92, 0.03, 0.03],
        [0.02, 0.03, 0.92, 0.03],
    ]
    state = eonhae.EonhaeCursorState().accept(0)
    for _ in range(8):
        state = state.drop()

    assert state.center == 9
    assert state.uses_global_search(8)

    windowed = eonhae.assign_eonhae_run(
        rows,
        phrases,
        state.center,
        index,
        window=4,
        min_evidence=2,
        global_search=False,
        min_index=state.anchor - 2,
    )
    assert not windowed.accepted
    assert windowed.reason == "no candidate phrase"

    reacquired = eonhae.assign_eonhae_run(
        rows,
        phrases,
        state.center,
        index,
        window=4,
        min_evidence=2,
        global_search=True,
    )
    assert reacquired.accepted
    assert reacquired.global_search
    assert reacquired.center == 9
    assert reacquired.phrase == phrases[1]


def test_eonhae_classifier_assignment_refuses_low_margin_tie() -> None:
    index = shape_index(["U+1100", "U+1102", "U+1103", "other"])
    phrases = [phrase(0, "ᄀᄂ"), phrase(1, "ᄀᄃ")]
    rows = [
        [0.9, 0.05, 0.03, 0.02],
        [0.05, 0.45, 0.45, 0.05],
    ]

    assigned = eonhae.assign_eonhae_run(rows, phrases, 0, index, window=1, margin=1.0, min_evidence=2)

    assert not assigned.accepted
    assert assigned.reason == "ambiguous phrase"


def test_a_repeated_phrase_is_one_choice_at_the_occurrence_nearest_the_cursor() -> None:
    index = shape_index(["U+1100", "U+1102", "U+1103", "other"])
    phrases = [phrase(3, "ᄀᄂ"), phrase(9, "ᄃᄃ"), phrase(40, "ᄀᄂ")]
    rows = [
        [0.9, 0.05, 0.03, 0.02],
        [0.05, 0.9, 0.03, 0.02],
    ]

    assigned = eonhae.assign_eonhae_run(rows, phrases, 38, index, global_search=True, margin=1.0, min_evidence=2)

    assert assigned.accepted
    assert assigned.phrase.index == 40


def test_a_search_of_the_whole_volume_does_not_go_back_past_the_last_phrase() -> None:
    index = shape_index(JAMO_CLASSES)
    phrases = [phrase(3, "ᄀᄂ"), phrase(50, "ᄃᄃ")]
    rows = [reads("ᄀ", "ᄅ", "ᄆ", "ᄇ", "ᄉ"), reads("ᄂ", "ᄅ", "ᄆ", "ᄇ", "ᄉ")]

    behind = eonhae.assign_eonhae_run(rows, phrases, 48, index, global_search=True, min_evidence=2)
    ahead = eonhae.assign_eonhae_run(rows, phrases, 48, index, global_search=True, min_index=46, min_evidence=2)

    assert behind.accepted and behind.phrase.index == 3
    assert not ahead.accepted


def test_eonhae_classifier_assignment_refuses_when_argmax_agreement_is_low() -> None:
    index = shape_index(JAMO_CLASSES)
    phrases = [phrase(0, "ᄀᄂᄃ")]
    rows = [reads("ᄅ", "ᄆ", "ᄇ", "ᄉ", "ᄋ")] * 3

    assigned = eonhae.assign_eonhae_run(rows, phrases, 0, index, min_agree=0.6)

    assert not assigned.accepted
    assert assigned.reason == "classifier disagrees"
    assert assigned.agree_fraction == 0


def test_eonhae_classifier_assignment_refuses_all_hanja_phrase_without_hangul_evidence() -> None:
    index = shape_index(["U+1100", "U+1102", "other"])
    phrases = [phrase(0, "母親")]
    rows = [
        [0.90, 0.05, 0.05],
        [0.05, 0.90, 0.05],
    ]

    assigned = eonhae.assign_eonhae_run(rows, phrases, 0, index)

    assert not assigned.accepted
    assert assigned.reason == "too little evidence"


def test_eonhae_classifier_assignment_refuses_two_hangul_chars_with_default_evidence_floor() -> None:
    index = shape_index(["U+1100", "U+1102", "other"])
    phrases = [phrase(0, "ᄀᄂ")]
    rows = [
        [0.90, 0.05, 0.05],
        [0.05, 0.90, 0.05],
    ]

    assigned = eonhae.assign_eonhae_run(rows, phrases, 0, index)

    assert not assigned.accepted
    assert assigned.reason == "too little evidence"


def test_eonhae_classifier_assignment_accepts_three_known_hangul_chars() -> None:
    index = shape_index(["U+1100", "U+1102", "U+1103", "other"])
    phrases = [phrase(0, "ᄀᄂᄃ")]
    rows = [
        [0.90, 0.05, 0.03, 0.02],
        [0.05, 0.90, 0.03, 0.02],
        [0.05, 0.03, 0.90, 0.02],
    ]

    assigned = eonhae.assign_eonhae_run(rows, phrases, 0, index)

    assert assigned.accepted
    assert assigned.evidence == 3
    assert assigned.phrase == phrases[0]


def test_eonhae_unit_decisions_drop_disagreeing_known_label_and_keep_unknown_label() -> None:
    index = shape_index(JAMO_CLASSES)
    ko = phrase(0, "ᄀᄂᄌ")  # ᄌ has no class
    rows = [reads("ᄅ", "ᄆ", "ᄇ", "ᄉ", "ᄋ"), reads("ᄅ", "ᄂ", "ᄇ", "ᄉ", "ᄋ"), reads("ᄀ")]

    decisions = eonhae.eonhae_hangul_unit_decisions(ko, rows, index)

    assert decisions[0].classifier_agrees is False
    assert decisions[1].classifier_agrees is True
    assert decisions[2].classifier_agrees is None
    assert decisions[2].hangul_p is None


def test_eonhae_restricted_distribution_ignores_classes_outside_candidate_union() -> None:
    index = shape_index(["U+1100", "U+1102", "U+1103", "other"])

    distribution = eonhae.restricted_hangul_distribution(
        [0.01, 0.03, 0.94, 0.02],
        {"ᄀ", "ᄂ"},
        index,
    )

    assert distribution == pytest.approx({"ᄂ": 0.75, "ᄀ": 0.25})


def run_event(box: Box, *, column: int = 0, half: int = 0) -> tuple[str, eonhae.LayoutEvent]:
    return ("p", eonhae.LayoutEvent("eonhae", box, ("h",), column, half))


def test_eonhae_run_geometry_rejects_vertical_overlap_within_a_half() -> None:
    problems = eonhae.eonhae_run_geometry_problems(
        [
            run_event(Box(x=100, y=100, w=60, h=80)),
            run_event(Box(x=102, y=150, w=62, h=80)),
        ],
        {"p": eonhae.EonhaePageGeometry(median_small_height=80, half_column_width=120)},
    )
    assert [problem.reason for problem in problems] == ["overlap"]


def test_eonhae_run_geometry_rejects_small_boxes() -> None:
    problems = eonhae.eonhae_run_geometry_problems(
        [run_event(Box(x=100, y=100, w=35, h=39))],
        {"p": eonhae.EonhaePageGeometry(median_small_height=80, half_column_width=100)},
    )
    assert {problem.reason for problem in problems} == {"small box"}
    assert len(problems) == 2


def test_eonhae_run_geometry_rejects_large_gaps_within_a_half() -> None:
    problems = eonhae.eonhae_run_geometry_problems(
        [
            run_event(Box(x=100, y=100, w=60, h=60)),
            run_event(Box(x=101, y=260, w=60, h=60)),
        ],
        {"p": eonhae.EonhaePageGeometry(median_small_height=60, half_column_width=120)},
    )
    assert [problem.reason for problem in problems] == ["gap"]


def test_eonhae_run_geometry_catches_page_six_cancellation_shape() -> None:
    problems = eonhae.eonhae_run_geometry_problems(
        [
            run_event(Box(x=886, y=2170, w=62, h=45), column=5),
            run_event(Box(x=889, y=2200, w=60, h=58), column=5),
            run_event(Box(x=897, y=2229, w=35, h=77), column=5),
        ],
        {"p": eonhae.EonhaePageGeometry(median_small_height=78, half_column_width=98)},
    )
    reasons = [problem.reason for problem in problems]
    assert "overlap" in reasons
    assert "small box" in reasons


def test_hanja_alignment_searches_near_the_cursor_and_refuses_bad_pages() -> None:
    events = (
        eonhae.LayoutEvent("hanja", Box(x=0, y=0, w=50, h=50), ("U+5929",), 0),
        eonhae.LayoutEvent("hanja", Box(x=0, y=60, w=50, h=50), ("U+5730",), 0),
    )
    aligned = eonhae.align_hanja(events, list("月天地日"), 0, {"U+5929", "U+5730"}, window=4)
    assert aligned.accepted and aligned.offset == 1 and aligned.chars == ("天", "地")
    assert aligned.matched == (True, True)

    refused = eonhae.align_hanja(
        events,
        list("月火水日"),
        0,
        {"U+5929", "U+5730", "U+6708", "U+706B", "U+6C34", "U+65E5"},
        window=4,
    )
    assert not refused.accepted


def test_hanja_alignment_leaves_spurious_events_unmatched() -> None:
    events = (
        eonhae.LayoutEvent("hanja", Box(x=0, y=0, w=50, h=50), ("U+5929",), 0),
        eonhae.LayoutEvent("hanja", Box(x=0, y=60, w=50, h=50), ("U+7D66",), 0),
        eonhae.LayoutEvent("hanja", Box(x=0, y=120, w=50, h=50), ("U+5730",), 0),
        eonhae.LayoutEvent("hanja", Box(x=0, y=180, w=50, h=50), ("U+65E5",), 0),
    )
    aligned = eonhae.align_hanja(
        events,
        list("月天地日"),
        0,
        {"U+5929", "U+7D66", "U+5730", "U+65E5"},
        window=4,
    )
    assert aligned.accepted
    assert aligned.chars == ("天", None, "地", "日")
    assert aligned.matched == (True, False, True, True)
    assert aligned.refused == 0


def test_hanja_alignment_prefers_upper_neighbour_for_weak_reading_row_match() -> None:
    events = (
        eonhae.LayoutEvent("hanja", Box(x=100, y=100, w=118, h=150), ("U+5FD8",), 0),
        eonhae.LayoutEvent("hanja", Box(x=105, y=270, w=158, h=96), ("U+5553",), 0),
        eonhae.LayoutEvent("hanja", Box(x=100, y=430, w=139, h=155), ("U+9EBC",), 0),
    )
    aligned = eonhae.align_hanja(events, list("怎麼"), 0, {"U+5FD8", "U+5553", "U+9EBC"}, window=2)
    assert aligned.chars == ("怎", None, "麼")
    assert aligned.matched == (True, False, True)


def test_script_and_codepoints_for_small_eonhae_characters() -> None:
    assert eonhae.script_of("高") == "han"
    assert eonhae.script_of("ᄒᆞ") == "hangul"
    assert eonhae.codepoints("ᄒᆞ") == "U+1112 U+119E"


def test_a_private_use_character_names_no_character() -> None:
    assert eonhae.names_a_character("天")
    assert eonhae.names_a_character("ᄒᆞ")
    assert not eonhae.names_a_character("\ue839")
