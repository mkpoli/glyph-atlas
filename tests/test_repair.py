"""Tests of the alignment-repair pass: what it is allowed to move, and what it must never break.

The defect these cover is measured in `docs/reports/alignment-shift-repair.md`: the aligner matched a
line's characters to its detections in the order the detector returned them, which on the Ainu
manuscripts is not the order the line is read, so a unit labelled 手 held the crop of を. The pass has
two layers and they are tested apart: the deterministic one is the reading order and the rule that a
space is not ink, and the proposal one is the rigid phase a model decides, which is only proposed when
it wins in three currencies at once.

The invariant that gets its own tests is ownership. Deciding each character on its own evidence is not
enough to keep a line a sequence — a moved character can land on the box a withheld one keeps, and one
rectangle under two labels is worse than either decision — so the final rows are checked for two
owners of one box and every move that would duplicate one is put back.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from glyph_atlas import align, repair, tables
from glyph_atlas.align import Detection, Run, Token
from glyph_atlas.align import Detection as Det
from glyph_atlas.schema import Box, Confidence, Line, Page, ReviewState, Unit, UnitKind

RUN = Run(name="test-v1")


def detection(x: int, y: int, w: int = 30, h: int = 30) -> Det:
    """A detection as the aligner sees it: a box and a detector score."""
    return Detection(box=Box(x=x, y=y, w=w, h=h), score=0.5)


def scattered() -> list[Det]:
    """A column of six characters whose detections arrive in the order a detector returned them.

    The y values run 0, 60, 120, 180, 240, 300 down the page; the x values differ by a pixel, which
    is exactly the case that made `(-x, y)` sort them by that pixel rather than by y.
    """
    ys = [240, 0, 300, 60, 180, 120]
    return [detection(x=100 + (index % 3), y=y) for index, y in enumerate(ys)]


def line_of(text: str, box: Box | None = None) -> Line:
    return Line(id="doc:0:L0", page_id="doc:0", seq=1, box=box, text_raw=text, text=text,
                match_method="ainu-ink-columns-v1")


def unit_of(seq: int, text: str, box: Box | None, *, review: ReviewState = ReviewState.MACHINE,
            crop: str | None = None, confidence: Confidence | None = None) -> Unit:
    return Unit(id=f"doc:0:L0:test:{seq}", page_id="doc:0", document_id="doc", line_id="doc:0:L0",
                seq=seq, box=box, text_source=text, reading=text, unicode=f"U+{ord(text):04X}",
                kind=UnitKind.CHAR, method="detect-align", review=review, crop=crop,
                crop_sha256=crop.split("/")[-1] if crop else None, confidence=confidence)


def costs_of(rows: list[list[float]]) -> list[list[float]]:
    return [list(row) for row in rows]


# --- the deterministic layer ---------------------------------------------------------------------


def test_reading_order_puts_a_vertical_line_back_in_reading_order() -> None:
    """The fix the corpus asked for: a column's detections are walked down the page, not by x."""
    ordered = align.reading_order(scattered())
    assert [box.y for box in (item.box for item in ordered)] == [0, 60, 120, 180, 240, 300]


def test_a_space_is_not_ink() -> None:
    """Two ideographic spaces open these transcriptions and neither is a written character."""
    assert not repair.is_ink(Token(text="\u3000", start=0, end=1))
    assert not repair.is_ink(Token(text="", start=0, end=0, kind=UnitKind.GAP))
    assert repair.is_ink(Token(text="手", start=0, end=1))


