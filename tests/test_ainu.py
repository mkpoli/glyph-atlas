"""Tests for `glyph_atlas.ainu`: the line boxes derived for the アイヌ関連資料 transcriptions.

The geometry in these tests is the one measured on a page of 蝦夷紀行 (see the module's
`columns_of`): characters around 30 px wide, a step of about 15 px inside a line, ink columns whose
white space is 12 px, and 50 px from one line's centre to the next. Everything here is synthetic
boxes and a synthetic page: no detector, no page image, no network.
"""

from __future__ import annotations

import csv

import pytest

from glyph_atlas import ainu
from glyph_atlas.schema import Box, Line, Page


def column(x: int, ys: tuple[int, ...] = (100, 140, 180), w: int = 30, h: int = 40) -> list[Box]:
    """One vertical line of three characters, centred on `x`."""
    return [Box(x=x - w // 2, y=y, w=w, h=h) for y in ys]


def page(page_id: str = "hk:d:0", width: int = 1350, height: int = 1000) -> Page:
    return Page(id=page_id, document_id="hk:d", seq=0, canvas="c", image="i", width=width,
                height=height)


def line(seq: int, text: str, page_id: str = "hk:d:0") -> Line:
    """A transcribed line as the import writes one: text and no box."""
    return Line(id=f"{page_id}:L{seq}", page_id=page_id, seq=seq, text_raw=text, text=text)


def test_columns_come_back_right_to_left() -> None:
    """A vertical Japanese page is read from the right, so the first column is the rightmost."""
    boxes = column(100) + column(150) + column(200)
    found = ainu.columns_of(boxes)
    assert len(found) == 3
    centres = [sum(ainu.centre_of(boxes[index]) for index in group) / len(group) for group in found]
    assert centres == [200.0, 150.0, 100.0]


def test_a_pause_inside_a_line_does_not_split_it() -> None:
    """A line whose characters pause by more than a step is still one line.

    Characters are 30 px wide, so the step threshold is 15 px and the merge threshold 27 px. The
    measured page leans 7 to 13 px over a whole line, and a 20 px step is the case the two rules
    exist for: over the step, under the merge. The next line's 55 px step stays cut.
    """
    leaned = column(100) + [Box(x=105, y=220, w=30, h=40)]
    assert len(ainu.columns_of(leaned)) == 1, "the step is cut and the merge puts it back"
    assert len(ainu.columns_of(leaned, merge_ratio=0.5)) == 2, "a 15 px merge leaves the cut"
    apart = column(100) + column(155)
    assert len(ainu.columns_of(apart)) == 2, "the next line is 55 px from the first"


def test_a_gap_wider_than_the_step_ends_the_column() -> None:
    """Two lines of a page are more than a step apart, which is what makes them two columns."""
    boxes = column(100) + column(200)
    assert len(ainu.columns_of(boxes)) == 2
    assert len(ainu.columns_of(boxes, gap_ratio=5.0, merge_ratio=2.0)) == 1, "a wider step joins them"


def test_a_small_mark_beside_a_line_belongs_to_it() -> None:
    """A mark a hand put beside a line is not a line of its own."""
    boxes = column(100) + [Box(x=112, y=300, w=8, h=8)]
    grouped = ainu.columns_of(boxes)
    assert len(grouped) == 1 and len(grouped[0]) == 4
    assert len(ainu.columns_of(boxes, merge_ratio=0.1)) == 2, "the mark is a detection of its own"


def test_an_empty_page_is_not_an_error() -> None:
    """A page the detector found nothing on is a page with no columns, not a crash."""
    assert ainu.columns_of([]) == []
    assert ainu.derive_page(page(), [line(0, "あ")], []).reason == "no detection"
    assert ainu.regions_of([]) == []
    assert ainu.histogram([]) == "(no detections)"


def test_the_histogram_shows_where_the_ink_is() -> None:
    """The profile is for eyeballing a grouping: a line at 150 shows right of a line at 100."""
    boxes = column(100) + column(150) + [Box(x=600, y=100, w=20, h=30)]
    profile = ainu.histogram(boxes, width=40)
    assert len(profile) == 40
    used = [index for index, mark in enumerate(profile) if mark != " "]
    assert used == sorted(used), "the profile reads left to right"
    assert used[0] < 20 <= used[-1], "the text block sits left of the mark at x=600"


def test_regions_split_a_spread_at_a_wide_gap() -> None:
    """A text block on one leaf and cataloguing marks on the other are counted apart."""
    boxes = column(100) + column(150) + [Box(x=900, y=80, w=20, h=20)]
    regions = ainu.regions_of(boxes)
    assert len(regions) == 2
    assert [len(ainu.columns_of(region)) for region in regions] == [1, 2], "right leaf first"


def test_regions_leave_one_text_block_whole() -> None:
    """No gap inside a dense text block reaches the region cut, so the page stays one region."""
    boxes = column(100) + column(150) + column(200) + column(250)
    regions = ainu.regions_of(boxes)
    assert len(regions) == 1 and len(ainu.columns_of(regions[0])) == 4


def test_a_paired_page_gives_every_line_the_box_of_its_column() -> None:
    """Twenty columns for twenty lines: line 0 takes the rightmost column's ink, line 19 the leftmost."""
    boxes: list[Box] = []
    for index in range(20):
        boxes.extend(column(1300 - index * 50, ys=(100, 145, 190), w=30, h=40))
    lines = [line(seq, "あ" * 3) for seq in range(20)]
    derivation = ainu.derive_page(page(), lines, boxes)
    assert derivation.paired and derivation.reason == "paired"
    first = derivation.line_box(0)
    last = derivation.line_box(19)
    assert first is not None and last is not None
    assert first.x > last.x, "the first line is the rightmost column"
    assert first.w <= 40 and first.h >= 120, "the box is the union of that column's ink"
    assert derivation.line_box(20) is None, "there is no twenty-first column"


def test_a_count_mismatch_pairs_nothing() -> None:
    """Nineteen columns for twenty lines is not a pairing, and says so rather than guessing."""
    boxes: list[Box] = []
    for index in range(19):
        boxes.extend(column(1300 - index * 50, ys=(100, 145, 190)))
    lines = [line(seq, "あ" * 12) for seq in range(20)]
    derivation = ainu.derive_page(page(), lines, boxes)
    assert not derivation.paired
    assert derivation.reason == "19 columns for 20 lines"
    assert derivation.line_box(0) is None


def test_a_title_is_not_a_page() -> None:
    """A page transcribed with a title and nothing else is left alone; there is nothing to place.

    The column count and the evidence both fit — one column, one line, and a detection a character —
    so this is the body rule on its own: `body_lines=1` accepts the same page.
    """
    derivation = ainu.derive_page(page(), [line(0, "蝦夷紀行上")], column(100))
    assert not derivation.paired and "title" in derivation.reason
    assert ainu.derive_page(page(), [line(0, "蝦夷紀行上")], column(100), body_lines=1).paired


def test_a_column_too_thin_for_its_line_is_not_paired() -> None:
    """A count match by luck is not evidence: a sliver of a column cannot stand for a long line.

    This is 蝦夷紀行 page 2, where twenty columns meet twenty transcribed lines and the rightmost
    column holds four detections for a line of twenty characters, because the leaf is torn at the
    edge. The count matches and the pairing would be written; the evidence gate refuses it.
    """
    boxes = column(1300, ys=(100, 140), w=30, h=40)
    for index in range(1, 20):
        boxes.extend(column(1300 - index * 50, ys=(100, 145, 190)))
    lines = [line(seq, "あ" * 20) for seq in range(20)]
    derivation = ainu.derive_page(page(), lines, boxes)
    assert not derivation.paired, "four detections for a line of twenty characters is not a match"
    assert "detections a character" in derivation.reason
    assert ainu.derive_page(page(), lines, boxes, min_per_character=0.0).paired, "the gate is the only objection"


def test_one_thin_column_refuses_the_page() -> None:
    """The gate is per column, not per page: the other columns do not cover for a thin one.

    Four columns of 20, 20, 20 and 2 detections against four lines of twenty characters each average
    0.78 detections a character, over the floor, while the first line's own column stands at 0.10. A
    page-wide average accepted this page; the per-column gate refuses it.
    """
    boxes = column(1300, ys=tuple(100 + 40 * step for step in range(2)))
    for index in range(1, 4):
        boxes.extend(column(1300 - index * 50, ys=tuple(100 + 40 * step for step in range(20))))
    lines = [line(seq, "あ" * 20) for seq in range(4)]
    derivation = ainu.derive_page(page(), lines, boxes)
    assert not derivation.paired, "two detections for twenty characters is not a line"
    assert "weakest column" in derivation.reason
    assert ainu.evidence_per_character(derivation, lines) > ainu.MIN_DETECTIONS_PER_CHARACTER, (
        "the page average is over the floor, which is exactly why it is not the gate"
    )
    assert derivation.evidence == [] and derivation.pairing == [], "a refused page carries no pairing"


def test_lines_are_paired_in_the_platforms_order() -> None:
    """The pairing follows `Line.seq`, not the order the table happens to hold the lines in."""
    boxes = column(100) + column(150)
    first, second = line(0, "あ" * 4), line(1, "い" * 4)
    derivation = ainu.derive_page(page(), [second, first], boxes, body_lines=2)
    assert derivation.paired
    assert [item.id for item in derivation.pairing] == [first.id, second.id]
    right = derivation.line_box(0)
    left = derivation.line_box(1)
    assert right is not None and left is not None and right.x > left.x, "the first line is the rightmost"


def test_the_evidence_is_the_detections_a_character() -> None:
    """The measure the gate reads: detections over transcribed characters, page and column alike."""
    derivation = ainu.Derivation(page_id="p", columns=ainu.columns_of(column(100)), boxes=column(100))
    assert ainu.evidence_per_character(derivation, [line(0, "あ" * 6)]) == pytest.approx(0.5)
    assert ainu.evidence_per_character(derivation, []) == 0.0
    paired = ainu.derive_page(page(), [line(0, "あ" * 2)], column(100), body_lines=1)
    assert paired.evidence == [pytest.approx(1.5)]
    assert ainu.evidence_per_column(paired) == paired.evidence


def test_a_withdrawn_box_does_not_survive_a_stricter_run(tmp_path) -> None:
    """A run that refuses a page withdraws the box an earlier, looser run wrote there.

    The dataset is one page of four lines and four columns, and the first column holds a single
    detection for a line of four characters. A run with the gate off pairs it; the run after it, with
    the gate on, must take that box away rather than leave the earlier claim standing as the import's.
    """
    from glyph_atlas import tables

    directory = tmp_path / "ainu"
    directory.mkdir()
    boxes = column(1300, ys=(100,), w=30, h=40)
    for index in range(1, 4):
        boxes.extend(column(1300 - index * 50, ys=(100, 140, 180, 220), w=30, h=40))
    lines = [line(seq, "あ" * 4) for seq in range(4)]
    tables.write(directory / "pages.parquet", [page()], Page)
    tables.write(directory / "lines.parquet", lines, Line)
    found = {page().id: boxes}

    loose = ainu.derive_dataset(directory, detections=found, min_per_character=0.0,
                                out=tmp_path / "loose.tsv")
    assert loose["paired"] == 1 and loose["boxes"] == 4 and loose["withdrawn"] == 0
    written = tables.read(directory / "lines.parquet", Line)
    assert all(ainu.derived_by_atlas(item) and item.box is not None for item in written)

    strict = ainu.derive_dataset(directory, detections=found, min_per_character=0.5,
                                 out=tmp_path / "strict.tsv")
    assert strict["paired"] == 0 and strict["withdrawn"] == 4
    after = tables.read(directory / "lines.parquet", Line)
    assert all(item.box is None for item in after), "the earlier claim is withdrawn"
    assert all(not ainu.derived_by_atlas(item) for item in after)


def test_a_persons_box_is_never_withdrawn(tmp_path) -> None:
    """A line whose box a person set is left alone, whatever the derivation decides."""
    from glyph_atlas import tables

    directory = tmp_path / "ainu"
    directory.mkdir()
    lines = [line(seq, "あ" * 4) for seq in range(4)]
    lines[0].box = Box(x=1, y=2, w=3, h=4)
    lines[0].match_method = "manual"
    lines[0].meta = {"source": "review"}
    tables.write(directory / "pages.parquet", [page()], Page)
    tables.write(directory / "lines.parquet", lines, Line)

    counts = ainu.derive_dataset(directory, detections={page().id: []}, out=tmp_path / "c.tsv")
    assert counts["withdrawn"] == 0
    after = tables.read(directory / "lines.parquet", Line)
    assert after[0].box == Box(x=1, y=2, w=3, h=4)


def test_a_persons_box_is_never_replaced_when_the_page_pairs(tmp_path) -> None:
    """A page that pairs cleanly still does not overrule a person's line box.

    Four complete columns and four lines, with the first line's box set by hand: the derivation pairs
    the page, proposes boxes for the other three lines, and leaves the first one exactly as it was —
    geometry, method and provenance.
    """
    from glyph_atlas import tables

    directory = tmp_path / "ainu"
    directory.mkdir()
    boxes = column(1300)
    for index in range(1, 4):
        boxes.extend(column(1300 - index * 50))
    lines = [line(seq, "あ" * 3) for seq in range(4)]
    lines[0].box = Box(x=1, y=2, w=3, h=4)
    lines[0].match_method = "manual"
    lines[0].meta = {"source": "review"}
    tables.write(directory / "pages.parquet", [page()], Page)
    tables.write(directory / "lines.parquet", lines, Line)

    counts = ainu.derive_dataset(directory, detections={page().id: boxes}, out=tmp_path / "c.tsv")
    assert counts["paired"] == 1
    after = tables.read(directory / "lines.parquet", Line)
    assert after[0].box == Box(x=1, y=2, w=3, h=4), "the person's box stands"
    assert after[0].match_method == "manual" and after[0].meta == {"source": "review"}
    assert all(item.box is not None and ainu.derived_by_atlas(item) for item in after[1:]), (
        "the other three lines get the proposal"
    )


def test_a_withdrawn_line_retires_its_machine_units(tmp_path) -> None:
    """A unit the aligner placed inside a withdrawn box is retired, and a reviewed one is not."""
    from glyph_atlas import tables
    from glyph_atlas.schema import ReviewState, Unit, UnitKind

    directory = tmp_path / "ainu"
    directory.mkdir()
    boxes = column(1300)
    for index in range(1, 4):
        boxes.extend(column(1300 - index * 50))
    lines = [line(seq, "あ" * 3) for seq in range(4)]
    # One line carries an import's box from the start, so the derivation never touches it.
    lines[1].box = Box(x=800, y=800, w=10, h=10)
    lines[1].match_method = "import"
    lines[1].meta = {"source": "honkoku-lines"}
    tables.write(directory / "pages.parquet", [page()], Page)
    tables.write(directory / "lines.parquet", lines, Line)
    ainu.derive_dataset(directory, detections={page().id: boxes}, out=tmp_path / "a.tsv")

    def unit(ident: str, line_id: str, review: ReviewState) -> Unit:
        return Unit(id=ident, page_id=page().id, line_id=line_id, seq=0,
                    box=Box(x=1285, y=100, w=30, h=40), text_source="あ", kind=UnitKind.CHAR,
                    method="detect-align", review=review)

    units = [
        unit(f"{lines[0].id}:f:1", lines[0].id, ReviewState.REJECTED),
        unit(f"{lines[0].id}:f:2", lines[0].id, ReviewState.MACHINE),
        unit(f"{lines[0].id}:f:3", lines[0].id, ReviewState.REVIEWED),
        unit(f"{lines[1].id}:f:1", lines[1].id, ReviewState.MACHINE),
        Unit(id=f"{lines[1].id}:m:1", page_id=page().id, line_id=lines[1].id, seq=1,
             box=Box(x=1, y=1, w=2, h=2), method="manual", review=ReviewState.MACHINE),
    ]
    tables.write(directory / "units.parquet", units, Unit)

    counts = ainu.derive_dataset(directory, detections={page().id: []}, out=tmp_path / "b.tsv")
    assert counts["withdrawn"] == 3, "the three derived boxes go; the import's stays"
    assert counts["units-retired"] == 2, "both of the first line's machine units are retired"
    after = {item.id: item for item in tables.read(directory / "units.parquet", Unit)}
    assert not after[f"{lines[0].id}:f:1"].active, "a machine unit in a withdrawn box is retired"
    assert not after[f"{lines[0].id}:f:2"].active
    assert after[f"{lines[0].id}:f:3"].active, "a reviewer's unit is never retired by a machine"
    assert after[f"{lines[1].id}:f:1"].active, "a unit on a box the import set is not retired"
    assert after[f"{lines[1].id}:m:1"].active, "a manual unit is not the pipeline's to retire"
    lines_after = {item.id: item for item in tables.read(directory / "lines.parquet", Line)}
    assert lines_after[lines[1].id].box == Box(x=800, y=800, w=10, h=10), (
        "the import's box is not withdrawn either"
    )


def test_an_empty_selection_derives_nothing(tmp_path) -> None:
    """`pages=[]` means no page, not every page: `--limit 0` must not run the dataset."""
    from glyph_atlas import tables

    directory = tmp_path / "ainu"
    directory.mkdir()
    lines = [line(seq, "あ" * 3) for seq in range(4)]
    tables.write(directory / "pages.parquet", [page()], Page)
    tables.write(directory / "lines.parquet", lines, Line)

    counts = ainu.derive_dataset(directory, detections={page().id: column(100)}, pages=[],
                                 out=tmp_path / "c.tsv")
    assert counts["pages"] == 0 and counts["boxes"] == 0
    assert all(item.box is None for item in tables.read(directory / "lines.parquet", Line)), (
        "the table is not written at all"
    )


class Counting:
    """A detector that answers one page's boxes and counts how often it was asked.

    It is only callable; giving it a `boxes` method would route it through the ONNX path, which is
    the branch this fake exists to avoid.
    """

    def __init__(self, boxes: list[Box]) -> None:
        self.found = boxes
        self.calls = 0

    def __call__(self, page: Page) -> list[Box]:
        self.calls += 1
        return list(self.found)


def test_the_cache_is_written_then_reused(tmp_path) -> None:
    """An empty cache detects and persists; a complete one answers without a detector."""
    from glyph_atlas import tables

    directory = tmp_path / "ainu"
    directory.mkdir()
    cache = tmp_path / "detections.jsonl"
    boxes = column(1300)
    for index in range(1, 4):
        boxes.extend(column(1300 - index * 50))
    lines = [line(seq, "あ" * 3) for seq in range(4)]
    tables.write(directory / "pages.parquet", [page()], Page)
    tables.write(directory / "lines.parquet", lines, Line)

    detector = Counting(boxes)  # a callable detector, so no image, cache or ONNX session is needed
    first = ainu.derive_dataset(directory, detector=detector, cache=cache, out=tmp_path / "a.tsv")
    assert detector.calls == 1 and cache.exists() and cache.stat().st_size > 0, (
        "an empty cache detects, and the result is persisted rather than left in memory"
    )
    assert first["cache-entries"] == 1 and first["cache-reused"] == 0
    assert ainu._read_cache(cache) == {page().id: boxes}

    cached = ainu.derive_dataset(directory, detector=None, cache=cache, out=tmp_path / "b.tsv")
    assert cached["cache-reused"] == 1 and cached["cache-entries"] == 1


def test_a_cache_from_other_settings_is_not_reused(tmp_path) -> None:
    """Boxes computed at another score or with another export are not attributed to this run."""
    from glyph_atlas import tables

    directory = tmp_path / "ainu"
    directory.mkdir()
    cache = tmp_path / "detections.jsonl"
    lines = [line(seq, "あ" * 3) for seq in range(4)]
    tables.write(directory / "pages.parquet", [page()], Page)
    tables.write(directory / "lines.parquet", lines, Line)
    ainu._write_cache(cache, {page().id: column(100)}, [page().id],
                      settings=ainu.detector_settings(ainu.DEFAULT_ONNX, score=0.02))
    assert ainu._read_cache(cache, settings=ainu.detector_settings(
        ainu.DEFAULT_ONNX, score=0.02)) == {page().id: column(100)}
    assert ainu._read_cache(cache, settings=ainu.detector_settings(
        ainu.DEFAULT_ONNX, score=0.05)) == {}, "a different operating point is different boxes"


def test_the_census_cache_keeps_pages_it_did_not_compute(tmp_path) -> None:
    """A subset run adds to a cache rather than replacing it."""
    cache = tmp_path / "detections.jsonl"
    settings = ainu.detector_settings(ainu.DEFAULT_ONNX, score=0.02)
    ainu._write_cache(cache, {"hk:a:0": column(100)}, settings=settings)
    ainu.append_cache(cache, "hk:b:0", column(150))
    ainu._write_cache(cache, {"hk:b:0": column(150)}, settings=settings)
    again = ainu._read_cache(cache, settings=settings)
    assert set(again) == {"hk:b:0"}, "the whole-file write is what drops a duplicate append"
    ainu._write_cache(cache, {**again, "hk:a:0": column(100)}, settings=settings)
    assert set(ainu._read_cache(cache, settings=settings)) == {"hk:a:0", "hk:b:0"}, (
        "a subset run's whole-file write keeps the pages it did not compute"
    )


def test_columns_tsv_holds_every_field_the_report_quotes(tmp_path) -> None:
    """The measurement is a file a report can read back, with one row per page."""
    boxes = column(100) + column(150)
    lines = [line(0, "あ" * 3), line(1, "い" * 3)]
    derivation = ainu.derive_page(page(), lines, boxes, body_lines=2)
    row = ainu.page_row(derivation, page(), lines)
    path = tmp_path / "columns.tsv"
    assert ainu.write_columns(path, [row]) == 1
    with path.open(encoding="utf-8", newline="") as handle:
        read = list(csv.DictReader(handle, delimiter="\t"))
    assert list(read[0]) == list(ainu.COLUMNS_FIELDS)
    assert read[0]["page_id"] == "hk:d:0" and read[0]["columns"] == "2" and read[0]["lines"] == "2"
    assert read[0]["paired"] == "1"


class EditsDuringDetection:
    """A detector that, while it runs, does what a reviewer would do to one line."""

    def __init__(self, boxes: list[Box], directory, edit) -> None:
        self.found = boxes
        self.directory = directory
        self.edit = edit
        self.calls = 0

    def __call__(self, page: Page) -> list[Box]:
        self.calls += 1
        if self.calls == 1:
            self.edit(self.directory)
        return list(self.found)


def four_columns(page_id: str = "hk:d:0") -> list[Box]:
    """Four complete columns, which is what a page of four lines needs to pair."""
    boxes = column(1300)
    for index in range(1, 4):
        boxes.extend(column(1300 - index * 50))
    return boxes


def test_an_edit_during_detection_is_not_overwritten(tmp_path) -> None:
    """A reviewer who sets a box while the detector runs keeps it, and keeps their note.

    `derive_dataset` reads the lines, runs the detector for minutes and only then writes. The write
    re-reads the table under the lock, so it sees the edit — the old version saw it and applied the
    stale proposal anyway, replacing the box, the method and the whole `meta` dict.
    """
    from glyph_atlas import tables

    directory = tmp_path / "ainu"
    directory.mkdir()
    lines = [line(seq, "あ" * 3) for seq in range(4)]
    tables.write(directory / "pages.parquet", [page()], Page)
    tables.write(directory / "lines.parquet", lines, Line)

    def edit(path):
        saved = tables.read(path / "lines.parquet", Line)
        saved[1].box = Box(x=1, y=2, w=3, h=4)
        saved[1].match_method = "manual"
        saved[1].meta = {"source": "review", "note": "saved during detection"}
        tables.write(path / "lines.parquet", saved, Line)

    detector = EditsDuringDetection(four_columns(), directory, edit)
    counts = ainu.derive_dataset(directory, detector=detector, out=tmp_path / "c.tsv")
    assert counts["paired"] == 1
    assert counts["stale"] == 1, "the edited line's proposal is dropped rather than applied"

    after = {item.id: item for item in tables.read(directory / "lines.parquet", Line)}
    edited = after[lines[1].id]
    assert edited.box == Box(x=1, y=2, w=3, h=4), "the reviewer's box stands"
    assert edited.match_method == "manual"
    assert edited.meta == {"source": "review", "note": "saved during detection"}, (
        "the note survives: only the derivation's own meta key is merged"
    )
    assert all(ainu.derived_by_atlas(after[lines[index].id]) for index in (0, 2, 3)), (
        "the other three lines get the proposal"
    )


def test_a_note_added_during_detection_survives_the_proposal(tmp_path) -> None:
    """A line whose box the derivation may set keeps any other meta key added while it ran."""
    from glyph_atlas import tables

    directory = tmp_path / "ainu"
    directory.mkdir()
    lines = [line(seq, "あ" * 3) for seq in range(4)]
    tables.write(directory / "pages.parquet", [page()], Page)
    tables.write(directory / "lines.parquet", lines, Line)

    def edit(path):
        saved = tables.read(path / "lines.parquet", Line)
        saved[2].meta = {**(saved[2].meta or {}), "note": "left during detection"}
        tables.write(path / "lines.parquet", saved, Line)

    detector = EditsDuringDetection(four_columns(), directory, edit)
    counts = ainu.derive_dataset(directory, detector=detector, out=tmp_path / "c.tsv")
    assert counts["boxes"] == 4 and counts["stale"] == 0
    after = {item.id: item for item in tables.read(directory / "lines.parquet", Line)}
    assert after[lines[2].id].meta.get("note") == "left during detection"
    assert ainu.derived_by_atlas(after[lines[2].id]), "the proposal is still applied"


def test_an_untagged_imported_box_is_never_replaced(tmp_path) -> None:
    """An import's box carries no method tag and no provenance, and is still not a proposal's to take."""
    from glyph_atlas import tables

    directory = tmp_path / "ainu"
    directory.mkdir()
    lines = [line(seq, "あ" * 3) for seq in range(4)]
    lines[0].box = Box(x=7, y=8, w=9, h=10)  # no match_method, no meta: what an import writes
    tables.write(directory / "pages.parquet", [page()], Page)
    tables.write(directory / "lines.parquet", lines, Line)

    counts = ainu.derive_dataset(directory, detections={page().id: four_columns()},
                                 out=tmp_path / "c.tsv")
    assert counts["paired"] == 1
    after = {item.id: item for item in tables.read(directory / "lines.parquet", Line)}
    assert after[lines[0].id].box == Box(x=7, y=8, w=9, h=10), "the import's box stands"
    assert not ainu.derived_by_atlas(after[lines[0].id])
    assert all(after[lines[index].id].box is not None for index in (1, 2, 3))


def test_a_gloss_column_beside_the_lines_is_left_unread() -> None:
    """A thin column between two lines, such as a gloss beside a word, is skipped, not paired.

    Five lines of six characters stand 60 px apart, and a column of two small marks sits between the
    second and the third. Pairing only equal counts refused this page; the pairing reads past it.
    """
    boxes: list[Box] = []
    for index in range(5):
        boxes.extend(column(1300 - index * 60, ys=tuple(100 + 40 * step for step in range(6))))
    boxes.extend(column(1300 - 90, ys=(100, 140), w=10, h=20))
    lines = [line(seq, "あ" * 6) for seq in range(5)]
    derivation = ainu.derive_page(page(), lines, boxes)
    assert derivation.paired and derivation.reason == "paired, 1 columns unread"
    assert [len(span) for span in derivation.spans] == [1, 1, 1, 1, 1]
    third = derivation.line_box(2)
    assert third is not None and third.x < 1300 - 90 - 15, "the third line is the column past the gloss"


def test_a_line_split_in_two_columns_takes_both() -> None:
    """A line whose ink splits into two neighbouring columns is given both, and one box over them."""
    boxes: list[Box] = []
    for index in range(4):
        boxes.extend(column(1300 - index * 100, ys=tuple(100 + 40 * step for step in range(8))))
    boxes.extend(column(1300 - 4 * 100, ys=tuple(100 + 40 * step for step in range(4))))
    boxes.extend(column(1300 - 4 * 100 - 45, ys=tuple(260 + 40 * step for step in range(4))))
    lines = [line(seq, "あ" * 8) for seq in range(5)]
    derivation = ainu.derive_page(page(), lines, boxes)
    assert derivation.paired and [len(span) for span in derivation.spans] == [1, 1, 1, 1, 2]
    last = derivation.line_box(4)
    assert last is not None and last.w > 60, "the box holds both halves"


def test_a_column_holding_far_more_than_its_line_is_not_paired() -> None:
    """Twice the ink a line names is the ink of more than that line; the page is refused."""
    boxes: list[Box] = []
    for index in range(4):
        boxes.extend(column(1300 - index * 50, ys=tuple(100 + 40 * step for step in range(12))))
    lines = [line(seq, "あ" * 4) for seq in range(4)]
    derivation = ainu.derive_page(page(), lines, boxes)
    assert not derivation.paired and "fullest column" in derivation.reason
    assert ainu.derive_page(page(), lines, boxes, max_per_character=5.0).paired


def test_a_transcription_short_of_a_line_is_not_paired_one_column_off() -> None:
    """Six full columns and five transcribed lines: one line of ink is not in the transcription.

    Joining two side-by-side columns into one line, or leaving a full column unread, would pair every
    line after it one column off, so the page is refused.
    """
    boxes: list[Box] = []
    for index in range(6):
        boxes.extend(column(1300 - index * 60, ys=tuple(100 + 40 * step for step in range(6))))
    lines = [line(seq, "あ" * 6) for seq in range(5)]
    derivation = ainu.derive_page(page(), lines, boxes)
    assert not derivation.paired and derivation.line_box(0) is None
