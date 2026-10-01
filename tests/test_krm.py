"""Tests for placing KRM headwords on a facsimile page. No model runs: boxes and class rankings are given."""

from __future__ import annotations

from pathlib import Path

from glyph_atlas import krm
from glyph_atlas.glossary import Glyph
from glyph_atlas.schema import Box

UNIT = 80.0
HEADER = "# HDIC Project\n# KRM\n"
COLUMNS_TSV = ("entry_id", "hanzi_id", "kazama_location", "tenri_location", "volume_name", "radical_name",
               "volume_radical_index", "hanzi_entry", "original_entry", "definition")
MAIN = HEADER + "\t".join(COLUMNS_TSV) + "\n"


def code(char: str) -> str:
    return f"U+{ord(char):04X}"


def test_a_headword_is_read_as_its_written_glyphs() -> None:
    assert krm.headword("人", "〇") == (Glyph("人"),)
    assert krm.headword("一／人", "〇／〇") == (Glyph("一"), Glyph("人"))
    assert krm.headword("五／ー（人）", "〇／〇") == (Glyph("五"), Glyph(krm.MARK, "人"))
    assert krm.headword("佛", "仏") == (Glyph("仏", "佛"),)
    assert krm.headword("將／為／［便］", "〇") == (Glyph("將"), Glyph("為"))
    assert krm.headword("⿰亻胃", "〇") == (Glyph("⿰亻胃"),)
    assert krm.headword("■", "〇") == (Glyph("〓"),)
    assert krm.headword("僕β", "〇")[0].text == "僕β"


def test_a_headword_with_an_unencoded_form_has_no_code_point() -> None:
    assert krm.encoded(Glyph("仏", "佛")) == "U+4ECF"
    assert krm.encoded(Glyph("⿰亻胃")) is None
    assert krm.encoded(Glyph("僕β", "僕")) is None
    assert krm.encoded(Glyph(krm.MARK, "人")) is None
    assert krm.encoded(Glyph("〓")) is None


def test_entries_and_frames_are_read_from_the_tsv(tmp_path: Path) -> None:
    (tmp_path / "krm_main.tsv").write_text(
        MAIN + "F00001\tS00001\tK01001310\tTa023310\t仏上\t人\tv1#1\t人\t〇\t音仁\n"
        "F00002\tS00002\tK01001331\tTa023331\t仏上\t人\tv1#1\t一／人\t〇／〇\tヒトリ\n", encoding="utf-8")
    (tmp_path / "krm_ndl.tsv").write_text(
        HEADER + "Book\tRadical\tKazama\tTenri\tNDL_url\n"
        "仏上\t人\t1\t23\thttps://dl.ndl.go.jp/info:ndljp/pid/2586891/6\n"
        "仏上\t人\t1\t23\thttps://dl.ndl.go.jp/info:ndljp/pid/2586891/6\n"
        "法上\t水\t5\t37\thttps://dl.ndl.go.jp/info:ndljp/pid/2586895/19\n"
        "法上\t水\t5\t37\thttps://dl.ndl.go.jp/info:ndljp/pid/2586895/22\n", encoding="utf-8")
    entries = krm.read_entries(tmp_path / "krm_main.tsv")
    assert [(e.page, e.line, e.segment, e.order) for e in entries] == [(23, 3, 1, 0), (23, 3, 3, 1)]
    assert entries[1].glyphs == (Glyph("一"), Glyph("人"))
    frames, conflicts = krm.read_frames(tmp_path / "krm_ndl.tsv")
    assert frames == {("仏上", 23): ("2586891", 6)}
    assert conflicts == {("法上", 37)}


def test_evenly_spaced_lines_are_fitted_through_scattered_values() -> None:
    values = [1000 - 250 * k + jitter for k, jitter in zip(range(8), (0, 6, -5, 3, -2, 4, -6, 1), strict=True)]
    _, start, step = krm.fit(values + [40.0], 8, 180, 360, 0.25, anchor=1000, direction=-1)
    assert start == 1000 and abs(step - 250) <= 2


def entry(entry_id: str, line: int, segment: int, order: int, *glyphs: str) -> krm.Entry:
    return krm.Entry(entry_id, entry_id, "仏上", 24, line, segment, order, f"Ta024{line}{segment}{order}", "", "人",
                     "", "", "", tuple(Glyph(g) for g in glyphs))


def headword_box(column: float, top: float) -> Box:
    return Box(x=int(column - 80), y=int(top), w=160, h=160)


def page(columns: list[float], tiers: list[float]) -> list[Box]:
    """A page with one headword at the top of every cell, and small gloss boxes below each."""
    boxes = []
    for x in columns:
        for y in tiers:
            boxes.append(headword_box(x, y))
            boxes += [Box(x=int(x - 30), y=int(y + 200 + 90 * k), w=60, h=70) for k in range(4)]
    return boxes


COLUMNS = [3000 - 250 * k for k in range(8)]
TIERS = [500, 1300, 2100, 2900]


