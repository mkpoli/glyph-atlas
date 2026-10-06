"""Tests of the box relabel: a line aligned in the old order gets each box's character back."""

from __future__ import annotations

import json

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


def repair(units, verdicts=None, forms=None):
    detections = {"hl:item:0": BOXES}
    return box_relabel.repair(units, [line()], detections, run=run(), classifier=None, crop_of=None,
                              verdicts=verdicts, forms=forms)


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


def test_a_unit_a_person_confirmed_or_corrected_keeps_its_label():
    old = stale_units()
    old[0] = old[0].model_copy(update={"review": ReviewState.REVIEWED})
    repaired, records = repair(old, verdicts={old[1].id: box_relabel.MATCH, old[2].id: box_relabel.CORRECTION})
    by_id = {unit.id: unit for unit in repaired}
    assert [by_id[unit.id].text_source for unit in old[:3]] == ["高", "八", "百"]
    assert [record["status"] for record in records].count("protected") == 3


def test_a_unit_a_review_only_called_wrong_takes_the_realigned_label_and_keeps_its_history():
    old = stale_units()
    old[0] = old[0].model_copy(update={"review": ReviewState.DISPUTED})
    old[1] = old[1].model_copy(update={"review": ReviewState.REVIEWED})
    repaired, records = repair(old, verdicts={old[1].id: box_relabel.WRONG})
    by_id = {unit.id: unit for unit in repaired}
    assert by_id[old[0].id].text_source == TEXT[BOXES.index(old[0].box)]
    assert by_id[old[1].id].text_source == TEXT[BOXES.index(old[1].box)]
    assert box_relabel.counts(records) == {"relabelled": 4}
    assert by_id[old[1].id].meta["box_relabel"]["review"] == {"state": "reviewed", "verdict": "wrong"}
    assert "review" not in by_id[old[0].id].meta["box_relabel"]


def test_a_unit_of_a_decided_form_takes_a_realigned_label_of_its_family_only():
    old = stale_units()  # 高 on the box of 百, 八 on the box of 高, 百 on the box of 四
    forms = {old[0].id: "百", old[1].id: "U+56DB", old[2].id: "U+56DB"}
    repaired, records = repair(old, forms=forms)
    by_id = {unit.id: unit for unit in repaired}
    statuses = {record["unit_id"]: record["status"] for record in records}
    assert statuses[old[0].id] == "relabelled" and by_id[old[0].id].text_source == "百"
    assert statuses[old[2].id] == "relabelled" and by_id[old[2].id].text_source == "四"
    assert statuses[old[1].id] == "review" and by_id[old[1].id].text_source == "八"
    assert by_id[old[1].id].meta["box_relabel"] == {"method": box_relabel.METHOD, "status": "review",
                                                    "before": "八", "after": "高", "form": "U+56DB"}


def test_the_latest_review_of_a_unit_decides_its_verdict():
    def site(target, verdict, **extra):
        evidence = json.dumps({"kind": "character-review", "verdict": verdict, **extra})
        return {"target": target, "event": json.dumps({"target_type": "unit", "target_id": target, "field": "review",
                                                       "new": "disputed", "evidence": evidence})}
    events = [site("a", "wrong", issue="reading"), site("a", "wrong", issue="character", suggested_character="U+9650"),
              site("b", "match"), site("b", "wrong", issue="crop"),
              site("c", "match", kind="visual-quiz"),
              {"target_type": "unit", "target_id": "d", "field": "text_source", "new": "高", "evidence": None},
              {"target_type": "unit", "target_id": "e", "field": "seen", "new": True, "evidence": None},
              {"target_type": "unit", "target_id": "f", "field": "review", "new": "reviewed", "evidence": None}]
    assert box_relabel.verdicts_of(events) == {"a": "correction", "b": "wrong", "d": "correction", "f": "match"}


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
    repaired, _ = repair(old, verdicts={old[0].id: box_relabel.MATCH})
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


