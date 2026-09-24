"""Tests of the pre-review joined-crop scan: what it calls joined, and what it refuses to call.

The crops are synthetic: a character is a small ink block drawn into a cell, a joined crop is two or
three of those stacked with blank rows between them, and a single character is one cell. The
recognizer is a stand-in for `review.refine.SplitEngine` that answers from the crop it is handed — a
lookup on the crop's size and ink — so the scan's decisions are exercised without a model, and the
stand-in never sees a threshold, a verdict or a line of this module.

What is pinned down here is the set of distinctions the pass exists for: a stack read as a sequence
is joined and withheld; one encoded ligature read as a sequence is still one character; a tall single
character is a question for a person rather than a finding; a crop is never suspicious because its
text is long; a failure to reach the model is `unavailable` and not `uncertain`, and a weak reading is
`uncertain` and not `joined`; and a second run continues from the ledger instead of rescanning.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from glyph_atlas.review import preflight
from glyph_atlas.schema import Box, ReviewState, Unit, UnitKind

PAGE = "hk:doc:1"
LINE = f"{PAGE}:L1"


def unit(seq: int, *, height: int, width: int = 30, text: str = "ア", unicode: str | None = None,
         review: ReviewState = ReviewState.MACHINE, kind: UnitKind = UnitKind.CHAR,
         page: str = PAGE, line: str = LINE) -> Unit:
    """One placed unit, at `height` pixels tall."""
    return Unit(
        id=f"{line}:fp:{seq}", page_id=page, document_id="hk:doc", line_id=line, seq=seq,
        box=Box(x=10, y=100 + seq * 60, w=width, h=height), kind=kind, text_source=text,
        reading=text, unicode=unicode or f"U+{ord(text[0]):04X}", review=review, method="detect-align",
    )


class StubStore:
    """The four reads the scan makes, and nothing else."""

    def __init__(self, units: list[Unit], *, revisions: dict[str, int] | None = None,
                 human: set[str] | None = None, pages: dict[str, tuple[int, int]] | None = None):
        self._units = list(units)
        self._revisions = dict(revisions or {})
        self._human = set(human or ())
        self._pages = dict(pages or {page: (600, 800) for page in {u.page_id for u in units}})
        self.reads = 0

    def set_revision(self, unit_id: str, revision: int) -> None:
        self._revisions[unit_id] = revision

    def unit_snapshot(self, unit_id: str | None = None):
        self.reads += 1
        rows = [(u, self._revisions.get(u.id, 1)) for u in self._units]
        return [row for row in rows if unit_id is None or row[0].id == unit_id]

    def events(self):
        return [type("E", (), {"target_id": target, "role": "reviewer", "field": "review"})() for target in self._human]

    def page(self, page_id: str):
        size = self._pages.get(page_id)
        return None if size is None else type("P", (), {"width": size[0], "height": size[1]})()

    def line(self, line_id: str):
        return type("L", (), {"id": line_id, "text": "", "vertical": True})()


def test_short_triangle_join_is_withheld_even_when_whole_ocr_loses_marker():
    pytest.importorskip("cv2")
    from types import SimpleNamespace

    from glyph_atlas.symbol_parts import recognize_parts

    image = Image.new("RGB", (40, 50), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 2, 30, 20), fill="black")
    draw.polygon([(4, 46), (20, 30), (36, 46)], fill="black")
    values = iter(["シ", "▲"])

    def child(_):
        text = next(values)
        votes = [{"engine": engine, "text": text, "score": .99}
                 for engine in ("NDLkotenOCR", "Atlas classifier")]
        return {"votes": votes, "candidates": votes[:1]}

    partition = recognize_parts(image, child)
    assert partition and partition["accepted"]
    # Deliberately weak whole-crop OCR; it cannot override the separate region.
    whole = {"engine": "NDLkotenOCR", "text": "シ", "score": .6}
    model = SimpleNamespace(read=lambda _: {"votes": [whole], "symbol_partition": partition})
    current = unit(1, height=50, width=40)
    engine = SimpleNamespace(model=model)
    report = preflight.scan(StubStore([current]), engine=engine,
                           crop_of=lambda *_: (image, "digest"))
    assert report.records[0].verdict == "joined"
    assert report.records[0].withhold
    assert report.records[0].ocr.text == "シ"
    assert report.records[0].ocr.symbol_partition["text"] == ["シ", "▲"]


def stacked(count: int, *, cell: int = 44, gap: int = 6, ink: int = 0) -> Image.Image:
    """`count` ink cells stacked with blank rows between them, one character a cell."""
    height = count * cell + (count - 1) * gap
    return fill(height, count, ink=ink)


def fill(height: int, count: int, *, width: int = 30, ink: int = 0) -> Image.Image:
    """`count` blocks of ink filling a crop exactly `height` pixels tall, blank rows between them.

    The crop of a unit is its box, so a test crop has to be the size the box says: the scan hashes
    the pixels and measures the ink inside that rectangle, and a stand-in recognizer keyed on the
    crop's size would otherwise be answering about a different image than the geometry describes.
    """
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    if count <= 1:
        draw.rectangle([6, 3, 23, height - 4], fill=ink)
        return image.convert("RGB")
    gap = 6
    cell = (height - gap * (count - 1)) // count
    for index in range(count):
        top = index * (cell + gap)
        draw.rectangle([6, top + 3, 23, top + cell - 3], fill=ink)
    return image.convert("RGB")


def solid(height: int, *, width: int = 30) -> Image.Image:
    """One tall block of ink with no blank run in it at all."""
    return fill(height, 1, width=width)


def crops_for(table: dict[str, Image.Image]):
    """A crop provider that answers from a unit id, and records the page order it was asked in."""
    asked: list[str] = []

    def provider(store, item):
        asked.append(item.page_id or "")
        return table[item.id], f"digest-{item.id}"

    provider.asked = asked  # type: ignore[attr-defined]
    return provider


class ScriptedEngine:
    """A stand-in for `SplitEngine`: `assess` answers from the crop's size, never from this module.

    One entry per crop size: `(reading, score)`. A size that is not in the table answers "no joined
    sequence recognized", which is what the real engine answers for a crop it reads as one character.
    `raises` makes the whole model unavailable, which is how a missing ONNX session behaves.
    """

    def __init__(self, table: dict[tuple[int, int], tuple[str, float]], *,
                 raises: Exception | None = None, split: dict | None = None):
        self.table = table
        self.raises = raises
        self.split = split
        self.calls: list[tuple[tuple[int, int], str | None]] = []

    def assess(self, crop: Image.Image, expected: str | None = None) -> dict:
        self.calls.append((crop.size, expected))
        if self.raises is not None:
            raise self.raises
        found = self.table.get(crop.size)
        engines = [{"name": "NDLkotenOCR", "sha256": "stub"}, {"name": "Atlas classifier"}]
        if found is None:
            return {"accepted": False, "reason": "no joined sequence recognized",
                    "engines": engines, "sequence": None}
        text, score = found
        if expected is not None and self.split is not None:
            return {**self.split, "engines": engines,
                    "sequence": {"text": text, "score": score, "engine": "NDLkotenOCR"}}
        return {"accepted": score >= 0.9, "reason": "confidence" if score >= 0.9 else "sequence OCR is uncertain",
                "engines": engines, "sequence": {"text": text, "score": score, "engine": "NDLkotenOCR"}}


def guard_for(ligatures: set[str]):
    """A character layer that knows the ligatures a test tells it about, and records the questions."""
    asked: list[str] = []

    def check(code_point: str):
        asked.append(code_point)
        return object() if code_point in ligatures else None

    check.asked = asked  # type: ignore[attr-defined]
    return check


def run(units: list[Unit], table: dict[str, Image.Image], ocr: dict, **kwargs):
    """Scan a stub store with a scripted provider and engine."""
    store = StubStore(units, revisions=kwargs.pop("revisions", None), human=kwargs.pop("human", None))
    provider = crops_for(table)
    engine = kwargs.pop("engine", None) or ScriptedEngine(ocr)
    report = preflight.scan(store, crop_of=provider, engine=engine, **kwargs)
    return report, provider, engine


# --- the joins -----------------------------------------------------------------------------------


def test_two_stacked_characters_are_joined_and_withheld() -> None:
    """The case the pass exists for: one box, two characters, a reading and a visible stack."""
    one = unit(1, height=44)
    two = unit(2, height=94)
    report, _, _engine = run([one, two], {one.id: stacked(1), two.id: stacked(2)},
                             {(30, 94): ("アイ", 0.96)}, guard=guard_for(set()),
                             propose_splits=True)
    joined = report.by_verdict("joined")
    assert [record.unit_id for record in joined] == [two.id]
    record = joined[0]
    assert record.withhold is True and report.withheld == [two.id]
    assert report.whole_crop_reads == 1 and report.split_attempts == 1
    assert record.ocr and record.ocr.text == "アイ" and record.ocr.characters == 2
    assert record.ink.valley_depth > 0.9 and record.ink.height_ratio > 1.9
    assert record.split is not None, "a confident join is offered to the split engine"
    assert [record.verdict for record in report.records if record.unit_id == one.id] == ["single"]
    assert report.ocr_calls == report.whole_crop_reads == 1, "the ordinary crop costs no read"


def test_three_stacked_characters_are_joined() -> None:
    """Three in one box is the same finding as two, and the reading says how many."""
    two = unit(1, height=94)
    three = unit(2, height=144)
    report, _, _ = run([two, three], {two.id: stacked(2), three.id: stacked(3)},
                       {(30, 144): ("アイウ", 0.93), (30, 94): ("アイ", 0.95)},
                       guard=guard_for(set()))
    assert len(report.by_verdict("joined")) == 2
    assert report.by_verdict("joined")[1].ocr.characters == 3
    assert report.counts == {"joined": 2}


def test_a_ligature_read_as_two_characters_is_still_one() -> None:
    """𪜈 reads トモ and its ink is one character: the identity guard decides, not the reading."""
    ligature = unit(1, height=94, text="𪜈", unicode="U+2A708")
    ordinary = unit(2, height=44)
    table = {ligature.id: stacked(2), ordinary.id: stacked(1)}
    report, _, engine = run([ligature, ordinary], table, {(30, 94): ("トモ", 0.99)},
                            guard=guard_for({"U+2A708"}))
    record = report.records[0]
    assert record.verdict == "single" and record.withhold is False
    assert record.ligature == "U+2A708" and "one encoded ligature" in record.reason
    assert engine.calls == [], "a ligature costs no recognizer call: its identity already answers"
    assert report.ocr_calls == 0


def test_a_ligature_layer_that_cannot_answer_withholds_a_join(monkeypatch) -> None:
    """Without the overlay a multi-character reading cannot be told from a ligature: uncertain."""
    monkeypatch.setattr(preflight.refs, "ligature", None)
    two = unit(1, height=94)
    report, _, _ = run([two], {two.id: stacked(2)}, {(30, 94): ("トモ", 0.97)})
    record = report.records[0]
    assert record.verdict == "uncertain" and record.withhold is False
    assert "ligature layer is unavailable" in record.reason
    assert record.split is None


def test_a_reading_that_spells_a_ligature_is_not_a_ligature() -> None:
    """The live case: reading トキ over a parent whose own code point is キ, with two separated characters.

    A transcription reads トキ, the character layer knows U+1B124 as the digraph トキ, and the unit's
    encoded identity is U+30AD — one character. The guard is asked about the identity and nothing
    else, so the ink's two separated cells and the recognizer's sequence are a join, not a ligature.
    """
    parent = unit(1, height=44, text="キ", unicode="U+30AD")
    joined_crop = unit(2, height=94, text="トキ", unicode="U+30AD")
    guard = guard_for({"U+1B124"})
    report, _, _ = run([parent, joined_crop], {parent.id: fill(44, 1), joined_crop.id: fill(94, 2)},
                       {(30, 94): ("トキ", 0.96)}, guard=guard)
    record = next(item for item in report.records if item.unit_id == joined_crop.id)
    assert record.verdict == "joined" and record.ligature is None
    assert set(guard.asked) == {"U+30AD"}, "the guard sees the encoded identity, never the reading"
    assert "トキ" not in guard.asked


def test_the_digraph_itself_is_one_character_whatever_it_reads() -> None:
    """U+1B124 is one encoded character: its crop is not a join however its reading spells."""
    digraph = unit(1, height=94, text="トキ", unicode="U+1B124")
    report, _, engine = run([digraph], {digraph.id: fill(94, 2)}, {(30, 94): ("トキ", 0.98)},
                            guard=guard_for({"U+1B124"}))
    record = report.records[0]
    assert record.verdict == "single" and record.ligature == "U+1B124"
    assert record.withhold is False and record.split is None
    assert engine.calls == [], "an encoded ligature is never handed to the recognizer"


def test_a_partial_boundary_set_is_still_joined() -> None:
    """Three characters read, two boundaries proposed: the crop is a join and is withheld as one."""
    source = unit(1, height=144)
    engine = ScriptedEngine({(30, 144): ("とりい", 0.99)},
                            split={"accepted": True, "reason": "two of three", "text": ["とり", "い"],
                                   "boxes": [{"x": 0, "y": 0, "w": 30, "h": 78},
                                             {"x": 0, "y": 84, "w": 30, "h": 60}]})
    report = preflight.scan(StubStore([source]), crop_of=crops_for({source.id: fill(144, 3)}),
                            engine=engine, guard=guard_for(set()), propose_splits=True)
    record = report.records[0]
    assert record.verdict == "joined" and record.withhold is True
    assert record.ocr and record.ocr.characters == 3
    assert record.split_state == "partial" and record.status == "joined:partial"
    assert "still a join" in record.reason
    assert report.split_attempts == 1
    assert len(record.split["boxes"]) == 2, "the partial boundaries are kept, not discarded"


def test_no_split_work_is_done_unless_it_is_asked_for() -> None:
    """The default scan reads crops and names joins; the boundaries are a second, opt-in pass."""
    units = [unit(index, height=94) for index in range(1, 4)]
    table = {item.id: stacked(2) for item in units}
    engine = ScriptedEngine({(30, 94): ("アイ", 0.95)}, split={"accepted": True, "boxes": [
        {"x": 0, "y": 0, "w": 30, "h": 44}, {"x": 0, "y": 50, "w": 30, "h": 44}], "reason": "two"})
    quiet = preflight.scan(StubStore(units), crop_of=crops_for(table), engine=engine,
                           guard=guard_for(set()))
    assert quiet.split_attempts == 0 and quiet.whole_crop_reads == 3
    assert all(record.split is None for record in quiet.records)
    assert all(record.split_state == "not-attempted" for record in quiet.records)
    assert {record.status for record in quiet.records} == {"joined:not-attempted"}
    assert len(quiet.withheld) == 3, "a join is still a join before anyone has looked for a boundary"
    assert all(expected is None for _, expected in engine.calls), (
        "the read asks for a reading, never for an assessment"
    )
    asked = preflight.scan(StubStore(units), crop_of=crops_for(table), engine=engine,
                           guard=guard_for(set()), propose_splits=True)
    assert asked.split_attempts == 3
    assert {record.split_state for record in asked.records} == {"proposed"}
    assessments = [expected for _, expected in engine.calls if expected is not None]
    assert assessments[-3:] == ["アイ", "アイ", "アイ"], (
        "only the split stage asks with a reading; every read asks with none"
    )


def test_the_sequence_floor_is_high_enough_to_withhold_a_crop() -> None:
    """Withholding a crop from single-character review is not done on a merely likely reading."""
    assert preflight.Thresholds().min_sequence_score >= 0.92
    two = unit(1, height=94)
    for score, verdict in ((0.50, "uncertain"), (0.85, "uncertain"), (0.91, "uncertain"),
                           (0.92, "joined"), (0.99, "joined")):
        report, _, _ = run([two], {two.id: stacked(2)}, {(30, 94): ("アイ", score)},
                           guard=guard_for(set()))
        assert report.records[0].verdict == verdict, f"a score of {score} decided {report.records[0].verdict}"


class ModelEngine:
    """A stand-in for the real `SplitEngine`: it has a `.model` whose `read` is the cheap path.

    The counters show what a scan costs: `reads` is one whole-crop read, `unit_assessments` is the
    expensive path that proposes children, and a scan that has not been asked to propose any must
    leave the second at zero.
    """

    def __init__(self, reading: tuple[str, float]):
        self.reading = reading
        self.reads = 0
        self.unit_assessments = 0
        self.model = self

    def read(self, crop):
        self.reads += 1
        text, score = self.reading
        return {
            "status": "ready",
            "engines": [{"name": "NDLkotenOCR", "provider": "CPUExecutionProvider"}],
            "votes": [
                {"engine": "NDLkotenOCR", "text": text, "score": score},
                {"engine": "Atlas classifier", "text": text[0], "score": 0.999},
            ],
        }

    def assess_unit(self, unit, crop, expected=None):
        self.unit_assessments += 1
        return {"accepted": True, "boxes": [{"x": 0, "y": 0, "w": 30, "h": 44},
                                            {"x": 0, "y": 50, "w": 30, "h": 44}],
                "text": [expected or "", ""], "reason": "two"}


def test_the_whole_crop_read_is_one_cheap_call_and_keeps_the_models_apart() -> None:
    """A read, not an assessment, for every suspicious crop; the classifier's vote is evidence only."""
    two = unit(1, height=94)
    engine = ModelEngine(("アイ", 0.95))
    report = preflight.scan(StubStore([two]), crop_of=crops_for({two.id: stacked(2)}), engine=engine,
                            guard=guard_for(set()))
    assert engine.reads == 1 and engine.unit_assessments == 0
    record = report.records[0]
    assert record.verdict == "joined"
    assert record.ocr.text == "アイ" and record.ocr.score == 0.95
    assert [vote["engine"] for vote in record.ocr.votes] == ["NDLkotenOCR", "Atlas classifier"], (
        "every engine's own answer is kept, and the classifier's 0.999 does not become the reading"
    )
    assert record.ocr.characters == 2
    assert record.ocr.engines == [{"name": "NDLkotenOCR", "provider": "CPUExecutionProvider"}]


