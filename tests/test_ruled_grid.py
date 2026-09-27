"""Character boxes from a page ruled in columns, on synthetic pages."""

import numpy as np
from PIL import Image, ImageDraw

from glyph_atlas import ruled_grid

PAPER, INK = 200, 30


def page(columns: list[str], *, n_cols: int = 7, rows: int = 11, blank_top: dict[int, int] | None = None):
    """A 1280x1811 page: a thick frame, thin column rules, and a stroked box for every character, the
    columns right to left, each box shifted a little as a carved character is. `blank_top` leaves cells
    empty at the top of a column (an indent)."""
    rng = np.random.default_rng(0)
    im = Image.new("L", (1280, 1811), PAPER)
    d = ImageDraw.Draw(im)
    x0, x1, y0, y1 = 160, 1110, 275, 1580
    d.rectangle([x0 - 20, y0 - 20, x1 + 20, y1 + 20], outline=INK, width=14)
    width, pitch = (x1 - x0) / n_cols, (y1 - y0) / rows
    for k in range(1, n_cols):
        d.line([x0 + k * width, y0, x0 + k * width, y1], fill=160, width=2)
    for c, text in enumerate(columns):
        left = x1 - (c + 1) * width
        start = (blank_top or {}).get(c, 0)
        for r, _ in enumerate(text):
            top = y0 + (start + r) * pitch
            dx = rng.uniform(-12, 12)
            d.rectangle([left + width * .22 + dx, top + pitch * .15, left + width * .68 + dx, top + pitch * .85],
                        outline=INK, width=7)
    return np.asarray(im, dtype=np.float32)


def test_characters_drop_markup_and_editorial_punctuation():
    text = "{{여백|2em}}並書。如虯字，{{*|臣}}<section begin=\"x\"/>\n=== 中聲解 ===\n{{nop}}"
    assert ruled_grid.characters(text) == list("並書如虯字臣中聲解")


def test_characters_keep_a_syllable_with_its_tone_mark_in_one_cell():
    assert ruled_grid.characters("如믈〮為ᄆᆡ〮") == ["如", "믈〮", "為", "ᄆᆡ〮"]


def test_a_full_page_is_cut_into_its_characters_in_reading_order():
    columns = ["並書如虯字初彂聲", "牙音如快字初彂聲穰", "舌音如斗字"] + ["初彂聲"] * 4
    text = "\n\n".join(columns)
    cut, why = ruled_grid.page_boxes(page(columns), text)
    assert why == "cut"
    assert [label for _, label, _, _ in cut.boxes] == ruled_grid.characters(text)
    first = cut.boxes[0][0]
    assert first.x > 900  # the first column is the rightmost
    tops = [b.y for b, _, column, _ in cut.boxes if column == 1]
    assert tops == sorted(tops) and len(tops) == 9


def test_an_indented_column_starts_at_its_first_character():
    columns = ["並書如虯"] + ["初彂聲"] * 6
    cut, _ = ruled_grid.page_boxes(page(columns, blank_top={0: 2}), "".join(columns))
    pitch = (1580 - 275) / 11
    assert cut.boxes[0][0].y > 275 + 1.8 * pitch


def test_a_page_whose_text_does_not_fill_its_cells_is_left_out():
    columns = ["並書如虯字"] * 7
    cut, why = ruled_grid.page_boxes(page(columns), "並書如虯" * 7)
    assert cut is None and why == "35 inked cells, 28 characters"


def test_eight_columns_of_thirteen():
    columns = ["理而已理旣不二則何得不與天"] * 8
    cut, why = ruled_grid.page_boxes(page(columns, n_cols=8, rows=13), "".join(columns))
    assert why == "cut" and cut.layout == "8x13" and len(cut.boxes) == 104


def test_only_han_counts_as_han():
    assert ruled_grid.is_han("並") and ruled_grid.is_han("𠕅")
    assert not ruled_grid.is_han("ㄱ") and not ruled_grid.is_han("믈〮")
