"""Tests for placing HDIC headwords on a facsimile page. No model runs: boxes and class rankings are given."""

from __future__ import annotations

from pathlib import Path

from glyph_atlas import hdic
from glyph_atlas.glossary import Glyph
from glyph_atlas.schema import Box

UNIT = 80.0
KRM = hdic.DICTIONARIES["krm"].layout
EVEN_RIGHT = hdic.DICTIONARIES["krm"].right
HEADER = "# HDIC Project\n# KRM\n"
COLUMNS_TSV = ("entry_id", "hanzi_id", "kazama_location", "tenri_location", "volume_name", "radical_name",
               "volume_radical_index", "hanzi_entry", "original_entry", "definition")
MAIN = HEADER + "\t".join(COLUMNS_TSV) + "\n"


def code(char: str) -> str:
    return f"U+{ord(char):04X}"


def test_a_headword_is_read_as_its_written_glyphs() -> None:
    assert hdic.headword("人", "〇") == (Glyph("人"),)
    assert hdic.headword("一／人", "〇／〇") == (Glyph("一"), Glyph("人"))
    assert hdic.headword("五／ー（人）", "〇／〇") == (Glyph("五"), Glyph(hdic.MARK, "人"))
    assert hdic.headword("佛", "仏") == (Glyph("仏", "佛"),)
    assert hdic.headword("將／為／［便］", "〇") == (Glyph("將"), Glyph("為"))
    assert hdic.headword("⿰亻胃", "〇") == (Glyph("⿰亻胃"),)
    assert hdic.headword("■", "〇") == (Glyph("〓"),)
    assert hdic.headword("僕β", "〇")[0].text == "僕β"


def test_a_headword_with_an_unencoded_form_has_no_code_point() -> None:
    assert hdic.encoded(Glyph("仏", "佛")) == "U+4ECF"
    assert hdic.encoded(Glyph("⿰亻胃")) is None
    assert hdic.encoded(Glyph("僕β", "僕")) is None
    assert hdic.encoded(Glyph(hdic.MARK, "人")) is None
    assert hdic.encoded(Glyph("〓")) is None


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
    entries, frames, conflicts = hdic.read_krm(tmp_path)
    assert [(e.page, e.line, e.segment, e.order) for e in entries] == [(23, 3, 1, 0), (23, 3, 3, 1)]
    assert entries[1].glyphs == (Glyph("一"), Glyph("人"))
    assert entries[0].meta["kazama_location"] == "K01001310"
    assert frames == {("仏上", 23): ("2586891", 6)}
    assert conflicts == {("法上", 37)}


def test_ktb_reads_the_large_headwords_and_their_half_leaves(tmp_path: Path) -> None:
    columns = ("TBID", "TB_vol_radical", "TB_radical", "Entry", "Entry_type", "Entry_diff", "TB_def", "SYID",
               "YYID", "TB_remarks", "Lv_page", "Zang_page")
    rows = [("1_016_A51", "一", "Regular_seal"), ("1_016_A52", "弌", "Embedded_clerical"), ("1_016_A53", "天", "Regular_seal"),
            ("1_017_A62-Shirafuji", "𡫚", "Regular"), ("1_019_A321", "禶", "Songben-Yupian")]
    (tmp_path / "KTB.tsv").write_text("# KTB\n" + "\t".join(columns) + "\n" + "".join(
        f"{tid}\tv1#1\t一\t{entry}\t{kind}\t\t\t\t\t\t1\t1\n" for tid, entry, kind in rows), encoding="utf-8")
    (tmp_path / "KTB_ndl.txt").write_text("# KTB\nVol_radical\tKTB_vol\tRadical\tBook_leaf\tNDL_url\n"
                                          "v1#1\tG01\t一\t1_016_A\thttp://dl.ndl.go.jp/info:ndljp/pid/1245837/18\n", encoding="utf-8")
    entries, frames, _ = hdic.read_ktb(tmp_path)
    assert [(e.entry_id, e.page, e.line, e.order) for e in entries] == [("1_016_A51", "1_016_A", 5, 1), ("1_016_A53", "1_016_A", 5, 3)]
    assert frames == {("1", "1_016_A"): ("1245837", 18)}


