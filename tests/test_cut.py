import json

import numpy as np
import pytest

from glyph_atlas import align, cut
from glyph_atlas.schema import Box, Line, ReviewState

BLANK = np.zeros((400, 400), dtype=bool)


def tokens(text):
    return align.tokens_of(Line(id="l", page_id="p", seq=0, text=text, text_raw=text, box=Box(x=0, y=0, w=100, h=400)))


def detection(y, h, *, index, parts=1, source="detection", score=.5):
    return cut.Cell(along=(y, y + h), across=(10, 50), source=source, score=score, detection=index, parts=parts)


def work(text, cells, reads):
    """A line whose candidate box over cells `[start, end)` reads as `reads[(start, end)]`: character to probability."""
    found = tokens(text)
    spans = cut.spans_of(cells, (40.0, 40.0))
    costs = np.full((len(found), len(spans)), cut.FLOOR)
    for i, token in enumerate(found):
        for span in spans:
            mass = reads.get((span.start, span.end), {}).get(token.text, 0.0)
            costs[i, span.index] = -np.log(max(mass, align.PROBABILITY_FLOOR))
    for span in spans:
        span.features = cut.describe(span, cut.Frame(True, BLANK), (40.0, 40.0), np.array([.9, .1]))
        span.features.update(quality=0.0, quality_given=0.0, shift=0.0)
    return cut.Work(line_id="l", tokens=found, cells=cells, spans=spans, costs=costs, known=[True] * len(found),
                    frame=cut.Frame(True, BLANK), sizes=(40.0, 40.0))


def place(text, cells, reads):
    return cut.place(work(text, cells, reads), cut.WEIGHTS, None)


def test_each_token_takes_its_own_detection():
    cells = [detection(0, 40, index=0), detection(50, 40, index=1)]
    placed = place("字学", cells, {(0, 1): {"字": .9}, (1, 2): {"学": .9}})
    assert [p.kind for p in placed] == ["box", "box"]
    assert [(p.box.y, p.box.h) for p in placed] == [(0, 40), (50, 40)]


def test_a_detection_over_two_characters_is_cut():
    """The detection's two parts each read as a character; the whole box reads as neither."""
    cells = [detection(0, 40, index=0), detection(50, 40, index=1, parts=2, source="part"),
             detection(90, 40, index=1, parts=2, source="part")]
    placed = place("字学校", cells, {(0, 1): {"字": .9}, (1, 2): {"学": .9}, (2, 3): {"校": .9}})
    assert [p.kind for p in placed] == ["box", "part", "part"]
    assert placed[1].box.y == 50 and placed[2].box.y == 90


def test_the_whole_of_a_cut_detection_is_still_one_box():
    cells = [detection(0, 40, index=0, parts=2, source="part"), detection(40, 40, index=0, parts=2, source="part")]
    placed = place("事", cells, {(0, 2): {"事": .9}})
    assert placed[0].kind == "box" and (placed[0].box.y, placed[0].box.h) == (0, 80)


def test_all_the_parts_of_a_detection_are_the_detection_s_own_box():
    """Its parts are trimmed to their ink; the detector's box is what the detector drew, however tall."""
    whole = ((0, 130), (8, 52))
    cells = [cut.Cell(along=(y, y + 40), across=(10, 50), source="part", score=.5, detection=0, parts=3, whole=whole)
             for y in (2, 45, 88)]
    spans = cut.spans_of(cells, (40.0, 40.0))
    box = next(span for span in spans if span.kind == "box")
    assert (box.along, box.across) == whole
    assert all(span.along[1] - span.along[0] <= 2.8 * 40 for span in spans if span.kind != "box")


def test_ink_is_never_read_from_the_far_side_of_the_page():
    ink = np.zeros((200, 200), dtype=bool)
    ink[100:150, 100:150] = True
    frame = cut.Frame(True, ink)
    assert frame.patch((-3, -1), (0, 40)).size == 0
    span = cut.Span(start=0, end=1, along=(0, 30), across=(0, 15), kind="box", score=.5, valley=0.0)
    found = cut.describe(span, frame, (40.0, 40.0), np.array([.9, .1]))
    assert found["ink_before"] == 0 and found["ink_beside"] == 0


def test_two_detections_over_one_character_are_joined():
    cells = [detection(0, 20, index=0), detection(22, 20, index=1), detection(60, 40, index=2)]
    placed = place("字学", cells, {(0, 2): {"字": .9}, (2, 3): {"学": .9}})
    assert [p.kind for p in placed] == ["merge", "box"]
    assert (placed[0].box.y, placed[0].box.h) == (0, 42)


def test_a_character_the_detector_missed_is_found_in_its_ink():
    cells = [detection(0, 40, index=0), cut.Cell(along=(50, 85), across=(12, 48), source="ink"),
             detection(100, 40, index=1)]
    placed = place("字之学", cells, {(0, 1): {"字": .9}, (1, 2): {"之": .6}, (2, 3): {"学": .9}})
    assert [p.kind for p in placed] == ["box", "ink", "box"]


def test_ink_and_detections_the_transcription_has_no_character_for_are_passed():
    cells = [detection(0, 40, index=0), cut.Cell(along=(50, 60), across=(12, 48), source="ink"),
             detection(70, 40, index=1), detection(120, 40, index=2)]
    placed = place("字学", cells, {(0, 1): {"字": .9}, (3, 4): {"学": .9}})
    assert [(p.kind, p.box.y) for p in placed] == [("box", 0), ("box", 120)]
    assert placed[1].features["passed"] == 2