def test_a_space_holding_a_box_gives_it_up() -> None:
    """The corpus shows the run gave the leading spaces detections; the pass takes them back."""
    space = unit_of(1, "\u3000", Box(x=95, y=0, w=30, h=30))
    views = [
        repair.LineView(
            line=line_of("\u3000あ"), tokens=[Token(text="\u3000", start=0, end=1),
                                              Token(text="あ", start=1, end=2)],
            detections=[detection(95, 0), detection(100, 60)],
            units=[
                repair.UnitView(index=0, token=Token(text="\u3000", start=0, end=1), unit=space,
                                ink=False, detection=None, detection_index=None,
                                old_box=space.box),
                repair.UnitView(index=1, token=Token(text="あ", start=1, end=2), unit=None, ink=True,
                                detection=detection(100, 60), detection_index=1,
                                old_box=None),
            ],
            phases=[repair.Phase(first=0, cost=0.0, matched=1, unplaced=0, unused=0, per_pair=0.0,
                                 assignment={1: 1})],
            chosen=repair.Phase(first=0, cost=0.0, matched=1, unplaced=0, unused=0, per_pair=0.0,
                                assignment={1: 1}),
            unshifted=repair.Phase(first=0, cost=0.0, matched=1, unplaced=0, unused=0, per_pair=0.0,
                                   assignment={1: 1}),
            reason="", gap=repair.Gap(), before={}, after={},
        )
    ]
    decision, records = repair.decide(views)
    space_record = records[0]
    assert space_record.status == "applied"
    assert space_record.layer == "deterministic"
    assert space_record.new_box is None
    assert "space is not ink" in space_record.reason
    assert decision.moved == 1


# --- the proposal layer --------------------------------------------------------------------------


def test_a_shift_that_wins_on_the_characters_is_proposed() -> None:
    """A rigid offset is proposed only when it also wins character by character."""
    ink = [0, 1, 2, 3]
    # The shifted phase is better on every character the two phases both place, and the unshifted one
    # leaves the last detection unused.
    costs = costs_of([[9.0, 0.1, 9.0, 9.0], [9.0, 9.0, 0.1, 9.0], [9.0, 9.0, 9.0, 0.2],
                      [9.0, 9.0, 9.0, 9.0]])
    phases = repair.phase_costs(costs, ink, len(costs[0]), RUN.weights)
    chosen, _, reason = repair.choose_phase(phases, costs, ink)
    assert chosen.first == 1
    assert chosen.wins == 3 and chosen.common == 3
    assert "characters both phases place" in reason


def test_a_shift_that_only_covers_fewer_characters_is_refused() -> None:
    """A phase may not win by paying skip penalties instead of match costs."""
    ink = [0, 1, 2, 3, 4, 5]
    # The shift is cheaper in total and better a matched pair, and it gets both by moving five
    # characters onto worse ink to escape one terrible pair — which is exactly what the per-character
    # count refuses.
    costs = costs_of([[9.0] * 7 for _ in range(6)])
    for row in range(5):
        costs[row][row] = 0.1
        costs[row][row + 1] = 1.0
    costs[5][5] = 20.0
    costs[5][6] = 9.0
    phases = repair.phase_costs(costs, ink, 7, RUN.weights)
    chosen, _, reason = repair.choose_phase(phases, costs, ink)
    assert chosen.first == 0
    assert "characters both phases place" in reason


def test_the_phase_margin_is_measured_a_matched_pair_at_a_time() -> None:
    """The pair margin is the number a phase cannot buy by matching less."""
    ink = [0, 1]
    costs = costs_of([[4.0, 1.0], [4.0, 1.0]])
    phases = repair.phase_costs(costs, ink, 2, RUN.weights)
    shifted = next(phase for phase in phases if phase.first == 1)
    unshifted = next(phase for phase in phases if phase.first == 0)
    assert shifted.per_pair == pytest.approx(1.0)
    assert unshifted.per_pair == pytest.approx(2.5)


# --- ownership: one crop, one character ----------------------------------------------------------


def record_for(seq: int, old: Box | None, new: Box | None, *,
               status: str = "applied") -> repair.RepairRecord:
    return repair.RepairRecord(id=f"ar{seq:06d}", unit_id=f"doc:0:L0:test:{seq}",
                               line_id="doc:0:L0", seq=seq, old_box=old, new_box=new,
                               status=status)  # type: ignore[arg-type]