def test_review_events_are_read_from_json_lines_a_list_or_a_d1_query(tmp_path):
    event = {"target_type": "unit", "target_id": "a", "field": "review", "new": "reviewed", "evidence": None}
    (tmp_path / "lines.jsonl").write_text(json.dumps(event) + "\n")
    (tmp_path / "list.json").write_text(json.dumps([event]))
    (tmp_path / "d1.json").write_text(json.dumps([{"results": [event], "success": True}]))
    for name in ("lines.jsonl", "list.json", "d1.json"):
        assert box_relabel.read_reviews(tmp_path / name) == [event]


def test_a_pipeline_edit_an_unsure_answer_or_an_undone_review_decides_nothing():
    def event(target, field, new, evidence=None, role="reviewer", **extra):
        return {"id": f"{target}:{field}:{new}", "target_type": "unit", "target_id": target, "field": field,
                "new": new, "role": role, "evidence": json.dumps(evidence) if evidence else None, **extra}
    events = [event("a", "review", "disputed", {"verdict": "wrong", "issue": "crop"}, at="2026-09-28T10:00:00Z"),
              event("a", "meta", "{}", role="model", at="2026-09-28T11:00:00+00:00"),
              event("b", "review", "disputed", {"verdict": "unsure"}),
              {"target": "c", "undone": 1, "event": json.dumps(event("c", "review", "reviewed", {"verdict": "match"}))},
              event("d", "review", '"reviewed"'),
              event("e", "note", "a note")]
    assert box_relabel.verdicts_of(events) == {"a": "wrong", "d": "match"}


def test_review_events_count_once_in_the_order_they_were_made():
    later = {"id": "cf:1", "target_type": "unit", "target_id": "a", "field": "review", "new": "reviewed",
             "role": "reviewer", "at": "2026-09-29T00:00:00Z",
             "evidence": json.dumps({"verdict": "wrong", "suggested_character": "U+9AD8"})}
    earlier = {"id": "cf:0", "target_type": "unit", "target_id": "a", "field": "review", "new": "disputed",
               "role": "reviewer", "at": "2026-09-28T00:00:00+00:00", "evidence": json.dumps({"verdict": "wrong"})}
    assert box_relabel.verdicts_of([later, earlier, {"target": "a", "event": json.dumps(later)}]) == {"a": "correction"}


def test_a_unit_sent_to_review_takes_its_place_in_the_line():
    old = stale_units()
    repaired, _ = repair(old, forms={old[1].id: "U+56DB"})
    assert sorted(unit.seq for unit in repaired) == [1, 2, 3, 4]



def test_a_unit_of_a_decided_form_whose_box_left_its_line_is_unplaced_not_sent_to_review(monkeypatch):
    old = stale_units()
    new = [unit(index + 1, char, BOXES[index]).model_copy(update={"id": f"n{index}"})
           for index, char in enumerate(TEXT[:3])]
    forms = {unit.id: "U+5343" for unit in old}
    kept = {record["status"] for record in box_relabel.relabel(old, new, forms=forms) if record["after"] is None}
    assert kept == {"review"}
    monkeypatch.setattr(box_relabel, "unplaced_reason", lambda *args, **kwargs: "other-line")
    left = {record["status"] for record in box_relabel.relabel(old, new, forms=forms) if record["after"] is None}
    assert left == {"unplaced"}