def test_tsj_takes_the_written_form_where_it_differs(tmp_path: Path) -> None:
    columns = ("SJID", "SJ2ID", "SJ_Rinsen", "SJ_vol_radical", "SJ_radical", "Entry", "Entry_original", "SJ_source",
               "SJ_entry_remarks", "TBID", "SYID")
    (tmp_path / "TSJ_entries.tsv").write_text("# TSJ\n" + "\t".join(columns) + "\n"
        "s0104a601\ts0104a601\t新21-61\tv1#1\t天部第一\t天\t◯\t出所不詳\t\t\t\n"
        "s0104a612\ts0104a612\t新21-62\tv1#1\t天部第一\t𠀘\t⿱⿱一丷兀\t出所不詳\t\t\t\n", encoding="utf-8")
    (tmp_path / "TSJ_ndl.tsv").write_text("# TSJ\nSJ_vol\tSJ_vol_leaf\tSJ_Rinsen_p\tNDL_URL_813.2-Sy968s_1916\tNIJL_Shoryobu_micro\n"
        "1\ts0104a\t21\thttps://dl.ndl.go.jp/info:ndljp/pid/3438195/12\t\n", encoding="utf-8")
    entries, frames, _ = hdic.read_tsj(tmp_path)
    assert [(e.page, e.line, e.order, e.glyphs) for e in entries] == [
        ("s0104a", 6, 1, (Glyph("天"),)), ("s0104a", 6, 12, (Glyph("⿱⿱一丷兀", "𠀘"),))]
    assert frames == {("01", "s0104a"): ("3438195", 12)}


def test_evenly_spaced_lines_are_fitted_through_scattered_values() -> None:
    values = [1000 - 250 * k + jitter for k, jitter in zip(range(8), (0, 6, -5, 3, -2, 4, -6, 1), strict=True)]
    _, start, step = hdic.fit(values + [40.0], 8, 180, 360, 0.25, anchor=1000, direction=-1)
    assert start == 1000 and abs(step - 250) <= 2


def entry(entry_id: str, line: int, segment: int, order: int, *glyphs: str) -> hdic.Entry:
    return hdic.Entry(entry_id, "仏上", 24, line, segment, order, f"Ta024{line}{segment}{order}", tuple(Glyph(g) for g in glyphs))


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
    grids = hdic.page_grids(page(COLUMNS, TIERS), UNIT, KRM)
    right = grids["right"]
    assert [round(x) for x in right.columns] == COLUMNS
    assert [round(y) for y in right.tiers] == TIERS


def test_headwords_are_kept_where_the_classifier_reads_them() -> None:
    boxes = page(COLUMNS, TIERS)
    grid = hdic.page_grids(boxes, UNIT, KRM)["right"]
    labels = {(1, 1): "仿", (1, 2): "像", (2, 1): "傮"}
    entries = [entry("F1", 1, 1, 0, "仿"), entry("F2", 1, 2, 0, "像"), entry("F3", 2, 1, 0, "傮")]

    def rank(box: Box) -> list[str]:
        for (line, seg), char in labels.items():
            target = headword_box(COLUMNS[line - 1], TIERS[seg - 1])
            if (box.x, box.y) == (target.x, target.y):
                return [code(char)] if char != "傮" else [code("僧")]
        return [code("ノ")]

    result = hdic.place(entries, boxes, grid, UNIT, rank, known={code("仿"), code("像"), code("僧"), code("ノ")}, layout=KRM)
    kept = {p.entry.entry_id: p for p in result.pairs if p.kept}
    assert set(kept) == {"F1", "F2", "F3"}
    assert kept["F1"].verdict is True and kept["F3"].verdict is None  # 傮: no class, anchored at the tier line
    assert (kept["F2"].box.x, kept["F2"].box.y) == (COLUMNS[0] - 80, TIERS[1])


def test_a_headword_the_classifier_refuses_is_left_out() -> None:
    boxes = page(COLUMNS, TIERS)
    grid = hdic.page_grids(boxes, UNIT, KRM)["right"]
    result = hdic.place([entry("F1", 1, 1, 0, "仿")], boxes, grid, UNIT, lambda b: [code("人")],
                       known={code("仿"), code("人")}, layout=KRM)
    assert not [p for p in result.pairs if p.kept]
    assert result.counts.get("refused") == 1