def test_a_three_cycle_with_a_withheld_middle_leaves_one_owner_a_box() -> None:
    """The regression the per-unit gate cannot see on its own.

    Three characters rotate: A takes B's old box, B is withheld and keeps its own, C takes A's old
    box. Deciding them one at a time leaves A and B on one rectangle. The pass has to put A back,
    which then frees B's box, and the result is that every box has one owner — even though it means
    the line is not a clean rotation any more.
    """
    a, b, c = Box(x=0, y=0, w=10, h=10), Box(x=0, y=20, w=10, h=10), Box(x=0, y=40, w=10, h=10)
    records = [record_for(1, a, b), record_for(2, b, b, status="uncertain"), record_for(3, c, a)]
    result = repair.resolve_collisions(records)
    assert result["reverted"] >= 1
    owners: dict[tuple[int, int, int, int], int] = {}
    for record in records:
        box = repair.final_box(record)
        if box is None:
            continue
        key = (box.x, box.y, box.w, box.h)
        owners[key] = owners.get(key, 0) + 1
    assert max(owners.values()) == 1
    assert records[0].status == "uncertain"
    assert "same box" in records[0].reason
    assert records[0].new_box == b, "the box it wanted is kept on the record as the proposal it is"


def test_a_human_box_is_never_taken_by_a_move() -> None:
    """A reviewer's box is the ink their answer was read against; a move onto it is put back."""
    anchor = Box(x=0, y=20, w=10, h=10)
    records = [record_for(1, Box(x=0, y=0, w=10, h=10), anchor),
               record_for(2, anchor, anchor, status="human")]
    repair.resolve_collisions(records)
    assert records[0].status == "uncertain"
    assert repair.final_box(records[0]) == Box(x=0, y=0, w=10, h=10)
    assert repair.final_box(records[1]) == anchor


def test_a_collision_the_source_already_had_is_reported_not_touched() -> None:
    """Two retained units on one box is the state the pass found, and it may not make it worse."""
    box = Box(x=0, y=0, w=10, h=10)
    records = [record_for(1, box, box, status="unchanged"),
               record_for(2, box, box, status="unchanged")]
    result = repair.resolve_collisions(records)
    assert result["reverted"] == 0
    assert result["baseline-duplicates"] == 1


def test_collisions_finds_two_owners_of_one_box() -> None:
    """The invariant checked on finished rows: one crop, one character."""
    units = [unit_of(1, "手", Box(x=0, y=0, w=10, h=10)), unit_of(2, "を", Box(x=0, y=0, w=10, h=10))]
    found = repair.collisions(units)
    assert len(found) == 1
    assert sorted(next(iter(found.values()))) == sorted([unit.id for unit in units])


# --- what a written row looks like ---------------------------------------------------------------


def small_dataset(directory: Path) -> tuple[list[Unit], list[Line]]:
    """One page, one line of three characters and three detections in a scrambled order."""
    boxes = {"あ": Box(x=0, y=120, w=10, h=10), "い": Box(x=0, y=0, w=10, h=10),
             "う": Box(x=0, y=60, w=10, h=10)}
    units = [unit_of(seq, text, boxes[text], crop=f"https://example.invalid/{text}.png",
                     confidence=Confidence(text=0.5, detection=0.4))
             for seq, text in enumerate("あいう", start=1)]
    line = line_of("あいう", Box(x=0, y=0, w=10, h=130))
    tables.write(directory / "pages.parquet",
                 [Page(id="doc:0", document_id="doc", seq=0, canvas="a4", image="i",
                       width=100, height=200)], Page)
    tables.write(directory / "lines.parquet", [line], Line)
    tables.write(directory / "units.parquet", units, Unit)
    return units, [line]


def plan_for(directory: Path) -> repair.RepairPlan:
    """A plan whose boxes are the reading order, made without a classifier or an image."""
    detections = {"doc:0": [Box(x=0, y=120, w=10, h=10), Box(x=0, y=0, w=10, h=10),
                            Box(x=0, y=60, w=10, h=10)]}
    line = tables.read(directory / "lines.parquet", Line)[0]
    units = tables.read(directory / "units.parquet", Unit)
    views = repair.view_line(
        line,
        [Detection(box=box, score=0.5) for box in detections["doc:0"]],
        run=RUN,
        classifier=None,
        crop_of=None,
        units=units,
    )
    decision, records = repair.decide(views, line=line)
    plan = repair.RepairPlan(created="now", source={"directory": str(directory)})
    plan.lines.append(decision)
    plan.records.extend(records)
    for index, record in enumerate(plan.records, start=1):
        record.id = f"ar{index:06d}"
    return plan


