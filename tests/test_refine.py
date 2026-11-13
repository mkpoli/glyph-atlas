import numpy as np
import pytest

from glyph_atlas import refine
from glyph_atlas.schema import Box


def test_edges_move_by_shares_of_the_box():
    assert refine.moved((100, 100, 40, 80), (0.25, -0.5, 0, 0.25)) == (110, 60, 30, 140)
    assert refine.moved((100, 100, 40, 80), (0, 0, 5, 0))[2] == 40 + refine.REACH * 40, "one round reaches no further"
    assert refine.moved((100, 100, 40, 80), (0.75, 0, -0.75, 0)) == (100, 100, 40, 80), "a box is never emptied"


def test_the_box_lands_on_the_centre_of_its_window():
    pytest.importorskip("cv2")
    grey = np.full((300, 300), 200, dtype=np.uint8)
    grey[100:180, 120:160] = 0
    seen = refine.window(grey, (120, 100, 40, 80), 200)
    assert seen.shape == (refine.SIZE, refine.SIZE)
    edge = round(refine.SIZE * refine.MARGIN / (1 + 2 * refine.MARGIN))
    assert seen[edge + 4:-edge - 4, edge + 4:-edge - 4].max() < 30, "the box fills the centre whatever its shape"
    assert seen[:edge - 4].min() > 170 and seen[:, :edge - 4].min() > 170
    outside = refine.window(grey, (-30, -30, 40, 40), 200)
    assert outside[:10, :10].min() == 200, "beyond the page is paper"


def test_boxes_are_moved_round_by_round_and_kept_on_the_page():
    pytest.importorskip("cv2")

    class Session:
        """Asks every box to grow downward by a quarter, and expects a higher IoU each time it is asked."""

        calls = 0

        def run(self, _, feed):
            count = len(feed["pixels"])
            self.calls += 1
            return [np.tile(np.array([[0, 0, 0, .25]], dtype=np.float32), (count, 1)),
                    np.full(count, .5 + .1 * self.calls, dtype=np.float32)]

    refiner = refine.Refiner("unused.onnx", session=Session(), rounds=2)
    grey = np.full((200, 200), 255, dtype=np.uint8)
    (box, after, before), (low, _, _) = refiner.refine(grey, [Box(x=10, y=10, w=40, h=64), Box(x=10, y=150, w=40, h=40)])
    assert (box.x, box.y, box.w, box.h) == (10, 10, 40, 100)
    assert before == pytest.approx(.6) and after == pytest.approx(.8)
    assert low.y + low.h == 200
    assert refiner.refine(grey, []) == []