# --- what is not a join --------------------------------------------------------------------------


def test_a_tall_single_character_is_a_question_not_a_finding() -> None:
    """Ink tall enough for two read as one character: a person decides, the pass does not."""
    neighbour = unit(1, height=44)
    tall = unit(2, height=80)
    report, _, engine = run([neighbour, tall], {neighbour.id: fill(44, 1), tall.id: fill(80, 1)},
                            {(30, 80): ("ア", 0.97)}, guard=guard_for(set()))
    record = next(item for item in report.records if item.unit_id == tall.id)
    assert record.verdict == "uncertain" and record.withhold is False
    assert "one character recognized" in record.reason
    assert record.split is None
    assert engine.calls == [((30, 80), None)], "no split is attempted for an uncertain crop"


def test_a_multi_character_reading_without_a_stack_is_uncertain() -> None:
    """A reading is not ink: with no blank run and no height, the sequence is not believed."""
    neighbour = unit(1, height=44)
    plain = unit(2, height=70)
    report, _, _ = run([neighbour, plain], {neighbour.id: fill(44, 1), plain.id: solid(70)},
                       {(30, 70): ("アイ", 0.95)}, guard=guard_for(set()))
    record = next(item for item in report.records if item.unit_id == plain.id)
    assert record.verdict == "uncertain"
    assert "the ink shows no blank run" in record.reason
    assert record.split is None, "an unstacked crop is not handed to a splitter"