def test_a_moved_unit_drops_its_crop_and_its_confidence(tmp_path: Path) -> None:
    """A pre-cut crop file is a picture of the old rectangle; a viewer must not draw it."""
    source = tmp_path / "source"
    small_dataset(source)
    plan = plan_for(source)
    moved = [record for record in plan.records if record.status in ("applied", "confirmed")]
    assert moved, "the scrambled order has to move something"
    counts = repair.apply_plan(plan, tmp_path / "derived", source=source)
    assert counts["applied"] == len(moved)
    units = {unit.id: unit for unit in tables.read(tmp_path / "derived" / "units.parquet", Unit)}
    for record in moved:
        unit = units[record.unit_id]
        assert unit.crop is None and unit.crop_sha256 is None and unit.confidence is None
        assert unit.meta[repair.META_KEY]["reliable"] is True
        assert unit.box == record.new_box


def test_apply_then_undo_restores_the_rows_exactly(tmp_path: Path) -> None:
    """Reversibility is a property of the log: the same row, byte for byte, comes back."""
    source = tmp_path / "source"
    small_dataset(source)
    before = {unit.id: unit.model_dump(mode="json")
              for unit in tables.read(source / "units.parquet", Unit)}
    plan = plan_for(source)
    derived = tmp_path / "derived"
    repair.apply_plan(plan, derived, source=source)
    counts = repair.undo_plan(derived)
    assert counts["restored"] > 0
    after = {unit.id: unit.model_dump(mode="json")
             for unit in tables.read(derived / "units.parquet", Unit)}
    assert after == before


def test_replay_puts_the_correction_back(tmp_path: Path) -> None:
    """Undo and replay are the same log read in two directions."""
    source = tmp_path / "source"
    small_dataset(source)
    plan = plan_for(source)
    derived = tmp_path / "derived"
    repair.apply_plan(plan, derived, source=source)
    applied = {unit.id: unit.model_dump(mode="json")
               for unit in tables.read(derived / "units.parquet", Unit)}
    repair.undo_plan(derived)
    repair.replay_plan(derived)
    again = {unit.id: unit.model_dump(mode="json")
             for unit in tables.read(derived / "units.parquet", Unit)}
    assert again == applied


def test_withheld_units_are_marked_for_quiz_suppression(tmp_path: Path) -> None:
    """A crop the pass could not judge may not be shown as an exemplar of its character."""
    record = record_for(1, Box(x=0, y=0, w=10, h=10), Box(x=0, y=20, w=10, h=10),
                        status="uncertain")
    unit = unit_of(1, "手", Box(x=0, y=0, w=10, h=10))
    repair._move_unit(unit, record)
    note = unit.meta[repair.META_KEY]
    assert note["reliable"] is False and note["withheld"] is True and note["quiz"] is False
    assert unit.box == Box(x=0, y=0, w=10, h=10), "a withheld record does not move the unit"


def test_a_human_unit_is_written_as_the_store_has_it(tmp_path: Path) -> None:
    """The machine may not overwrite a person's row, even when the order disagrees with it."""
    source = tmp_path / "source"
    small_dataset(source)
    plan = plan_for(source)
    human_id = plan.records[0].unit_id
    plan.records[0].status = "human"
    plan.records[0].machine = False
    repair.apply_plan(plan, tmp_path / "derived", source=source)
    unit = next(unit for unit in tables.read(tmp_path / "derived" / "units.parquet", Unit)
                if unit.id == human_id)
    assert unit.review is ReviewState.MACHINE
    assert repair.META_KEY not in (unit.meta or {}) or unit.meta[repair.META_KEY]["status"] != "human"


def test_verify_reports_a_clean_derived_dataset(tmp_path: Path) -> None:
    """The invariant is checked on the dataset a viewer reads, not on the plan."""
    source = tmp_path / "source"
    small_dataset(source)
    plan = plan_for(source)
    derived = tmp_path / "derived"
    repair.apply_plan(plan, derived, source=source)
    report = repair.verify_dataset(derived)
    assert report["collisions"] == {}
    assert report["stale-crops"] == []
    assert report["reliable"] >= 1


