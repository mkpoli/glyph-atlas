"""Tests of the box relabel: a line aligned in the old order gets each box's character back."""

from __future__ import annotations

import pytest

from glyph_atlas import align, box_relabel, koji
from glyph_atlas.schema import Box, Line, ReviewState, Unit, UnitKind

TEXT = "高八百四"
#: Four boxes down one column, top to bottom.
BOXES = [Box(x=100, y=y, w=30, h=28) for y in (10, 50, 90, 130)]


def line() -> Line:
    return Line(id="hl:item_0_001", page_id="hl:item:0", seq=0, box=Box(x=90, y=0, w=60, h=200),
                vertical=True, text_raw=TEXT, text=koji.plain(TEXT))


def unit(seq: int, text: str, box: Box | None, review: ReviewState = ReviewState.MACHINE) -> Unit:
    return Unit(id=f"hl:item_0_001:old:{seq}", page_id="hl:item:0", line_id="hl:item_0_001", seq=seq, box=box,
                kind=UnitKind.CHAR, text_source=text, method="detect-align", review=review)


def stale_units(order=(2, 0, 3, 1)) -> list[Unit]:
    """The line as the old order aligned it: character i of the text on box `order[i]`."""
    return [unit(index + 1, char, BOXES[order[index]]) for index, char in enumerate(TEXT)]


def run() -> align.Run:
    return align.Run(name="test", accept=0.5, margin=0.0)


def repair(units, protected=()):
    detections = {"hl:item:0": BOXES}
    return box_relabel.repair(units, [line()], detections, run=run(), classifier=None, crop_of=None,
                              protected=protected)


def test_a_line_whose_boxes_step_back_up_the_column_is_stale():
    assert box_relabel.descents(box_relabel.placed_of(stale_units())) == (2, 3)
    assert box_relabel.stale(box_relabel.placed_of(stale_units()))
    in_order = stale_units(order=(0, 1, 2, 3))
    assert box_relabel.descents(box_relabel.placed_of(in_order)) == (0, 3)
    assert not box_relabel.stale(box_relabel.placed_of(in_order))


def test_a_horizontal_line_read_left_to_right_is_not_stale():
    placed = [(index, Box(x=x, y=10, w=30, h=28)) for index, x in enumerate((10, 50, 90, 130))]
    assert box_relabel.descents(placed) == (0, 3)
    backwards = list(enumerate(box for _, box in reversed(placed)))
    assert box_relabel.stale(backwards)


def test_a_line_too_short_to_tell_is_not_stale():
    assert not box_relabel.stale(box_relabel.placed_of(stale_units()[:2]))


def test_each_id_keeps_its_box_and_takes_the_character_written_there():
    old = stale_units()
    repaired, records = repair(old)
    by_id = {unit.id: unit for unit in repaired}
    for before in old:
        after = by_id[before.id]
        assert after.box == before.box
        assert after.text_source == TEXT[BOXES.index(before.box)]
    assert box_relabel.counts(records) == {"relabelled": 4}
    assert by_id[old[0].id].meta["box_relabel"] == {"method": box_relabel.METHOD, "status": "relabelled",
                                                    "before": "高", "after": "百"}


def test_the_repaired_line_reads_in_order():
    repaired, _ = repair(stale_units())
    assert not box_relabel.stale(box_relabel.placed_of(repaired))


def test_a_unit_a_person_reviewed_keeps_its_label():
    old = stale_units()
    old[0] = old[0].model_copy(update={"review": ReviewState.REVIEWED})
    repaired, records = repair(old, protected={old[1].id})
    by_id = {unit.id: unit for unit in repaired}
    assert by_id[old[0].id].text_source == "高"
    assert by_id[old[1].id].text_source == "八"
    assert [record["status"] for record in records].count("protected") == 2


def test_a_box_the_new_alignment_leaves_empty_loses_its_label():
    old = stale_units() + [unit(5, "四", Box(x=100, y=170, w=30, h=28))]
    detections = {"hl:item:0": [*BOXES, Box(x=100, y=170, w=30, h=28)]}
    repaired, records = box_relabel.repair(old, [line()], detections, run=run(), classifier=None, crop_of=None)
    statuses = {record["unit_id"]: record["status"] for record in records}
    assert list(statuses.values()).count("unplaced") == 1
    empty = next(unit for unit in repaired if statuses[unit.id] == "unplaced")
    assert empty.text_source is None and empty.unicode is None and empty.seq is None
    assert next(record for record in records if record["status"] == "unplaced")["reason"] == "no-unit"
    assert empty.meta["box_relabel"]["before"] in TEXT


