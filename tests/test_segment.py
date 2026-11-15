import numpy as np
import pytest

from glyph_atlas import cut, segment
from glyph_atlas.align import Detection
from glyph_atlas.schema import Box


class Framing:
    """Reads each candidate's probability from the feature a test gave it."""

    def probabilities(self, rows):
        return np.array([row["p"] for row in rows])


def work(cells, odds):
    """A column of `cells` whose candidates frame a character with the probability `odds` gives their kind."""
    spans = cut.spans_of(cells, (40.0, 40.0))
    for span in spans:
        span.features = {"p": odds[(span.kind, span.start, span.end)] if (span.kind, span.start, span.end) in odds
                         else odds[span.kind]}
    return cut.Work(line_id="column-0", tokens=[], cells=cells, spans=spans, costs=np.zeros((0, 0)), known=[],
                    frame=cut.Frame(True, np.zeros((1, 1), dtype=bool)), sizes=(40.0, 40.0))


def halves():
    whole = ((0, 80), (0, 40))
    return [cut.Cell(along=(0, 40), across=(0, 40), source="part", detection=0, parts=2, whole=whole),
            cut.Cell(along=(40, 80), across=(0, 40), source="part", detection=0, parts=2, whole=whole)]


def kinds(chosen):
    return [span.kind for span, _ in chosen]


def test_a_box_is_cut_when_its_parts_frame_characters_better_than_the_whole():
    assert kinds(segment.choose(work(halves(), {"part": 0.8, "box": 0.3}), Framing())) == ["part", "part"]
    assert kinds(segment.choose(work(halves(), {"part": 0.6, "box": 0.9}), Framing())) == ["box"]
    # One doubtful part does not keep a box whole that frames nothing.
    doubtful = {("part", 0, 1): 0.8, ("part", 1, 2): 0.3, "box": 0.001}
    assert kinds(segment.choose(work(halves(), doubtful), Framing())) == ["part", "part"]


def test_a_cut_gives_up_the_margin():
    column = work(halves(), {"part": 0.7, "box": 0.6})
    assert kinds(segment.choose(column, Framing())) == ["part", "part"]
    assert kinds(segment.choose(column, Framing(), margin=1.0)) == ["box"]


def test_boxes_are_joined_only_into_a_likely_character():
    pair = [cut.Cell(along=(0, 20), across=(0, 40), source="detection", detection=0),
            cut.Cell(along=(22, 40), across=(0, 40), source="detection", detection=1)]
    assert kinds(segment.choose(work(pair, {"box": 0.1, "merge": 0.4}), Framing())) == ["box", "box"]
    assert kinds(segment.choose(work(pair, {"box": 0.1, "merge": 0.9}), Framing())) == ["merge"]


def test_unboxed_ink_becomes_a_box_when_it_is_likely_a_character_and_a_detection_is_never_lost():
    cells = [cut.Cell(along=(0, 40), across=(0, 40), source="detection", detection=0),
             cut.Cell(along=(45, 85), across=(0, 40), source="ink")]
    assert kinds(segment.choose(work(cells, {"box": 1e-9, "ink": 0.3, "mixed": 0.1}), Framing())) == ["box"]
    assert kinds(segment.choose(work(cells, {"box": 1e-9, "ink": 0.7, "mixed": 0.1}), Framing())) == ["box", "ink"]


def test_a_box_its_column_leaves_whole_is_cut_by_its_own_shape():
    pytest.importorskip("cv2")
    from PIL import Image

    page = np.full((1200, 800), 235, dtype=np.uint8)
    boxes = [Box(x=100, y=100 + 120 * row, w=100, h=100) for row in range(6)]
    # Two small characters, one under the other, in one box beside a column of large ones.
    small = [Box(x=400, y=300, w=44, h=44), Box(x=400, y=356, w=44, h=44)]
    for box in [*boxes, *small]:
        page[box.y + 4:box.y + box.h - 4, box.x + 4:box.x + box.w - 4] = 20
    # Two side by side in one box.
    side = [Box(x=400, y=700, w=44, h=44), Box(x=456, y=700, w=44, h=44)]
    for box in side:
        page[box.y + 4:box.y + box.h - 4, box.x + 4:box.x + box.w - 4] = 20
    detections = [Detection(box=box, score=0.5) for box in boxes]
    detections += [Detection(box=Box(x=400, y=300, w=44, h=100), score=0.5),
                   Detection(box=Box(x=400, y=700, w=100, h=44), score=0.5)]
    found = segment.columns_of(detections, Image.fromarray(page).convert("RGB"))
    alone = {column.work.line_id: column for column in found if column.work.line_id.startswith("alone-")}
    assert sorted(alone) == ["alone-6", "alone-7"]
    assert alone["alone-6"].work.frame.vertical and not alone["alone-7"].work.frame.vertical
    for column in found:
        for span in column.work.spans:
            span.features = {"p": 0.9 if span.kind == "part" or len(column.members) > 1 else 0.2}
    pieces = segment.pieces_of(found, Framing())
    stacked = sorted((piece.box.y, piece.box.h) for piece in pieces if piece.detections == (6,))
    beside = sorted((piece.box.x, piece.box.w) for piece in pieces if piece.detections == (7,))
    assert len(stacked) == 2 and stacked[0][0] + stacked[0][1] <= 356 <= stacked[1][0] + 4
    assert len(beside) == 2 and beside[0][0] + beside[0][1] <= 456 <= beside[1][0] + 4
    assert sum(piece.kind == "box" for piece in pieces) == 6


def test_a_detection_held_by_another_stays_with_its_holder():
    pytest.importorskip("cv2")
    from PIL import Image

    class Whole:
        def probabilities(self, rows):
            return np.array([0.9 if row["kind_box"] else 0.2 for row in rows])

    page = np.full((1000, 400), 235, dtype=np.uint8)
    boxes = [Box(x=100, y=100 + 120 * row, w=100, h=100) for row in range(5)]
    for box in boxes:
        page[box.y + 4:box.y + box.h - 4, box.x + 4:box.x + box.w - 4] = 20
    detections = [Detection(box=box, score=0.5) for box in boxes]
    detections.append(Detection(box=Box(x=100, y=340, w=100, h=50), score=0.4))  # the top half of the third
    found = segment.columns_of(detections, Image.fromarray(page).convert("RGB"))
    assert not any(column.work.line_id == "alone-5" for column in found), "a held box is not read alone"
    for column in found:
        for span in column.work.spans:
            span.features = {"kind_box": float(span.kind == "box")}
    pieces = segment.pieces_of(found, Whole())
    assert [piece.detections for piece in pieces] == [(0,), (1,), (2, 5), (3,), (4,)]