def test_a_page_mostly_refused_is_left_out_whole() -> None:
    boxes = page(COLUMNS, TIERS)
    grid = hdic.page_grids(boxes, UNIT, KRM)["right"]
    entries = [entry(f"F{k}", k, 1, 0, "仿") for k in range(1, 5)] + [entry("F9", 5, 1, 0, "傮")]

    def rank(box: Box) -> list[str]:
        return [code("仿")] if box.x == COLUMNS[0] - 80 else [code("人")]

    result = hdic.place(entries, boxes, grid, UNIT, rank, known={code("仿"), code("人")}, layout=KRM)
    assert result.counts.get("page-refused") == 1
    assert "kept" not in result.counts
    assert not [p for p in result.pairs if p.kept]


def test_a_cell_pairs_its_glyphs_past_a_missed_mark() -> None:
    glyphs = [Glyph(hdic.MARK, "人"), Glyph("等")]
    boxes = [Box(x=0, y=0, w=160, h=160)]
    pairs = hdic.align_cell(glyphs, boxes, lambda i, j: True if glyphs[i].text == "等" else None, [False])
    assert pairs == [(1, 0)]


def test_tiers_are_not_fitted_at_half_their_spacing() -> None:
    boxes = []
    for x in COLUMNS:
        for y in TIERS:
            boxes += [headword_box(x, y + offset) for offset in (0, 200, 400)]
    grid = hdic.page_grids(boxes, UNIT, KRM)["right"]
    assert [round(y) for y in grid.tiers] == TIERS


def test_an_unjudged_glyph_is_left_out_when_its_pairing_is_in_doubt() -> None:
    boxes = page(COLUMNS, TIERS) + [headword_box(COLUMNS[0], TIERS[0] + 200)]
    grid = hdic.page_grids(boxes, UNIT, KRM)["right"]
    cell = entry("F1", 1, 1, 0, "人", "⿰亻胃", "僕β")

    def rank(box: Box) -> list[str]:
        return [code("人")] if (box.x, box.y) == (COLUMNS[0] - 80, TIERS[0]) else [code("ノ")]

    result = hdic.place([cell], boxes, grid, UNIT, rank, known={code("人"), code("ノ")}, layout=KRM)
    kept = [p.glyph.text for p in result.pairs if p.kept]
    assert kept == ["人"]  # three glyphs on two boxes: which unjudged glyph sits on the second is a guess


def test_two_pages_never_share_one_grid() -> None:
    def placement(agreed: int) -> hdic.Placement:
        result = hdic.Placement()
        result.pairs = [hdic.Pair(entry("F", 1, 1, 0, "人"), 0, Glyph("人"), Box(x=0, y=0, w=1, h=1), [], True, True)
                        for _ in range(agreed)]
        return result

    tried = {24: {"right": placement(5), "left": placement(1)}, 25: {"right": placement(4), "left": placement(0)}}
    assert hdic.assign_pages(tried, EVEN_RIGHT) == {24: "right", 25: "left"}
    assert hdic.assign_pages({23: {"right": placement(0), "left": placement(0)}}, EVEN_RIGHT) == {23: "left"}
    three = {page: {"right": placement(1), "left": placement(1)} for page in (36, 37, 38)}
    assert hdic.assign_pages(three, EVEN_RIGHT) == {}


TSJ = hdic.DICTIONARIES["tsj"].layout


def running_page() -> tuple[list[Box], list[float]]:
    """A one-tier page: four headwords down each of eight columns, small glosses between them."""
    columns, boxes = [3000 - 250 * k for k in range(8)], []
    for x in columns:
        for k in range(4):
            boxes.append(headword_box(x, 400 + 700 * k))
            boxes += [Box(x=int(x - 30), y=400 + 700 * k + 200 + 90 * g, w=60, h=70) for g in range(4)]
    return boxes, columns


def test_a_one_tier_page_takes_each_column_whole() -> None:
    boxes, columns = running_page()
    grid = hdic.page_grids(boxes, UNIT, TSJ)["right"]
    assert [round(x) for x in grid.columns] == columns
    assert len(grid.tiers) == 1
    assert [b.y for b in hdic.cell_boxes(boxes, grid, 1, 1, UNIT, TSJ)] == [400, 1100, 1800, 2500]