def test_a_page_grid_is_anchored_on_its_outer_columns() -> None:
    grids = krm.page_grids(page(COLUMNS, TIERS), UNIT)
    right = grids["right"]
    assert [round(x) for x in right.columns] == COLUMNS
    assert [round(y) for y in right.tiers] == TIERS


def test_headwords_are_kept_where_the_classifier_reads_them() -> None:
    boxes = page(COLUMNS, TIERS)
    grid = krm.page_grids(boxes, UNIT)["right"]
    labels = {(1, 1): "仿", (1, 2): "像", (2, 1): "傮"}
    entries = [entry("F1", 1, 1, 0, "仿"), entry("F2", 1, 2, 0, "像"), entry("F3", 2, 1, 0, "傮")]

    def rank(box: Box) -> list[str]:
        for (line, seg), char in labels.items():
            target = headword_box(COLUMNS[line - 1], TIERS[seg - 1])
            if (box.x, box.y) == (target.x, target.y):
                return [code(char)] if char != "傮" else [code("僧")]
        return [code("ノ")]

    result = krm.place(entries, boxes, grid, UNIT, rank, known={code("仿"), code("像"), code("僧"), code("ノ")})
    kept = {p.entry.entry_id: p for p in result.pairs if p.kept}
    assert set(kept) == {"F1", "F2", "F3"}
    assert kept["F1"].verdict is True and kept["F3"].verdict is None  # 傮: no class, anchored at the tier line
    assert (kept["F2"].box.x, kept["F2"].box.y) == (COLUMNS[0] - 80, TIERS[1])


def test_a_headword_the_classifier_refuses_is_left_out() -> None:
    boxes = page(COLUMNS, TIERS)
    grid = krm.page_grids(boxes, UNIT)["right"]
    result = krm.place([entry("F1", 1, 1, 0, "仿")], boxes, grid, UNIT, lambda b: [code("人")],
                       known={code("仿"), code("人")})
    assert not [p for p in result.pairs if p.kept]
    assert result.counts.get("refused") == 1


def test_a_page_mostly_refused_is_left_out_whole() -> None:
    boxes = page(COLUMNS, TIERS)
    grid = krm.page_grids(boxes, UNIT)["right"]
    entries = [entry(f"F{k}", k, 1, 0, "仿") for k in range(1, 5)] + [entry("F9", 5, 1, 0, "傮")]

    def rank(box: Box) -> list[str]:
        return [code("仿")] if box.x == COLUMNS[0] - 80 else [code("人")]

    result = krm.place(entries, boxes, grid, UNIT, rank, known={code("仿"), code("人")})
    assert result.counts.get("page-refused") == 1
    assert not [p for p in result.pairs if p.kept]


def test_a_cell_pairs_its_glyphs_past_a_missed_mark() -> None:
    glyphs = [Glyph(krm.MARK, "人"), Glyph("等")]
    boxes = [Box(x=0, y=0, w=160, h=160)]
    pairs = krm.align_cell(glyphs, boxes, lambda i, j: True if glyphs[i].text == "等" else None, [False])
    assert pairs == [(1, 0)]


def test_tiers_are_not_fitted_at_half_their_spacing() -> None:
    boxes = []
    for x in COLUMNS:
        for y in TIERS:
            boxes += [headword_box(x, y + offset) for offset in (0, 200, 400)]
    grid = krm.page_grids(boxes, UNIT)["right"]
    assert [round(y) for y in grid.tiers] == TIERS


def test_an_unjudged_glyph_is_left_out_when_its_pairing_is_in_doubt() -> None:
    boxes = page(COLUMNS, TIERS) + [headword_box(COLUMNS[0], TIERS[0] + 200)]
    grid = krm.page_grids(boxes, UNIT)["right"]
    cell = entry("F1", 1, 1, 0, "人", "⿰亻胃", "僕β")

    def rank(box: Box) -> list[str]:
        return [code("人")] if (box.x, box.y) == (COLUMNS[0] - 80, TIERS[0]) else [code("ノ")]

    result = krm.place([cell], boxes, grid, UNIT, rank, known={code("人"), code("ノ")})
    kept = [p.glyph.text for p in result.pairs if p.kept]
    assert kept == ["人"]  # three glyphs on two boxes: which unjudged glyph sits on the second is a guess


def test_two_pages_never_share_one_grid() -> None:
    def placement(agreed: int) -> krm.Placement:
        result = krm.Placement()
        result.pairs = [krm.Pair(entry("F", 1, 1, 0, "人"), 0, Glyph("人"), Box(x=0, y=0, w=1, h=1), [], True, True)
                        for _ in range(agreed)]
        return result

    tried = {24: {"right": placement(5), "left": placement(1)}, 25: {"right": placement(4), "left": placement(0)}}
    assert krm.assign_pages(tried) == {24: "right", 25: "left"}
    assert krm.assign_pages({23: {"right": placement(0), "left": placement(0)}}) == {23: "left"}
    three = {page: {"right": placement(1), "left": placement(1)} for page in (36, 37, 38)}
    assert krm.assign_pages(three) == {}