def test_a_character_with_no_ink_gets_no_box():
    placed = place("字学", [detection(0, 40, index=0)], {(0, 1): {"字": .9}})
    assert [p.kind for p in placed] == ["box", "skip"] and placed[1].box is None


def test_side_by_side_cells_are_never_one_box():
    beside = cut.Cell(along=(0, 40), across=(60, 100), source="detection", score=.5, detection=1)
    spans = cut.spans_of([detection(0, 40, index=0), beside], (40.0, 40.0))
    assert [(span.start, span.end) for span in spans] == [(0, 1), (1, 2)]


def test_the_margin_is_small_where_another_placement_costs_as_little():
    """Two identical characters over three identical boxes: either box may be the one passed."""
    cells = [detection(y, 40, index=i) for i, y in enumerate((0, 50, 100))]
    reads = {(i, i + 1): {"字": .9} for i in range(3)}
    unsure = place("字字", cells, reads)
    sure = place("字字", cells[:2], reads)
    assert unsure[0].features["margin"] < 1 < sure[0].features["margin"]
    assert unsure[0].features["log_posterior"] < sure[0].features["log_posterior"]


def test_boxes_inside_a_box_become_its_parts():
    def found(y, h, w=40):
        return align.Detection(box=Box(x=10, y=y, w=w, h=h), score=.5)
    holders = cut._nested([found(0, 90), found(2, 40, 38), found(48, 40, 38), found(200, 40)])
    assert holders == {0: [1, 2]}
    line = Line(id="l", page_id="p", seq=0, text="字学", text_raw="字学", box=Box(x=0, y=0, w=60, h=100))
    cells, _ = cut.cells_of(line, [found(0, 90), found(2, 40, 38), found(48, 40, 38)], cut.Frame(True, BLANK),
                            (40.0, 40.0))
    assert [(cell.source, cell.along, cell.parts) for cell in cells] == [("part", (2, 42), 2), ("part", (48, 88), 2)]


def test_unboxed_ink_between_detections_becomes_a_cell():
    pytest.importorskip("cv2")
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (800, 1200), (228, 214, 180))
    draw = ImageDraw.Draw(image)
    for top in (120, 200, 280):
        draw.rectangle((380, top, 419, top + 39), fill=(40, 36, 30))
    ink = cut.ink_map(image)
    assert ink[130, 400] and not ink[170, 400] and not ink[600, 100]
    line = Line(id="l", page_id="p", seq=0, text="字学校", text_raw="字学校", box=Box(x=360, y=110, w=80, h=220))
    found = [align.Detection(box=Box(x=380, y=top, w=40, h=40), score=.5) for top in (120, 280)]
    cells, sizes = cut.cells_of(line, found, cut.Frame(True, ink), (40.0, 40.0))
    assert sizes == (40.0, 40.0)
    assert [cell.source for cell in cells] == ["detection", "ink", "detection"]
    assert cells[1].along == (200, 240) and cells[1].across == (380, 420)


def test_touching_characters_in_unboxed_ink_are_cut_where_the_ink_thins():
    ink = np.zeros((300, 100), dtype=bool)
    ink[10:48, 30:70] = True
    ink[48:52, 48:52] = True
    ink[52:90, 30:70] = True
    cells = cut._ink_cells(cut.Frame(True, ink), (0, 120), (20, 80), 40.0, 40.0, 0)
    (top, cut_at), (resumed, bottom) = (cell.along for cell in cells)
    assert (top, bottom) == (10, 90) and cut_at == resumed and 48 <= cut_at <= 52


def test_the_models_are_read_from_json(tmp_path):
    model = {"features": ["a", "b"], "mean": [0, 0], "scale": [1, 2], "weights": [1.0, -2.0], "bias": 0.5,
             "accept": 0.9, "version": "v"}
    (tmp_path / "gate.json").write_text(json.dumps(model))
    gate = cut.Logistic(tmp_path / "gate.json")
    assert gate.probabilities([{"a": 1.0, "b": 2.0, "c": 9.0}])[0] == pytest.approx(1 / (1 + np.exp(.5)))
    assert gate.accept == .9 and gate.version == "v" and len(gate.probabilities([])) == 0


def test_a_cutter_accepts_by_its_gate(tmp_path, monkeypatch):
    names = ["cost"]
    for name, bias in (("frame.json", 0.0), ("gate.json", 4.0)):
        (tmp_path / name).write_text(json.dumps({"features": names if name == "gate.json" else ["top"], "mean": [0],
                                                 "scale": [1], "weights": [-1.0 if name == "gate.json" else 0.0],
                                                 "bias": bias, "accept": 0.9, "version": "test"}))
    cells = [detection(0, 40, index=0), detection(50, 40, index=1)]
    prepared = work("字学", cells, {(0, 1): {"字": .9}, (1, 2): {"学": .01}})
    monkeypatch.setattr(cut, "prepare_page", lambda *a, **k: [prepared])
    line = Line(id="l", page_id="p", seq=0, text="字学", text_raw="字学", box=Box(x=0, y=0, w=100, h=400))
    placed = cut.Cutter(tmp_path).cut([line], [], None, None)["l"]
    assert [p.accepted for p in placed] == [True, False], "the gate reads the classifier's cost of each box"
    units = cut.units_of(line, placed, run="test", version="test")
    assert [unit.review for unit in units] == [ReviewState.MACHINE, ReviewState.REJECTED]
    assert units[0].upstream["cut"] == "box" and units[0].confidence.segmentation == pytest.approx(placed[0].probability)
