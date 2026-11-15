"""Tests for telling ink from other pigment and for the boxes that hold ink, on drawn pages."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from PIL import Image, ImageDraw

from glyph_atlas import pigment
from glyph_atlas.schema import Box

PAPER, INK, RED = (236, 228, 212), (48, 44, 40), (214, 96, 62)


def page(strokes: list[tuple[tuple[int, int, int, int], tuple[int, int, int]]]) -> Image.Image:
    image = Image.new("RGB", (600, 400), PAPER)
    draw = ImageDraw.Draw(image)
    # Writing far from the tests' boxes, so the darkest hundredth of the page is ink.
    for row in range(8):
        draw.rectangle((500, 20 + 45 * row, 560, 50 + 45 * row), fill=INK)
    # A stroke is the outline of its rectangle, 8 px thick: one connected piece that leaves its box mostly paper.
    for box, colour in strokes:
        draw.rectangle(box, outline=colour, width=8)
    return image


def test_ink_and_red_pigment_are_told_apart() -> None:
    found = pigment.pigments(page([((100, 100, 140, 108), INK), ((100, 200, 140, 208), RED)]))
    assert found.ink[104, 120] and not found.other[104, 120]
    assert found.other[204, 120] and not found.ink[204, 120]
    assert not found.ink[300, 300] and not found.other[300, 300], "paper is neither"


def test_a_box_on_red_pigment_alone_holds_no_ink() -> None:
    image = page([((100, 100, 150, 150), INK), ((100, 200, 150, 250), RED), ((250, 100, 300, 150), INK),
                  ((250, 90, 300, 96), RED)])
    boxes = [Box(x=98, y=98, w=56, h=56), Box(x=98, y=198, w=56, h=56), Box(x=248, y=88, w=56, h=66),
             Box(x=380, y=300, w=40, h=40), Box(x=-20, y=98, w=60, h=56), Box(x=92, y=160, w=60, h=0)]
    assert pigment.inked(image, boxes) == [True, False, True, False, False, False], \
        "ink beside red holds ink; red alone, bare paper, a box off the ink and a box of no area do not"
    assert pigment.inked(image, []) == []
    assert pigment.inked(Image.new("RGB", (200, 200), PAPER), boxes[:2]) == [True, True], "a page with no ink keeps its boxes"


def stamped(seal: tuple[int, int, int, int]) -> Image.Image:
    """A character with a solid red seal over part of the page, the two multiplied as pigments are."""
    image = page([((100, 100, 150, 150), INK), ((300, 100, 350, 150), INK)])
    pixels = np.asarray(image, dtype=np.float32)
    left, top, right, bottom = seal
    pixels[top:bottom, left:right] *= np.asarray(RED, dtype=np.float32) / np.asarray(PAPER, dtype=np.float32)
    return Image.fromarray(pixels.clip(0, 255).astype(np.uint8))


def test_a_character_under_a_seal_still_shows_its_ink() -> None:
    image = stamped((90, 90, 160, 160))
    found = pigment.pigments(image)
    assert found.ink[104, 120], "a stroke under the seal"
    assert found.other[125, 125] and not found.ink[125, 125], "the seal where no stroke is"
    assert pigment.inked(image, [Box(x=98, y=98, w=56, h=56), Box(x=298, y=98, w=56, h=56),
                                 Box(x=92, y=120, w=6, h=30)]) == [True, True, False], \
        "the character under the seal and the one beside it hold ink; a box on the seal's edge does not"


def test_a_red_frame_on_a_page_of_little_writing_is_not_taken_for_the_ink() -> None:
    image = Image.new("RGB", (800, 800), PAPER)
    draw = ImageDraw.Draw(image)
    draw.rectangle((200, 200, 600, 600), outline=RED, width=10)
    draw.rectangle((375, 375, 425, 425), outline=INK, width=8)
    ink, _ = pigment.axes(pigment.density(image))
    assert ink @ np.full(3, 3 ** -0.5) > 0.95, "the ink's direction stays near grey"
    assert pigment.inked(image, [Box(x=372, y=372, w=58, h=58), Box(x=196, y=380, w=20, h=60)]) == [True, False]


def test_proposals_on_no_ink_are_dropped_when_asked(tmp_path) -> None:
    from glyph_atlas import character_proposals

    image = page([((100, 100, 150, 150), INK), ((100, 200, 150, 250), RED)])

    class Found:
        def boxes(self, image):
            return [(Box(x=98, y=98, w=56, h=56), 0.9), (Box(x=98, y=198, w=56, h=56), 0.8)]

    assert len(character_proposals.propose(image, Found())) == 2
    (kept,) = character_proposals.propose(image, Found(), ink=True)
    assert (kept.x, kept.y, kept.w, kept.h, kept.score) == (98, 98, 56, 56, 0.9)
    model = tmp_path / "detector.onnx"
    model.write_bytes(b"model")
    assert character_proposals.proposer(model, ink=True) != character_proposals.proposer(model)
