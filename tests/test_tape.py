"""Tests for placing headwords along the columns of a half-leaf read as one tape."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from glyph_atlas import longkan, tape
from glyph_atlas.schema import Box
from glyph_atlas.tape import Slot

HEAD, ROW, HEIGHT = 140.0, 80.0, 2000.0


def printed(slots, per_column=HEIGHT, drop=(), extra=()):
    """A page's candidates where each slot stands where its length says, columns `per_column` high."""
    place, out = 0.0, []
    for k, slot in enumerate(slots):
        if k not in drop:
            out.append((place, int(place // per_column), Box(x=0, y=int(place % per_column), w=130, h=130)))
        place += HEAD + np.ceil(slot.gloss / 2) * ROW
    out += [(p, 0, Box(x=0, y=0, w=130, h=130)) for p in extra]
    return SimpleNamespace(candidates=sorted(out, key=lambda c: c[0]))


def alike(slots, page, right):
    """Similarities high for each slot's own box (`right`: {slot: box}) and low for the rest."""
    out = np.full((len(slots), len(page.candidates)), 0.2)
    for k, j in right.items():
        out[k, j] = 0.9
    return out


SLOTS = [Slot(c, g, n) for n, (c, g) in enumerate([("一", 6), ("二", 10), ("三", 4), ("四", 8), ("五", 12), ("六", 2)], 1)]


def test_a_page_is_paired_in_order_by_the_text_alone():
    page = printed(SLOTS)
    pairs, cost = tape.solve(SLOTS, page, np.full((6, 6), np.nan), HEAD, ROW)
    assert pairs == {k: k for k in range(6)}
    assert cost == pytest.approx(0, abs=0.1)


def test_a_missing_headword_is_passed_over_and_a_false_box_left_alone():
    page = printed(SLOTS, drop=(2,), extra=(640.0,))
    pairs, _ = tape.solve(SLOTS, page, np.full((6, 6), np.nan), HEAD, ROW)
    assert 2 not in pairs and len(pairs) >= 4
    assert page.candidates.index(next(c for c in page.candidates if c[0] == 640.0)) not in pairs.values()


def test_shape_anchors_hold_the_order_where_places_do_not():
    page = printed(SLOTS)
    page.candidates = [(p + 180 * (k >= 3), c, b) for k, (p, c, b) in enumerate(page.candidates)]
    right = alike(SLOTS, page, {k: k for k in range(6)})
    assert tape.solve(SLOTS, page, right, HEAD, ROW)[0] == {k: k for k in range(6)}
    chance = tape.posterior(SLOTS, page, right, HEAD, ROW)
    assert np.diag(chance).min() > 0.9
    assert (chance.sum(1) <= 1.0 + 1e-6).all(), "a slot's pairs sum to at most one"


def test_a_pair_the_page_leaves_open_has_no_high_probability():
    slots = [Slot("一", 4, 1), Slot("二", 4, 2)]
    page = SimpleNamespace(candidates=[(0.0, 0, Box(x=0, y=0, w=1, h=1)), (220.0, 0, Box(x=0, y=220, w=1, h=1)), (225.0, 0, Box(x=0, y=225, w=1, h=1))])
    chance = tape.posterior(slots, page, np.full((2, 3), np.nan), 140.0, 40.0)
    assert chance[1].max() < 0.9


def test_a_half_leafs_columns_and_split_headwords_come_out():
    """Ten columns of gloss in two half-columns, a headword across one column as two boxes side by side."""
    boxes, pitch = [], 90
    for column in range(10):
        left = 300 + column * 2 * pitch
        for row in range(6, 25):
            for part in range(2):
                boxes.append(Box(x=left + part * pitch - 35, y=row * 82, w=70, h=76))
        boxes += [Box(x=left - 40 - 0, y=40, w=75, h=130), Box(x=left + 45, y=45, w=75, h=125)]
    for column in (1, 4, 7):
        boxes.append(Box(x=300 + column * 2 * pitch - 22, y=300, w=140, h=140))
    page = tape.half(boxes, 80, 95)
    assert page is not None and page.pitch == pytest.approx(pitch, abs=1) and len(page.columns) == 10
    whole = [b for _, _, b in page.candidates if b.y < 60]
    assert whole and all(b.w > 1.2 * pitch for b in whole), "the two parts are one box"


def test_a_crop_is_taken_to_its_columns_ink():
    image = Image.new("L", (400, 400), 245)
    draw = ImageDraw.Draw(image)
    draw.rectangle((150, 140, 240, 260), fill=10)  # the glyph
    page = SimpleNamespace(candidates=[(0.0, 0, Box(x=170, y=150, w=40, h=100))], pitch=90.0, middle=lambda c, y: 195.0)
    (_, _, box), = tape.recut(image, page)
    assert (box.x, box.y, box.w, box.h) == (150, 140, 91, 121)


def test_the_tables_fields_are_the_headwords_printed():
    entry = lambda field: longkan.Entry(1, field, "", "1", "金", "平聲", 9)
    assert longkan.fields(entry("鋝鉾鍒")) == ["鋝", "鉾", "鍒"]
    assert longkan.fields(entry("鈗俗鈂")) == ["鈗", "鈂"]
    assert longkan.fields(entry("俗")) == ["俗"], "a note is a headword where it is the whole field"
    assert longkan.fields(entry("")) == [None]
    assert longkan.fields(entry("⿰金属")) == ["⿰金属"]
    assert longkan.length("音容。〻，大鍾也。二。") == 7


def test_a_section_or_a_tone_takes_a_place_of_its_own():
    rows = [longkan.Entry(1, "鈂", "音容也。", "1", "金", "平聲", 9), longkan.Entry(2, "鋼", "", "1", "金", "上聲", 9), longkan.Entry(3, "人", "", "1", "人", "上聲", 9)]
    assert [(s.char, s.entry) for s in longkan.slots(rows)] == [("鈂", 1), (None, -1), ("鋼", 2), (None, -1), ("人", 3)]


def test_a_page_with_nothing_placed_has_no_certain_pair():
    slots = [Slot("一", 0, 1)]
    page = SimpleNamespace(candidates=[(0.0, 0, Box(x=0, y=0, w=1, h=1))])
    assert tape.posterior(slots, page, np.full((1, 1), np.nan), 140.0, 40.0)[0, 0] == pytest.approx(1 / (1 + np.exp(-1)))


def test_a_column_with_few_boxes_keeps_its_length_on_the_tape():
    boxes, pitch = [], 90
    for column in range(10):
        left = 300 + column * 2 * pitch
        rows = range(6, 25) if column != 5 else range(6, 7)
        boxes += [Box(x=left + part * pitch - 35, y=row * 82, w=70, h=76) for row in rows for part in range(2)]
        boxes.append(Box(x=left - 20, y=300, w=140, h=140))
    page = tape.half(boxes, 80, 95)
    heights = {c: place for place, c, _ in page.candidates}
    assert max(page.columns) - min(page.columns) == 9
    assert heights[page.columns[-1]] > 8 * (page.bottom - page.top) * 0.9, "the last column's headword is nine columns along"


def test_headwords_that_would_overlap_are_divided_at_the_blank_between_them():
    image = Image.new("L", (400, 600), 245)
    draw = ImageDraw.Draw(image)
    draw.rectangle((150, 100, 240, 190), fill=10)
    draw.rectangle((150, 205, 240, 300), fill=10)  # the next headword, 14 px below
    page = SimpleNamespace(candidates=[(0.0, 0, Box(x=160, y=110, w=60, h=110)), (100.0, 0, Box(x=160, y=200, w=60, h=90))],
                           pitch=90.0, middle=lambda c, y: 195.0)
    (_, _, upper), (_, _, lower) = tape.recut(image, page)
    assert upper.y + upper.h <= lower.y, "they no longer overlap"
    assert upper.y + upper.h <= 205 and lower.y >= 191
    other = [(0.0, 0, Box(x=160, y=110, w=60, h=50)), (9.0, 1, Box(x=160, y=130, w=60, h=50))]
    assert tape.apart(other, np.zeros((600, 400), bool), page) == other, "different columns are left alone"


def test_a_part_of_a_headword_is_dropped_and_not_cut_off_from_it():
    page = SimpleNamespace(pitch=90.0, middle=lambda c, y: 195.0)
    whole = (0.0, 0, Box(x=150, y=100, w=90, h=180))
    part = (90.0, 0, Box(x=150, y=190, w=90, h=90))
    other = (400.0, 1, Box(x=150, y=190, w=90, h=90))
    assert tape.apart([whole, part, other], np.zeros((600, 400), bool), page) == [whole, other]