def test_a_line_in_order_is_left_alone_unless_every_line_is_asked_for():
    old = stale_units(order=(0, 1, 2, 3))
    repaired, records = repair(old)
    assert repaired == old
    assert records == []
    swapped = [unit(1, "八", BOXES[0]), unit(2, "高", BOXES[1]), unit(3, "百", BOXES[2]), unit(4, "四", BOXES[3])]
    assert not box_relabel.stale(box_relabel.placed_of(swapped))
    repaired, records = box_relabel.repair(swapped, [line()], {"hl:item:0": BOXES}, run=run(), classifier=None,
                                           crop_of=None, every=True)
    assert [unit.text_source for unit in repaired] == list(TEXT)
    assert box_relabel.counts(records) == {"relabelled": 2, "unchanged": 2}


def test_a_box_holding_part_of_a_split_character_is_unplaced():
    old = stale_units()
    split = [unit(1, "高", BOXES[0]).model_copy(update={"id": "new:1", "granularity": "sequence", "group_id": "g0"}),
             unit(2, "高", BOXES[1]).model_copy(update={"id": "new:2", "granularity": "sequence", "group_id": "g0"}),
             unit(3, "八", BOXES[2]).model_copy(update={"id": "new:3"})]
    statuses = {tuple(record["box"]): record["status"] for record in box_relabel.relabel(old, split)}
    assert statuses[box_relabel.box_key(BOXES[0])] == "unplaced"
    assert statuses[box_relabel.box_key(BOXES[1])] == "unplaced"
    assert statuses[box_relabel.box_key(BOXES[2])] == "relabelled"


def test_a_page_whose_image_is_not_cached_is_left_as_it_is(tmp_path, monkeypatch):
    from glyph_atlas import tables
    from glyph_atlas.schema import Page

    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "cache"))
    source = tmp_path / "source"
    tables.write(source / "pages.parquet", [Page(id="hl:item:0", document_id="hl:item", seq=0,
                                                  image="https://example.org/missing.jpg", width=200, height=200)], Page)
    tables.write(source / "lines.parquet", [line()], Line)
    tables.write(source / "units.parquet", stale_units(), Unit)
    (tmp_path / "detections.jsonl").write_text(
        '{"kind": "glyph-atlas-ainu-detections", "version": 1, "settings": null}\n'
        '{"page_id": "hl:item:0", "boxes": [' + ", ".join(box.model_dump_json() for box in BOXES) + "]}\n")
    result = box_relabel.relabel_directory(source, tmp_path / "out", run=run(), classifier=None,
                                           detections=tmp_path / "detections.jsonl")
    assert result["pages_without_image"] == 1 and result["lines"] == 0
    assert tables.read(tmp_path / "out" / "units.parquet", Unit) == stale_units()
    with pytest.raises(ValueError, match="derived"):
        box_relabel.relabel_directory(source, source, run=run(), classifier=None, detections=tmp_path / "detections.jsonl")


def test_boxes_left_between_two_placed_characters_take_the_characters_skipped_there():
    old = stale_units()
    new = [unit(1, "高", BOXES[0]).model_copy(update={"id": "n1"}),
           unit(2, "八", None).model_copy(update={"id": "n2"}),
           unit(3, "百", None).model_copy(update={"id": "n3"}),
           unit(4, "四", BOXES[3]).model_copy(update={"id": "n4"})]
    records = {tuple(record["box"]): record for record in box_relabel.relabel(old, new)}
    assert [records[box_relabel.box_key(box)]["after"] for box in BOXES] == list(TEXT)
    assert records[box_relabel.box_key(BOXES[1])]["fill"] == "gap"
    assert "fill" not in records[box_relabel.box_key(BOXES[0])]


