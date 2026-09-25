"""The interlinear mark proposer on drawn pages: printed characters, glosses beside them, a circle.

A page is drawn with thick square characters in columns, thin glosses in the gaps between columns,
a break circle, and ink that no reviewer should be asked about: a mark far outside the text, a red seal
with a ring inside it.
"""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

pytest.importorskip("cv2")

from glyph_atlas import interlinear

SIZE = 120
COLUMNS = [300, 500, 700, 900, 1100, 1300]
ROWS = [150, 310, 470, 630, 790, 950]
SEAL = (COLUMNS[2], ROWS[3])
GLOSSES = [(COLUMNS[0] + 140, ROWS[1] + 60), (COLUMNS[3] + 140, ROWS[4] + 20), (COLUMNS[5] + 140, ROWS[2] + 70)]
CIRCLE = (COLUMNS[1] + 150, ROWS[2] + 105)
STRAY = (1550, 1320)


def character(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    """A printed character: a frame with a bar and a post, strokes 14 px wide."""
    draw.rectangle((x, y, x + SIZE, y + SIZE), outline="black", width=14)
    draw.rectangle((x, y + 53, x + SIZE, y + 67), fill="black")
    draw.rectangle((x + 53, y, x + 67, y + SIZE), fill="black")


def gloss(draw: ImageDraw.ImageDraw, x: int, y: int, fill: str = "black") -> None:
    """A gloss like ㅅ, 3 px strokes about a fifth of a character tall."""
    draw.line((x, y + 26, x + 12, y), fill=fill, width=3)
    draw.line((x + 12, y, x + 24, y + 26), fill=fill, width=3)


def ring(draw: ImageDraw.ImageDraw, x: int, y: int, fill: str = "black") -> None:
    draw.ellipse((x - 13, y - 13, x + 13, y + 13), outline=fill, width=3)


def page() -> Image.Image:
    image = Image.new("RGB", (1700, 1400), "white")
    draw = ImageDraw.Draw(image)
    for x in COLUMNS:
        for y in ROWS:
            if (x, y) != SEAL:
                character(draw, x, y)
    for x, y in GLOSSES:
        gloss(draw, x, y)
    ring(draw, *CIRCLE)
    gloss(draw, *STRAY)
    x, y = SEAL
    draw.rectangle((x, y, x + SIZE, y + SIZE), outline=(200, 30, 30), width=12)
    ring(draw, x + 60, y + 60, fill=(200, 30, 30))
    return image


def centre(box: tuple[int, int, int, int]) -> tuple[float, float]:
    x, y, w, h = box
    return x + w / 2, y + h / 2


def near(point: tuple[float, float], target: tuple[float, float], reach: float = 20) -> bool:
    return abs(point[0] - target[0]) <= reach and abs(point[1] - target[1]) <= reach


def test_the_glosses_and_the_circle_are_proposed_and_nothing_else() -> None:
    proposals = interlinear.propose(page())
    marks = [centre(p.box) for p in proposals if p.kind == "mark"]
    circles = [centre(p.box) for p in proposals if p.kind == "circle"]
    assert len(marks) == len(GLOSSES)
    for x, y in GLOSSES:
        assert any(near(mark, (x + 12, y + 13)) for mark in marks), (x, y, marks)
    assert len(circles) == 1 and near(circles[0], CIRCLE)
    assert all(0 < p.score <= 1 and p.features["unit"] == pytest.approx(SIZE, abs=4) for p in proposals)


def test_the_filters_say_why_the_rest_was_left_out() -> None:
    _, block, found = interlinear.survey(page())
    reasons = {}
    for candidate in found:
        reasons[candidate.get("dropped")] = reasons.get(candidate.get("dropped"), []) + [centre(candidate["box"])]
    assert any(near(point, (STRAY[0] + 12, STRAY[1] + 13)) for point in reasons["outside"])
    assert any(near(point, (SEAL[0] + 60, SEAL[1] + 60)) for point in reasons["seal"])
    x0, y0, x1, y1 = block
    assert x0 < COLUMNS[0] and y0 < ROWS[0] and x1 > COLUMNS[-1] + SIZE and y1 > ROWS[-1] + SIZE


def test_the_proposals_are_deterministic_and_ordered() -> None:
    first, second = interlinear.propose(page()), interlinear.propose(page())
    assert first == second
    assert [(p.y, p.x) for p in first] == sorted((p.y, p.x) for p in first)


def test_a_page_without_text_yields_nothing() -> None:
    blank = Image.new("RGB", (1200, 900), (236, 226, 200))
    draw = ImageDraw.Draw(blank)
    gloss(draw, 500, 400)
    assert interlinear.propose(blank) == []
    assert interlinear.propose(Image.new("RGB", (60, 60), "white")) == []


def test_characters_too_small_to_hold_a_mark_yield_nothing() -> None:
    assert interlinear.propose(page().resize((170, 140))) == []


def test_the_version_is_named() -> None:
    assert interlinear.PROPOSER.startswith("interlinear-")