def column_entries(*glyphs: str) -> list[hdic.Entry]:
    return [entry(f"E{k}", 1, 1, k, g) for k, g in enumerate(glyphs)]


def test_an_unjudged_headword_between_two_read_ones_is_kept() -> None:
    boxes, columns = running_page()
    boxes.append(headword_box(columns[0], 2850))  # a large character in the last gloss
    grid = hdic.page_grids(boxes, UNIT, TSJ)["right"]
    reads = {400: "天", 1100: "?", 1800: "地", 2500: "?"}

    def rank(box: Box) -> list[str]:
        char = reads.get(box.y) if box.x == columns[0] - 80 else None
        return [code(char)] if char and char != "?" else [code("ノ")]

    result = hdic.place(column_entries("天", "⿱一丷", "地", "⿰口天"), boxes, grid, UNIT, rank,
                        known={code("天"), code("地"), code("ノ")}, layout=TSJ)
    kept = {p.glyph.text: p.box.y for p in result.pairs if p.kept}
    # ⿱一丷 sits between 天 and 地, both read. ⿰口天 has no read glyph after it, and with a large
    # gloss character in the column the boxes outnumber the headwords, so nothing vouches for it.
    assert kept == {"天": 400, "⿱一丷": 1100, "地": 1800}


KTB = hdic.DICTIONARIES["ktb"].layout


def test_a_tier_head_is_the_headword_after_a_seal_form_or_the_first_character() -> None:
    grid = hdic.Grid(columns=(1000.0,), pitch=250.0, tiers=(500.0, 2300.0), tier_pitch=1800.0)
    seal, head = headword_box(1000, 500), headword_box(1000, 700)
    gloss = [Box(x=970, y=900 + 90 * k, w=60, h=70) for k in range(3)]
    # The lower entry of a later book: headword and gloss written the same size.
    plain = [Box(x=960, y=2300 + 90 * k, w=80, h=80) for k in range(4)]
    boxes = [seal, head, *gloss, *plain]
    assert hdic.cell_boxes(boxes, grid, 1, 1, UNIT, KTB) == [head]
    assert hdic.cell_boxes(boxes, grid, 1, 2, UNIT, KTB) == [plain[0]]


def test_a_second_reader_confirms_but_never_refuses() -> None:
    boxes = page(COLUMNS, TIERS)
    grid = hdic.page_grids(boxes, UNIT, KRM)["right"]
    first = headword_box(COLUMNS[0], TIERS[0])
    entries = [entry("F1", 1, 1, 0, "傮"), entry("F2", 2, 1, 0, "仿")]

    def second(box: Box) -> str:
        return "傮" if (box.x, box.y) == (first.x, first.y) else "人"

    result = hdic.place(entries, boxes, grid, UNIT, lambda b: [code("ノ")], known={code("仿"), code("ノ")},
                        layout=KRM, second=second)
    by_entry = {p.entry.entry_id: p for p in result.pairs}
    assert by_entry["F1"].verdict is True and by_entry["F1"].classifier is None and by_entry["F1"].second == "傮"
    # 仿 is refused by the classifier, which knows it; the second reader's 人 neither refuses nor confirms.
    assert by_entry["F2"].verdict is False


def test_a_neighbour_vouches_only_for_the_glyph_next_to_it() -> None:
    boxes, columns = running_page()
    boxes = [b for b in boxes if b.y != 1800 or abs(b.x + b.w / 2 - columns[0]) > 1]  # the third headword is missed
    boxes.append(headword_box(columns[0], 2850))  # and a large gloss character keeps the count off
    grid = hdic.page_grids(boxes, UNIT, TSJ)["right"]

    def rank(box: Box) -> list[str]:
        return [code({400: "天", 2500: "地"}[box.y])] if box.x == columns[0] - 80 and box.y in (400, 2500) else [code("ノ")]

    result = hdic.place(column_entries("天", "⿱一丷", "⿰口天", "地"), boxes, grid, UNIT, rank,
                        known={code("天"), code("地"), code("ノ")}, layout=TSJ)
    kept = {p.glyph.text for p in result.pairs if p.kept}
    # One of the two unread glyphs sits on the box between 天 and 地; nothing says which.
    assert kept == {"天", "地"}