def test_a_synthetic_page_repairs_a_one_position_shift() -> None:
    """A page whose column carries one character the transcription does not: the phase says so."""
    ys = [0, 40, 80, 120]
    detections = [detection(x=100, y=y) for y in ys]
    ordered = align.reading_order(detections)
    assert [item.box.y for item in ordered] == ys
    ink = [0, 1, 2]
    # Character 0 fits detection 1 and character 1 fits detection 2 and so on: the column opens with
    # a character the transcription does not have.
    costs = costs_of([[8.0, 0.2, 8.0, 8.0], [8.0, 8.0, 0.2, 8.0], [8.0, 8.0, 8.0, 0.2]])
    phases = repair.phase_costs(costs, ink, 4, RUN.weights)
    chosen, _, reason = repair.choose_phase(phases, costs, ink)
    assert chosen.first == 1
    assert chosen.assignment == {0: 1, 1: 2, 2: 3}
    assert chosen.wins == 3 and chosen.common == 3
    assert "characters both phases place" in reason


def test_undo_and_replay_are_idempotent(tmp_path: Path) -> None:
    """Running either twice is running it once, because both are driven by the rows themselves."""
    source = tmp_path / "source"
    small_dataset(source)
    plan = plan_for(source)
    derived = tmp_path / "derived"
    repair.apply_plan(plan, derived, source=source)
    first = repair.undo_plan(derived)
    second = repair.undo_plan(derived)
    assert second["restored"] == 0 and second["notes-cleared"] == 0
    assert first["restored"] >= 1
    assert repair.replay_plan(derived)["replayed"] >= 1
    assert repair.replay_plan(derived)["replayed"] == 0


def test_an_uncertain_note_does_not_touch_the_row(tmp_path: Path) -> None:
    """Withholding is a note about a crop, not a change to it."""
    source = tmp_path / "source"
    small_dataset(source)
    plan = plan_for(source)
    before = {unit.id: unit.model_dump(mode="json")
              for unit in tables.read(source / "units.parquet", Unit)}
    for record in plan.records:
        record.status = "uncertain"
    derived = tmp_path / "derived"
    counts = repair.apply_plan(plan, derived, source=source)
    assert counts["applied"] == 0
    after = {unit.id: unit.model_dump(mode="json")
             for unit in tables.read(derived / "units.parquet", Unit)}
    for unit_id, row in after.items():
        assert row["box"] == before[unit_id]["box"]
        assert row["confidence"] == before[unit_id]["confidence"]
        assert row["meta"][repair.META_KEY]["withheld"] is True
        assert row["meta"][repair.META_KEY]["reliable"] is False


def test_a_review_event_is_written_as_the_store_reads_it() -> None:
    """The store keeps `old` and `new` as JSON text; a log copied verbatim is JSON inside JSON."""
    row = {"seq": 7, "id": "rv00000007", "target_type": "unit", "target_id": "u1", "field": "review",
           "old": '"rejected"', "new": '"reviewed"', "role": "reviewer", "actor": "r1",
           "evidence": '{"kind": "character-review"}', "at": "2026-09-20T05:10:13+00:00",
           "client_id": "r1", "idempotency_key": "k", "result": '{"created": []}'}
    decoded = repair.decode_event(row)
    assert decoded["old"] == "rejected" and decoded["new"] == "reviewed"
    assert decoded["evidence"] == '{"kind": "character-review"}'
    assert "seq" not in decoded and "result" not in decoded and "client_id" not in decoded
    from glyph_atlas.schema import Review

    Review.model_validate(decoded)