def test_text_length_never_moves_a_verdict() -> None:
    """Two identical crops, one labelled with two characters: the ink decides, so the votes match."""
    short = unit(1, height=44, text="あ")
    long = unit(2, height=44, text="あい")
    tall_long = unit(3, height=94, text="あい")
    report, _, _ = run([short, long, tall_long],
                       {short.id: stacked(1), long.id: stacked(1), tall_long.id: stacked(2)},
                       {(30, 94): ("アイ", 0.95)}, guard=guard_for(set()))
    by_id = {record.unit_id: record for record in report.records}
    assert by_id[short.id].verdict == by_id[long.id].verdict == "single"
    assert by_id[short.id].reason == by_id[long.id].reason
    assert by_id[tall_long.id].verdict == "joined", "the ink, and only the ink, made it a join"


def test_no_equal_division_is_ever_proposed() -> None:
    """A suspicious crop the model cannot read is a failure, and no cut is invented for it."""
    two = unit(1, height=94)
    report, _, _engine = run([two], {two.id: stacked(2)}, {},
                             engine=ScriptedEngine({}, raises=RuntimeError("no onnx session")),
                             guard=guard_for(set()))
    record = report.records[0]
    assert record.verdict == "unavailable" and record.reason.startswith("the recognizer could not")
    assert record.ocr is not None and "RuntimeError" in (record.ocr.error or "")
    assert record.split is None and record.withhold is False
    assert record.box == two.box, "the record describes the crop; it does not cut it"