def test_a_gap_whose_boxes_and_characters_do_not_match_stays_unplaced():
    old = stale_units()
    new = [unit(1, "高", BOXES[0]).model_copy(update={"id": "n1"}),
           unit(2, "八", None).model_copy(update={"id": "n2"}),
           unit(4, "四", BOXES[3]).model_copy(update={"id": "n4"})]
    statuses = [record["status"] for record in box_relabel.relabel(old, new)]
    assert statuses.count("unplaced") == 2


def test_a_protected_unit_takes_its_place_so_its_line_is_not_stale_after_the_repair():
    old = stale_units()
    repaired, _ = repair(old, protected={old[0].id})
    assert repaired[0].text_source == "高"
    assert not box_relabel.stale(box_relabel.placed_of(repaired))


def test_a_repeat_mark_placed_on_its_own_box_is_a_character_of_the_line():
    old = stale_units()
    new = [unit(index + 1, char, BOXES[index]).model_copy(update={"id": f"n{index}"})
           for index, char in enumerate("高々百四")]
    new[1] = new[1].model_copy(update={"kind": UnitKind.ITERATION_MARK})
    records = {tuple(record["box"]): record for record in box_relabel.relabel(old, new)}
    assert records[box_relabel.box_key(BOXES[1])]["after"] == "々"


def test_a_relabelled_split_member_leaves_its_group():
    old = stale_units()
    old[0] = old[0].model_copy(update={"granularity": "sequence", "group_id": "old:g0"})
    repaired, _ = repair(old)
    assert repaired[0].group_id is None and repaired[0].granularity == "char"


def test_a_short_vertical_line_over_two_columns_is_read_down_each_column():
    placed = [(1, Box(x=140, y=10, w=30, h=28)), (2, Box(x=140, y=45, w=30, h=28)),
              (3, Box(x=100, y=10, w=30, h=28)), (4, Box(x=100, y=45, w=30, h=28))]
    assert box_relabel.descents(placed, vertical=True) == (0, 3)
    assert box_relabel.descents(placed) == (0, 3)


def test_a_gap_holding_a_token_of_several_characters_is_not_filled():
    old = stale_units()
    new = [unit(1, "高", BOXES[0]).model_copy(update={"id": "n1"}),
           unit(2, "八", None).model_copy(update={"id": "n2"}),
           unit(3, "百四", None).model_copy(update={"id": "n3"}),
           unit(4, "百", None).model_copy(update={"id": "n4"}),
           unit(5, "四", BOXES[3]).model_copy(update={"id": "n5"})]
    statuses = [record["status"] for record in box_relabel.relabel(old, new)]
    assert statuses.count("unplaced") == 2


def test_a_line_the_alignment_placed_nothing_on_gets_no_gap_fill():
    old = stale_units()
    new = [unit(index + 1, char, None).model_copy(update={"id": f"n{index}"}) for index, char in enumerate(TEXT)]
    assert {record["status"] for record in box_relabel.relabel(old, new)} == {"unplaced"}


def test_a_page_the_detections_do_not_cover_is_left_as_it_is(tmp_path, monkeypatch):
    from glyph_atlas import images, tables
    from glyph_atlas.schema import Page

    monkeypatch.setattr(images, "path_for", lambda url, **_: tmp_path / "page.png")
    source = tmp_path / "source"
    tables.write(source / "pages.parquet", [Page(id="hl:item:0", document_id="hl:item", seq=0,
                                                  image="https://example.org/page.jpg", width=200, height=200)], Page)
    tables.write(source / "lines.parquet", [line()], Line)
    tables.write(source / "units.parquet", stale_units(), Unit)
    (tmp_path / "detections.jsonl").write_text('{"kind": "glyph-atlas-ainu-detections", "version": 1, "settings": null}\n')
    result = box_relabel.relabel_directory(source, tmp_path / "out", run=run(), classifier=None,
                                           detections=tmp_path / "detections.jsonl")
    assert result["pages_without_detections"] == 1 and result["lines"] == 0


def test_a_unit_another_method_placed_on_the_line_is_not_relabelled():
    imported = unit(9, "錫", Box(x=100, y=170, w=30, h=28)).model_copy(update={"id": "ar:record:0", "method": "import"})
    repaired, records = repair([*stale_units(), imported])
    assert repaired[-1] == imported
    assert "ar:record:0" not in {record["unit_id"] for record in records}