def test_a_line_is_placed_as_one_sequence_or_not_at_all(tmp_path: Path) -> None:
    """A half-placed line puts the moved characters beside the wrong neighbours.

    One line of three characters: the first already holds its ink, the middle one has no box, and the
    last holds the middle one's ink. A move is only correct together with the moves its neighbours
    make under the same phase, so the pass takes the move back, and the first character keeps its box
    without being vouched for. Asked to place new ink, the same line places as a whole.
    """
    source = tmp_path / "source"
    units = [unit_of(1, "あ", Box(x=0, y=0, w=10, h=10)),
             unit_of(2, "い", None),
             unit_of(3, "う", Box(x=0, y=60, w=10, h=10))]
    line = line_of("あいう", Box(x=0, y=0, w=10, h=130))
    tables.write(source / "pages.parquet",
                 [Page(id="doc:0", document_id="doc", seq=0, canvas="a4", image="i",
                       width=100, height=200)], Page)
    tables.write(source / "lines.parquet", [line], Line)
    tables.write(source / "units.parquet", units, Unit)
    scattered = [Box(x=0, y=120, w=10, h=10), Box(x=0, y=0, w=10, h=10),
                 Box(x=0, y=60, w=10, h=10)]
    views = repair.view_line(
        line, [Detection(box=box, score=0.5) for box in scattered],
        run=RUN, classifier=None, crop_of=None, units=units)

    decision, records = repair.decide(views, line=line)
    ink = [record for record in records if record.ink]
    assert decision.status == "uncertain"
    assert all(record.status == "uncertain" for record in ink)
    assert all(repair.final_box(record) == record.old_box for record in records)
    assert any("one sequence or not at all" in record.reason for record in ink)
    assert ink[2].new_box is not None, "the box it wanted stays on the record as the proposal it is"
    assert all(not record.reliable for record in ink), "a withheld line vouches for none of its crops"

    decision, records = repair.decide(views, line=line, place_missing=True)
    assert decision.status == "reorder"
    assert [record.status for record in records if record.ink] == ["unchanged", "applied", "applied"]


def test_a_locked_box_blocks_the_moves_that_would_land_on_it(tmp_path: Path) -> None:
    """The three-cycle again, at the dataset level: locking one box locks the rotation.

    The three characters rotate through each other's boxes, so no box is duplicated by the rotation
    alone. Locking the first one — the row a reviewer owns — means the third one's move lands on it,
    and the pass has to give that move back; giving it back puts the third unit on the second's new
    box, and that move goes too. The result is that nothing moves, which is the only assignment that
    leaves one owner a box, and it is reported rather than silently applied.
    """
    source = tmp_path / "source"
    small_dataset(source)
    plan = plan_for(source)
    plan.records[0].status = "human"
    plan.records[0].machine = False
    derived = tmp_path / "derived"
    counts = repair.apply_plan(plan, derived, source=source)
    assert counts["applied"] == 0 and counts["reliable"] == 0
    assert counts["withheld-noted"] == len(plan.records) - 1, "the human row gets no machine note"
    assert repair.verify_dataset(derived)["collisions"] == {}
    lock = plan.records[0]
    unit = next(item for item in tables.read(derived / "units.parquet", Unit)
                if item.id == lock.unit_id)
    assert unit.box == lock.old_box


def test_only_a_machine_record_is_reliable() -> None:
    """`confirmed` and `reliable` are statements about the pipeline, never about a person."""
    record = record_for(1, Box(x=0, y=0, w=10, h=10), Box(x=0, y=20, w=10, h=10))
    assert record.reliable is True
    record.status = "uncertain"
    assert record.reliable is False
    record.status = "confirmed"
    record.machine = False
    assert record.reliable is False, "a human row is not a machine confirmation"


def test_a_persisted_path_carries_no_home_directory(tmp_path: Path, monkeypatch) -> None:
    """A plan or a report is published; a path in one may not name the machine it was made on."""
    inside = tmp_path / "dataset" / "units.parquet"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    assert repair.public_path(inside) == "dataset/units.parquet"
    away = Path.home() / "somewhere" / "units.parquet"
    assert repair.public_path(away) == "~/somewhere/units.parquet"
    assert repair.public_path(None) is None
    evidence = repair.file_evidence(inside)
    assert evidence["path"] == "dataset/units.parquet"


# --- the journal is carried whole, not only the rows a repair can overlay ------------------------


def page_dataset(directory: Path) -> Path:
    """One page with a transcription, the shape a page correction is written against."""
    from glyph_atlas.schema import Document, PageText

    document = Document(id="hk:d1", title="蝦夷紀行")
    page = Page(id="hk:d1:0", document_id="hk:d1", seq=0, canvas="c", image="i",
                width=1000, height=800)
    lines = [Line(id="hk:d1:0:L0", page_id="hk:d1:0", seq=0, box=None,
                  text_raw="文化五辰年の秋再ひ間宮林蔵をして北蝦夷の奥地に",
                  text="文化五辰年の秋再ひ間宮林蔵をして北蝦夷の奥地に"),
             Line(id="hk:d1:0:L1", page_id="hk:d1:0", seq=1, box=None,
                  text_raw="至らしむるに其年の七月十三日本蝦夷ソウヤを出",
                  text="至らしむるに其年の七月十三日本蝦夷ソウヤを出")]
    tables.write(directory / "documents.parquet", [document], Document)
    tables.write(directory / "pages.parquet", [page], Page)
    tables.write(directory / "lines.parquet", lines, Line)
    tables.write(directory / "page_texts.parquet",
                 [PageText(page_id=page.id, source="ainu-records",
                           text_raw="【右丁】\n\n蝦夷紀行巻之上\n" + lines[0].text_raw + "\n"
                                    + lines[1].text_raw + "\n")], PageText)
    return directory