def test_a_crop_that_cannot_be_read_is_unavailable_not_uncertain() -> None:
    """A page whose pixels cannot be reached is a state to retry, kept apart from a weak reading."""
    two = unit(1, height=94)

    def provider(store, item):
        raise ValueError("source image unavailable")

    store = StubStore([two])
    report = preflight.scan(store, crop_of=provider, engine=ScriptedEngine({}),
                            guard=guard_for(set()))
    record = report.records[0]
    assert record.verdict == "unavailable" and record.image_sha256 == ""
    assert "could not be read from the page" in record.reason


def test_a_weak_reading_is_uncertain_and_a_strong_one_is_joined() -> None:
    """The recognizer's own score decides between a finding and a question."""
    two = unit(1, height=94)
    weak, _, _ = run([two], {two.id: stacked(2)}, {(30, 94): ("アイ", 0.31)}, guard=guard_for(set()))
    edge, _, _ = run([two], {two.id: stacked(2)}, {(30, 94): ("アイ", 0.90)}, guard=guard_for(set()))
    strong, _, _ = run([two], {two.id: stacked(2)}, {(30, 94): ("アイ", 0.95)}, guard=guard_for(set()))
    assert weak.records[0].verdict == "uncertain"
    assert "scores 0.31" in weak.records[0].reason
    assert edge.records[0].verdict == "uncertain", "just under the floor is a question, not a finding"
    assert "scores 0.90" in edge.records[0].reason
    assert strong.records[0].verdict == "joined"
    assert weak.whole_crop_reads == strong.whole_crop_reads == 1