def test_only_a_headword_box_anchors_a_cell_at_its_tier_line() -> None:
    grid = hdic.Grid(columns=(1000.0,), pitch=250.0, tiers=(500.0,), tier_pitch=800.0)
    gloss_below = [Box(x=970, y=1050 + 90 * k, w=60, h=70) for k in range(3)]

    def kept(*boxes: Box) -> list[bool]:
        result = hdic.place([entry("F1", 1, 1, 0, "傮")], [*boxes, *gloss_below], grid, UNIT, lambda b: [code("ノ")],
                            known={code("ノ")}, layout=KRM)
        return [p.kept for p in result.pairs]

    # A small box on the tier line does not make a headword box well below it the cell's opening...
    assert kept(Box(x=1050, y=500, w=60, h=60), headword_box(1000, 850)) == [False]
    # ...and one above the line does not unseat the headword box that stands on it.
    assert kept(Box(x=1050, y=310, w=60, h=60), headword_box(1000, 500)) == [True]


def test_ktb_leaves_out_lines_whose_tiers_its_order_does_not_settle(tmp_path: Path) -> None:
    columns = ("TBID", "TB_vol_radical", "TB_radical", "Entry", "Entry_type", "Entry_diff", "TB_def", "SYID",
               "YYID", "TB_remarks", "Lv_page", "Zang_page")
    rows = [("1_050_B31", "𠋴", "Regular"), ("1_050_B32", "倓", "Regular"), ("1_050_B33", "㒒", "Regular"),
            ("1_050_B34", "偞", "Regular"), ("1_050_B41", "人", "Omitted"), ("1_050_B42", "仁", "Regular"),
            ("1_050_B51", "仕", "Regular"), ("1_050_B52", "他", "Embedded_clerical"), ("1_050_B53", "代", "Regular")]
    (tmp_path / "KTB.tsv").write_text("# KTB\n" + "\t".join(columns) + "\n" + "".join(
        f"{tid}\tv1#1\t人\t{entry}\t{kind}\t\t\t\t\t\t1\t1\n" for tid, entry, kind in rows), encoding="utf-8")
    (tmp_path / "KTB_ndl.txt").write_text("# KTB\nVol_radical\tKTB_vol\tRadical\tBook_leaf\tNDL_url\n", encoding="utf-8")
    entries, _, _ = hdic.read_ktb(tmp_path)
    assert [(e.entry_id, e.segment) for e in entries] == [("1_050_B51", 1), ("1_050_B53", 2)]


def test_a_second_seal_form_of_an_entry_keeps_its_own_id(tmp_path: Path) -> None:
    columns = ("TBID", "TB_vol_radical", "TB_radical", "Entry", "Entry_type", "Entry_diff", "TB_def", "SYID",
               "YYID", "TB_remarks", "Lv_page", "Zang_page")
    (tmp_path / "KTB.tsv").write_text("# KTB\n" + "\t".join(columns) + "\n"
                                      "1_016_B31\tv1#1\t示\t祉\tRegular_seal\t\t\t\t\t\t1\t1\n", encoding="utf-8")
    (tmp_path / "KTB_ndl_Seal.tsv").write_text(
        "TB_Seal_ID\tNDL_IIIF_Image_API_Base_URI\tx\ty\twidth\theight\n"
        "T1_016_B31\thttps://www.dl.ndl.go.jp/api/iiif/1245837/R0000019\t10\t20\t30\t40\n"
        "T1_016_B31_2\thttps://www.dl.ndl.go.jp/api/iiif/1245837/R0000019\t10\t80\t30\t40\n", encoding="utf-8")
    seals = hdic.read_ktb_seals(tmp_path)
    assert [(s.seal_id, s.entry_id, s.frame, s.entry) for s in seals] == [
        ("1_016_B31", "1_016_B31", 19, "祉"), ("1_016_B31_2", "1_016_B31", 19, "祉")]


def left_page() -> tuple[list[Box], list[float]]:
    """A left page of eight columns whose last column (line 8) holds only two headwords."""
    boxes, columns = running_page()
    boxes = [b for b in boxes if not (abs(b.x + b.w / 2 - columns[-1]) < 1 and b.y >= 1800)]
    return boxes, columns


def column_counts(columns: list[float]) -> list[hdic.Entry]:
    """HDIC's headwords for the page: four in each of lines 1 to 7, two in line 8, none of them classed."""
    return ([entry(f"L{line}-{k}", line, 1, k, "傮") for line in range(1, 8) for k in range(4)]
            + [entry(f"L8-{k}", 8, 1, k, "傮") for k in range(2)])