def test_two_lines_holding_one_box_are_stale_and_the_repair_leaves_the_box_to_one():
    right = Line(id="hl:item_0_000", page_id="hl:item:0", seq=0, box=Box(x=150, y=0, w=110, h=200), vertical=True,
                 text_raw="一二三四", text="一二三四")
    # The left line's box reaches past the centre of the right column, at 165.
    left = line().model_copy(update={"box": Box(x=90, y=0, w=90, h=200)})
    right_boxes = [Box(x=150, y=y, w=30, h=28) for y in (10, 50, 90, 130)]
    old_right = [Unit(id=f"hl:item_0_000:old:{index + 1}", page_id="hl:item:0", line_id=right.id, seq=index + 1,
                      box=box, kind=UnitKind.CHAR, text_source=char, method="detect-align")
                 for index, (char, box) in enumerate(zip("一二三四", right_boxes, strict=True))]
    # The left line was aligned over its own column and the right column's last box.
    old_left = [unit(index + 1, char, box) for index, (char, box) in enumerate(zip(TEXT, [*BOXES[:3], right_boxes[3]],
                                                                                    strict=True))]
    old = old_right + old_left
    assert box_relabel.stale_lines(old) == {right.id, left.id}
    detections = {"hl:item:0": [*BOXES, *right_boxes]}
    repaired, records = box_relabel.repair(old, [right, left], detections, run=run(), classifier=None, crop_of=None)
    lost = next(record for record in records if record["unit_id"] == old_left[3].id)
    assert lost["status"] == "unplaced" and lost["reason"] == "other-line"
    placed = [(unit.page_id, unit.line_id, unit.box) for unit in repaired if unit.box is not None and unit.seq is not None]
    assert box_relabel.shared_lines(placed) == set()
    assert not box_relabel.stale_lines(repaired)


def test_a_horizontal_line_gives_up_a_box_the_page_gave_a_vertical_line():
    across = Line(id="hl:item_0_009", page_id="hl:item:0", seq=9, box=Box(x=0, y=0, w=300, h=60), vertical=False,
                  text_raw="甲乙", text="甲乙")
    old = [unit(index + 1, char, BOXES[index]) for index, char in enumerate(TEXT)]
    old.append(Unit(id="hl:item_0_009:old:1", page_id="hl:item:0", line_id=across.id, seq=1, box=BOXES[0],
                    kind=UnitKind.CHAR, text_source="甲", method="detect-align"))
    orientation = {line().id: True, across.id: False}
    assert box_relabel.stale_lines(old, vertical=orientation) == {line().id, across.id}
    repaired, records = box_relabel.repair(old, [line(), across], {"hl:item:0": BOXES}, run=run(), classifier=None,
                                           crop_of=None)
    assert box_relabel.stale_lines(repaired, vertical=orientation) == set()
    lost = next(record for record in records if record["unit_id"] == "hl:item_0_009:old:1")
    assert (lost["status"], lost["reason"]) == ("unplaced", "other-line")


def test_a_gap_fill_never_takes_a_box_another_line_holds():
    right = Line(id="hl:item_0_000", page_id="hl:item:0", seq=0, box=Box(x=150, y=0, w=110, h=200), vertical=True,
                 text_raw="一二三四", text="一二三四")
    left = line().model_copy(update={"box": Box(x=90, y=0, w=90, h=200)})
    right_boxes = [Box(x=150, y=y, w=30, h=28) for y in (10, 50, 90, 130)]
    old_right = [Unit(id=f"hl:item_0_000:old:{index + 1}", page_id="hl:item:0", line_id=right.id, seq=index + 1,
                      box=box, kind=UnitKind.CHAR, text_source=char, method="detect-align")
                 for index, (char, box) in enumerate(zip("一二三四", right_boxes, strict=True))]
    # The left column has ink for three of its four characters; its old fourth unit sat on the right column.
    old_left = [unit(index + 1, char, box) for index, (char, box) in enumerate(zip(TEXT, [*BOXES[:3], right_boxes[3]],
                                                                                    strict=True))]
    detections = {"hl:item:0": [*BOXES[:3], *right_boxes]}
    repaired, records = box_relabel.repair(old_right + old_left, [right, left], detections, run=run(), classifier=None,
                                           crop_of=None, every=True)
    lost = next(record for record in records if record["unit_id"] == old_left[3].id)
    assert (lost["status"], lost["reason"]) == ("unplaced", "other-line")
    labelled = [(unit.page_id, unit.line_id, unit.box) for unit in repaired if unit.box is not None and unit.text_source]
    assert box_relabel.shared_lines(labelled) == set()