def test_human_units_are_counted_and_never_inspected() -> None:
    """A person's answer is not this pass's to revisit, and no crop of theirs is even decoded."""
    mine = unit(1, height=44)
    theirs = unit(2, height=94, review=ReviewState.REVIEWED)
    also_theirs = unit(3, height=94)
    table = {mine.id: stacked(1), theirs.id: stacked(2), also_theirs.id: stacked(2)}
    report, provider, _ = run([mine, theirs, also_theirs], table, {(30, 94): ("アイ", 0.95)},
                              human={also_theirs.id}, guard=guard_for(set()))
    assert report.skipped_human == 2
    assert {record.unit_id for record in report.records} == {mine.id}
    assert theirs.id not in provider.asked and also_theirs.id not in provider.asked


# --- the budget, the resume and the page grouping ------------------------------------------------


def test_the_budget_counts_whole_crop_reads() -> None:
    """`limit` is calls, not units: an ordinary crop is decided without spending one."""
    units = [unit(index, height=94) for index in range(1, 5)] + [unit(9, height=44)]
    table = {item.id: stacked(2) for item in units[:4]}
    table[units[4].id] = stacked(1)
    ocr = {(30, 94): ("アイ", 0.95)}
    first, _, engine = run(units, table, ocr, limit=2, guard=guard_for(set()))
    assert first.ocr_calls == 2 and first.stopped == "limit"
    assert len(first.records) == 2
    assert engine.calls and all(size == (30, 94) for size, _ in engine.calls)
    second, _, _ = run(units, table, ocr, limit=8, guard=guard_for(set()))
    assert second.whole_crop_reads == 4, "the stopped run left the rest to be picked up"
    assert {record.verdict for record in second.records} == {"joined", "single"}