def test_a_page_correction_is_carried_into_the_derived_journal(tmp_path: Path) -> None:
    """A correction is an event on the *page*, and a derived dataset that dropped it would lose it.

    The store's correction layer writes `target_type="page", field="correction"` (see
    `review.corrections`): there is no unit or line row for this pass to overlay, and the store's own
    change dispatcher leaves the projection alone. The journal is therefore read whole — every target
    type — while the overlay stays unit-and-line, and the event travels into `reviews.jsonl` so a
    store opened on the derived dataset rebuilds it.
    """
    from glyph_atlas.review import corrections
    from glyph_atlas.review.store import Store

    source = page_dataset(tmp_path / "source")
    store = Store(source)
    corrections.record(store, corrections.Correction(
        id="ezo-kiko-souya", page_id="hk:d1:0", line=3, original="ソウヤ", corrected="ソウヤ湾",
        note="原画像の左丁3行目を確認。"))
    store.export()

    state = repair.human_state(source)
    assert state.events == 1, "the journal holds the page event"
    assert state.targets["page"] == 1
    assert state.rows and state.rows[0]["target_type"] == "page"
    assert "ソウヤ湾" in json.dumps(repair.decode_event(state.rows[0]), ensure_ascii=False)

    plan = repair.RepairPlan(created="now", source={"directory": str(source)})
    derived = tmp_path / "derived"
    counts = repair.apply_plan(plan, derived, source=source)
    assert counts["reviews"] == 1
    carried = [json.loads(line) for line in (derived / repair.REVIEWS_NAME).read_text().splitlines()
               if line.strip()]
    assert [event["target_type"] for event in carried] == ["page"]

    reopened = Store(derived)
    rebuilt = reopened.rebuild()
    assert rebuilt["adopted"] == 1, "the page event is rebuilt on the derived dataset"
    read_back = corrections.page_corrections(reopened, "hk:d1:0",
                                             reopened.page_text("hk:d1:0") or "")
    assert [item.correction.corrected for item in read_back.applied] == ["ソウヤ湾"]
    assert "ソウヤ湾" in read_back.text()


# --- review findings -----------------------------------------------------------------------------


def _store_with_line(directory: Path, line: Line) -> None:
    """A review store holding one person's edit of a line, as `human_state` reads it."""
    import sqlite3

    with sqlite3.connect(directory / repair.STORE_NAME) as db:
        db.execute("CREATE TABLE events (seq INTEGER, id TEXT, target_type TEXT, target_id TEXT, "
                   "field TEXT, old TEXT, new TEXT, role TEXT, actor TEXT, evidence TEXT, at TEXT, "
                   "client_id TEXT, idempotency_key TEXT, result TEXT)")
        db.execute("CREATE TABLE lines (id TEXT, data TEXT)")
        db.execute("CREATE TABLE units (id TEXT, data TEXT)")
        db.execute("INSERT INTO events VALUES (1, 'rv00000001', 'line', ?, 'box', 'null', 'null', "
                   "'reviewer', 'r1', NULL, '2026-09-24T00:00:00+00:00', 'r1', 'k1', NULL)", (line.id,))
        db.execute("INSERT INTO lines VALUES (?, ?)", (line.id, line.model_dump_json()))


def test_a_withheld_unit_on_a_shared_box_keeps_its_crop() -> None:
    """Only a move is put back; a withheld unit still holds its own box and its own crop."""
    box = Box(x=0, y=0, w=10, h=10)
    kept = unit_of(1, "あ", box, crop="https://example.invalid/a.png")
    kept.meta = {repair.META_KEY: {"status": "uncertain", "old_box": box.model_dump()}}
    other = unit_of(2, "い", box)
    result = repair.enforce_injective([kept, other])
    assert result == {"reverted": 0, "baseline": 1}
    assert kept.crop == "https://example.invalid/a.png"
    assert "put back" not in kept.meta[repair.META_KEY].get("reason", "")