def test_a_reviewed_unit_keeps_its_box_and_the_line_the_page_gave_it_to_yields():
    right = Line(id="hl:item_0_000", page_id="hl:item:0", seq=0, box=Box(x=150, y=0, w=110, h=200), vertical=True,
                 text_raw="一二三四", text="一二三四")
    left = line().model_copy(update={"box": Box(x=90, y=0, w=90, h=200)})
    right_boxes = [Box(x=150, y=y, w=30, h=28) for y in (10, 50, 90, 130)]
    old_right = [Unit(id=f"hl:item_0_000:old:{index + 1}", page_id="hl:item:0", line_id=right.id, seq=index + 1,
                      box=box, kind=UnitKind.CHAR, text_source=char, method="detect-align")
                 for index, (char, box) in enumerate(zip("一二三四", right_boxes, strict=True))]
    old_left = [unit(index + 1, char, box) for index, (char, box) in enumerate(zip(TEXT, [*BOXES[:3], right_boxes[3]],
                                                                                    strict=True))]
    old_left[3] = old_left[3].model_copy(update={"review": ReviewState.REVIEWED})
    repaired, records = box_relabel.repair(old_right + old_left, [right, left], {"hl:item:0": [*BOXES, *right_boxes]},
                                           run=run(), classifier=None, crop_of=None)
    by_id = {record["unit_id"]: record for record in records}
    assert by_id[old_left[3].id]["status"] == "protected"
    assert (by_id[old_right[3].id]["status"], by_id[old_right[3].id]["reason"]) == ("unplaced", "held")
    labelled = [(unit.page_id, unit.line_id, unit.box) for unit in repaired if unit.box is not None and unit.text_source]
    assert box_relabel.shared_lines(labelled) == set()


def test_a_unit_sent_to_review_yields_the_box_a_reviewed_unit_of_another_line_holds():
    right = Line(id="hl:item_0_000", page_id="hl:item:0", seq=0, box=Box(x=150, y=0, w=110, h=200), vertical=True,
                 text_raw="一二三四", text="一二三四")
    left = line().model_copy(update={"box": Box(x=90, y=0, w=90, h=200)})
    right_boxes = [Box(x=150, y=y, w=30, h=28) for y in (10, 50, 90, 130)]
    # The right line's fourth unit is labelled 三 where 四 is written, and a person decided another
    # form for its cluster, so it would be sent to review on the box the left line's reviewed unit holds.
    old_right = [Unit(id=f"hl:item_0_000:old:{index + 1}", page_id="hl:item:0", line_id=right.id, seq=index + 1,
                      box=box, kind=UnitKind.CHAR, text_source=char, method="detect-align")
                 for index, (char, box) in enumerate(zip("一二三三", right_boxes, strict=True))]
    old_left = [unit(index + 1, char, box) for index, (char, box) in enumerate(zip(TEXT, [*BOXES[:3], right_boxes[3]],
                                                                                    strict=True))]
    old_left[3] = old_left[3].model_copy(update={"review": ReviewState.REVIEWED})
    repaired, records = box_relabel.repair(old_right + old_left, [right, left], {"hl:item:0": [*BOXES, *right_boxes]},
                                           run=run(), classifier=None, crop_of=None,
                                           forms={old_right[3].id: "U+5343"})
    by_id = {record["unit_id"]: record for record in records}
    assert by_id[old_left[3].id]["status"] == "protected"
    assert (by_id[old_right[3].id]["status"], by_id[old_right[3].id]["reason"]) == ("unplaced", "held")
    labelled = [(unit.page_id, unit.line_id, unit.box) for unit in repaired if unit.box is not None and unit.text_source]
    assert box_relabel.shared_lines(labelled) == set()