def test_a_second_run_skips_what_the_ledger_has_decided(tmp_path: Path) -> None:
    """The fingerprint is the decision: same image, same box, same revision, no second call."""
    two = unit(1, height=94)
    one = unit(2, height=44)
    table = {two.id: stacked(2), one.id: stacked(1)}
    state = preflight.ScanState(tmp_path / "scan.jsonl")
    first, _, _engine = run([two, one], table, {(30, 94): ("アイ", 0.95)}, state=state,
                            guard=guard_for(set()))
    assert first.scanned == 2 and first.ocr_calls == 1
    assert (tmp_path / "scan.jsonl").is_file()
    again, _, second_engine = run([two, one], table, {(30, 94): ("アイ", 0.95)}, state=state,
                                  guard=guard_for(set()))
    assert again.scanned == 0 and again.skipped_done == 2 and again.ocr_calls == 0
    assert second_engine.calls == []


def test_a_changed_revision_or_a_changed_image_is_scanned_again(tmp_path: Path) -> None:
    """A box that moved, a revision that changed or new pixels are a different decision."""
    two = unit(1, height=94)
    state = preflight.ScanState(tmp_path / "scan.jsonl")
    run([two], {two.id: stacked(2)}, {(30, 94): ("アイ", 0.95)}, state=state, guard=guard_for(set()))
    moved = two.model_copy(update={"box": Box(x=12, y=140, w=30, h=94)})
    changed, _, _ = run([moved], {moved.id: stacked(2)}, {(30, 94): ("アイ", 0.95)}, state=state,
                        guard=guard_for(set()))
    assert changed.scanned == 1 and changed.skipped_done == 0
    state2 = preflight.ScanState(tmp_path / "scan2.jsonl")
    store = StubStore([two], revisions={two.id: 1})
    engine = ScriptedEngine({(30, 94): ("アイ", 0.95)})
    preflight.scan(store, crop_of=crops_for({two.id: stacked(2)}), engine=engine, state=state2,
                   guard=guard_for(set()))
    store.set_revision(two.id, 2)
    revised = preflight.scan(store, crop_of=crops_for({two.id: stacked(2)}), engine=engine,
                             state=state2, guard=guard_for(set()))
    assert revised.scanned == 1, "a new revision is a new decision"
    fresh = preflight.scan(store, crop_of=crops_for({two.id: stacked(3)}), engine=engine,
                           state=state2, guard=guard_for(set()))
    assert fresh.scanned == 1 and fresh.records[0].image_sha256 != revised.records[0].image_sha256