def test_a_unit_the_store_holds_is_pinned_whatever_its_review_state() -> None:
    """The store's row is laid over every unit it holds, so none of them may be moved."""
    boxes = [Box(x=0, y=120, w=10, h=10), Box(x=0, y=0, w=10, h=10), Box(x=0, y=60, w=10, h=10)]
    units = [unit_of(seq, text, boxes[seq - 1]) for seq, text in enumerate("あいう", start=1)]
    line = line_of("あいう", Box(x=0, y=0, w=10, h=130))
    views = repair.view_line(line, [Detection(box=box, score=0.5) for box in boxes], run=RUN,
                             units=units)
    _, records = repair.decide(views, line=line, pinned={units[0].id})
    assert records[0].status == "human"
    assert repair.final_box(records[0]) == boxes[0]


def test_a_line_whose_units_do_not_pair_with_its_tokens_is_not_placed() -> None:
    """A split in the old run leaves fewer units than tokens, and pairing by rank drifts after it."""
    boxes = [Box(x=0, y=120, w=10, h=10), Box(x=0, y=0, w=10, h=10), Box(x=0, y=60, w=10, h=10)]
    units = [unit_of(1, "あ", boxes[0]), unit_of(2, "う", boxes[1])]
    line = line_of("あいう", Box(x=0, y=0, w=10, h=130))
    views = repair.view_line(line, [Detection(box=box, score=0.5) for box in boxes], run=RUN,
                             units=units)
    decision, records = repair.decide(views, line=line, place_missing=True)
    assert decision.status == "uncertain"
    assert all(repair.final_box(record) == record.old_box for record in records)
    assert any("one for one" in record.reason for record in records)


def test_a_pinned_units_ink_is_not_given_away() -> None:
    """The box the order would give a pinned unit stays with whoever holds it now."""
    ink, hand = Box(x=0, y=0, w=10, h=10), Box(x=2, y=2, w=6, h=6)
    units = [unit_of(1, "あ", hand, review=ReviewState.REVIEWED), unit_of(2, "い", ink)]
    line = line_of("あい", Box(x=0, y=0, w=10, h=130))
    views = repair.view_line(line, [Detection(box=ink, score=0.5)], run=RUN, units=units)
    _, records = repair.decide(views, line=line)
    assert repair.final_box(records[1]) == ink


def test_the_phase_margin_reaches_the_phase_choice(monkeypatch) -> None:
    """A threshold recorded in the plan is a threshold that was applied."""
    seen: list[float] = []
    real = repair.choose_phase

    def spy(*args, **kwargs):
        seen.append(kwargs.get("min_total"))
        return real(*args, **kwargs)

    monkeypatch.setattr(repair, "choose_phase", spy)
    boxes = [Box(x=0, y=0, w=10, h=10), Box(x=0, y=60, w=10, h=10)]
    line = line_of("あい", Box(x=0, y=0, w=10, h=130))
    repair.view_line(line, [Detection(box=box, score=0.5) for box in boxes], run=RUN,
                     units=[unit_of(1, "あ", boxes[0]), unit_of(2, "い", boxes[1])],
                     min_phase_margin=7.5)
    assert seen == [7.5]


def test_a_person_edited_line_is_overlaid_on_a_sharded_lines_table(tmp_path: Path) -> None:
    """A dataset whose lines are shards still gets the line a person corrected."""
    source = tmp_path / "source"
    small_dataset(source)
    plan = plan_for(source)
    line = tables.read(source / "lines.parquet", Line)[0]
    (source / "lines.parquet").unlink()
    tables.write(source / "lines", [line], Line, shard=True)
    edited = line.model_copy(update={"box": Box(x=1, y=1, w=9, h=129)})
    _store_with_line(source, edited)
    derived = tmp_path / "derived"
    counts = repair.apply_plan(plan, derived, source=source)
    assert counts.get("lines-with-human") == 1
    assert tables.read(derived / "lines", Line)[0].box == edited.box