def test_a_grid_anchored_one_column_in_is_moved_outward() -> None:
    boxes, columns = left_page()
    # Anchored on line 7, the grid sits one column to the right of the page's own.
    fitted = hdic.Grid(tuple(c + 250 for c in columns), 250.0, (500.0,), 3200.0, outward=1)
    result = hdic.place(column_counts(columns), boxes, fitted, UNIT, lambda b: [code("ノ")], {code("ノ")}, TSJ)
    assert result.counts.get("column-shifted") == 1
    assert {p.box.x + p.box.w // 2 for p in result.pairs if p.entry.line == 8} == {int(columns[-1])}


def test_a_well_fitted_grid_stays_where_it_is() -> None:
    boxes, columns = left_page()
    grid = hdic.Grid(tuple(columns), 250.0, (500.0,), 3200.0, outward=1)
    result = hdic.place(column_counts(columns), boxes, grid, UNIT, lambda b: [code("ノ")], {code("ノ")}, TSJ)
    assert "column-shifted" not in result.counts  # nothing lies beyond line 8


def test_counts_alone_do_not_move_a_grid_over_a_page_hdic_barely_fills() -> None:
    boxes, columns = running_page()
    boxes.append(headword_box(columns[0], 2850))
    boxes += [headword_box(columns[0] + 250, 400 + 700 * k) for k in range(4)]  # a column beyond line 1
    grid = hdic.Grid(tuple(columns), 250.0, (500.0,), 3200.0, outward=-1)
    result = hdic.place(column_entries("傮", "傮", "傮", "傮"), boxes, grid, UNIT, lambda b: [code("ノ")], {code("ノ")}, TSJ)
    assert "column-shifted" not in result.counts


def test_a_page_whose_column_nothing_settles_keeps_nothing() -> None:
    boxes, columns = left_page()
    fitted = hdic.Grid(tuple(c + 250 for c in columns), 250.0, (500.0,), 3200.0, outward=1)
    # HDIC fills only lines 6 to 8, as on the first page of a volume after its table of contents.
    entries = [e for e in column_counts(columns) if e.line >= 6]
    result = hdic.place(entries, boxes, fitted, UNIT, lambda b: [code("ノ")], {code("ノ")}, TSJ)
    assert result.counts.get("column-ambiguous") == 1
    assert not [p for p in result.pairs if p.kept]


def test_page_grids_point_outward_and_a_sparse_outer_column_is_recovered() -> None:
    boxes, columns = running_page()
    # The right page's own line 1 holds only two headwords, too few to anchor on.
    boxes = [b for b in boxes if not (abs(b.x + b.w / 2 - columns[0]) < 1 and b.y >= 1800)]
    grids = hdic.page_grids(boxes, UNIT, TSJ)
    right = grids["right"]
    assert right.outward == -1 and grids["left"].outward == 1
    assert round(right.columns[0]) == columns[1]  # anchored one column in
    entries = ([entry(f"L1-{k}", 1, 1, k, "傮") for k in range(2)]
               + [entry(f"L{line}-{k}", line, 1, k, "傮") for line in range(2, 9) for k in range(4)])
    result = hdic.place(entries, boxes, right, UNIT, lambda b: [code("ノ")], {code("ノ")}, TSJ)
    assert result.counts.get("column-shifted") == 1
    assert {p.box.x + p.box.w // 2 for p in result.pairs if p.entry.line == 1} == {columns[0]}


def test_a_ruler_beyond_the_outer_column_does_not_unseat_a_page() -> None:
    boxes, columns = running_page()
    ruler = [Box(x=columns[0] + 230, y=400 + 300 * k, w=20, h=200) for k in range(8)]  # thin, tall: marks
    grid = hdic.page_grids(boxes, UNIT, TSJ)["right"]
    entries = [entry(f"L{line}-{k}", line, 1, k, "傮") for line in range(1, 4) for k in range(4)]
    result = hdic.place(entries, boxes + ruler, grid, UNIT, lambda b: [code("ノ")], {code("ノ")}, TSJ)
    assert "column-ambiguous" not in result.counts and "column-shifted" not in result.counts