def test_pages_are_visited_one_at_a_time() -> None:
    """Grouping by page is what lets one decoded image serve every crop of that page."""
    first = unit(1, height=94, page="hk:doc:2", line="hk:doc:2:L1")
    second = unit(2, height=94, page="hk:doc:1", line="hk:doc:1:L1")
    third = unit(3, height=94, page="hk:doc:2", line="hk:doc:2:L2")
    table = {item.id: stacked(2) for item in (first, second, third)}
    report, provider, _ = run([first, second, third], table, {(30, 94): ("アイ", 0.95)},
                              guard=guard_for(set()))
    assert provider.asked == ["hk:doc:1", "hk:doc:2", "hk:doc:2"]
    assert len(report.by_verdict("joined")) == 3


def test_every_record_carries_the_evidence_a_reviewer_needs() -> None:
    """One record a crop: what it is, which pixels, what was measured and what was read."""
    two = unit(1, height=94)
    report, _, _ = run([two], {two.id: stacked(2)}, {(30, 94): ("アイ", 0.95)}, guard=guard_for(set()))
    record = report.records[0]
    payload = record.model_dump(mode="json")
    assert payload["unit_id"] == two.id and payload["revision"] == 1
    assert len(payload["image_sha256"]) == 64 and len(payload["fingerprint"]) == 32
    assert payload["box"] == two.box.model_dump()
    assert set(payload["ink"]) >= {"aspect", "height_ratio", "valley_depth", "reference_row_ink"}
    assert payload["ocr"]["engines"] and payload["ocr"]["text"] == "アイ"
    assert report.thresholds["min_sequence_score"] == preflight.Thresholds().min_sequence_score
    assert json.dumps(payload, ensure_ascii=False)


def test_a_progress_callback_sees_each_record_once() -> None:
    """A caller running batches can show where the pass is without polling the store."""
    units = [unit(index, height=94) for index in range(1, 4)]
    table = {item.id: stacked(2) for item in units}
    seen: list[tuple[int, int, str]] = []
    store = StubStore(units)
    preflight.scan(store, crop_of=crops_for(table), engine=ScriptedEngine({(30, 94): ("アイ", .95)}),
                   guard=guard_for(set()),
                   on_progress=lambda done, total, record: seen.append((done, total, record.verdict)))
    assert seen == [(1, 3, "joined"), (2, 3, "joined"), (3, 3, "joined")]


def test_the_scan_writes_nothing_to_the_store() -> None:
    """Read-only is the contract: no revision, no box and no review state is touched."""
    two = unit(1, height=94)
    store = StubStore([two])
    before = two.model_dump(mode="json")
    preflight.scan(store, crop_of=crops_for({two.id: stacked(2)}),
                   engine=ScriptedEngine({(30, 94): ("アイ", .95)}), guard=guard_for(set()))
    assert store.unit_snapshot()[0][0].model_dump(mode="json") == before
    assert store.reads >= 1


def test_the_split_engine_is_only_asked_about_joins() -> None:
    """A refusal from the splitter does not demote the finding, and an ordinary crop is never split."""
    two = unit(1, height=94)
    one = unit(2, height=44)
    engine = ScriptedEngine({(30, 94): ("アイ", 0.95), (30, 44): ("ア", 0.99)},
                            split={"accepted": False, "reason": "no low-ink valley"})
    report = preflight.scan(StubStore([two, one]),
                            crop_of=crops_for({two.id: stacked(2), one.id: stacked(1)}), engine=engine,
                            guard=guard_for(set()), propose_splits=True)
    assert report.records[0].verdict == "joined"
    split = report.records[0].split or {}
    assert split.get("accepted") is False and split.get("reason") == "no low-ink valley"
    assert report.records[0].split_state == "refused"
    assert report.records[0].status == "joined:refused"
    assert report.records[0].withhold is True, "a refused split is still not a single-character crop"
    assert engine.calls.count(((30, 44), None)) == 0, "the ordinary crop is never handed to a splitter"
    assert engine.calls[-1] == ((30, 94), "アイ"), "the join is asked about, with its reading"


class UnitAwareEngine(ScriptedEngine):
    """A stand-in for the current `SplitEngine`: the identity-aware entry point as well as `assess`."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.unit_calls: list[tuple[str, str | None, tuple[int, int], str | None]] = []

    def assess_unit(self, unit, crop, expected=None):
        self.unit_calls.append((unit.id, unit.unicode, crop.size, expected))
        return self.assess(crop, expected)


def test_the_identity_aware_entry_point_is_preferred_when_the_engine_has_it() -> None:
    """The engine is handed the unit, so the character layer sees the encoded identity, not a reading.

    A reading that spells a ligature's components is not evidence of one glyph. The scan passes the
    unit through when the engine offers `assess_unit`, and falls back to the crop-only `assess` for an
    engine that does not — which is what every other test here uses.
    """
    parent = unit(1, height=44, text="キ", unicode="U+30AD")
    sequence = unit(2, height=94, text="トキ", unicode="U+30AD")
    engine = UnitAwareEngine({(30, 94): ("トキ", 0.96)}, split={"accepted": True, "boxes": [
        {"x": 0, "y": 0, "w": 30, "h": 44}, {"x": 0, "y": 50, "w": 30, "h": 44}], "reason": "two"})
    report = preflight.scan(
        StubStore([parent, sequence]),
        crop_of=crops_for({parent.id: fill(44, 1), sequence.id: fill(94, 2)}),
        engine=engine, guard=guard_for({"U+1B124"}), propose_splits=True,
    )
    assert [call[:2] for call in engine.unit_calls] == [(sequence.id, "U+30AD")] * 2
    assert [call[3] for call in engine.unit_calls] == [None, "トキ"], "the split is asked about the reading"
    record = next(item for item in report.records if item.unit_id == sequence.id)
    assert record.verdict == "joined" and record.split_state == "proposed"
    assert record.split["accepted"] is True


def test_an_engine_with_only_assess_still_works() -> None:
    """The fallback is the old call: a stale or stand-in engine is never handed a unit it cannot take."""
    two = unit(1, height=94)
    engine = ScriptedEngine({(30, 94): ("アイ", 0.95)})
    assert not hasattr(engine, "assess_unit")
    quiet = preflight.scan(StubStore([two]), crop_of=crops_for({two.id: fill(94, 2)}), engine=engine,
                           guard=guard_for(set()))
    assert quiet.records[0].verdict == "joined" and quiet.split_attempts == 0
    asked = preflight.scan(StubStore([two]), crop_of=crops_for({two.id: fill(94, 2)}), engine=engine,
                           guard=guard_for(set()), propose_splits=True)
    assert asked.records[0].verdict == "joined" and asked.split_attempts == 1
    assert engine.calls == [((30, 94), None), ((30, 94), None), ((30, 94), "アイ")], (
        "the read is the crop-only call; the identity-aware call is only the optional split"
    )
