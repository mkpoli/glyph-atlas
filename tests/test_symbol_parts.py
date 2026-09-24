"""Printed symbols survive OCR suggestions without turning guesses into truth."""

from itertools import pairwise

import pytest
from PIL import Image, ImageDraw

from glyph_atlas.symbol_parts import recognize_parts, triangle_regions

# The marker detector is OpenCV's; without it no region is found, which is not what is under test.
pytest.importorskip("cv2")


def crop(*, shape="triangle", placement="after", touching_rows=False):
    image = Image.new("RGB", (40, 80), (228, 214, 181))
    draw = ImageDraw.Draw(image)
    draw.rectangle((6, 3, 27, 28), fill=(25, 22, 20))
    if touching_rows:
        draw.rectangle((5, 28, 9, 46), fill=(25, 22, 20))
    if shape == "triangle":
        draw.polygon([(4, 69), (20, 47), (36, 69)], fill=(25, 22, 20))
    elif shape == "slash":
        draw.polygon([(5, 69), (32, 47), (25, 62)], fill=(25, 22, 20))
    elif shape == "circle":
        draw.ellipse((4, 47, 36, 69), fill=(25, 22, 20))
    elif shape == "square":
        draw.rectangle((4, 47, 36, 69), fill=(25, 22, 20))
    if placement != "after":
        # Translate the same upright triangle above or between other ink.
        image = Image.new("RGB", (40, 120), (228, 214, 181))
        draw = ImageDraw.Draw(image)
        y = 5 if placement == "before" else 48
        draw.polygon([(4, y+22), (20, y), (36, y+22)], fill="black")
        draw.rectangle((6, 87, 27, 114), fill="black")
        if placement == "middle":
            draw.rectangle((6, 3, 27, 28), fill="black")
    return image


@pytest.mark.parametrize("placement,flags", [("after", [False, True]), ("before", [True, False]),
                                             ("middle", [False, True, False])])
def test_marker_position_and_original_pixels_are_preserved(placement, flags):
    image = crop(placement=placement)
    regions = triangle_regions(image)
    assert [r["symbol"] for r in regions] == flags
    assert sum(r["box"]["h"] for r in regions) == image.height
    assert all(r["box"]["w"] == image.width for r in regions)
    for left, right in pairwise(regions):
        assert left["box"]["y"] + left["box"]["h"] == right["box"]["y"]


def test_disconnected_components_need_not_have_a_blank_row():
    regions = triangle_regions(crop(touching_rows=True))
    assert len(regions) == 2
    assert regions[0]["box"]["h"] == 47


@pytest.mark.parametrize("shape", ["slash", "circle", "square"])
def test_other_shapes_do_not_trigger_triangle_ocr(shape):
    assert triangle_regions(crop(shape=shape)) == []


def test_one_symbol_and_uniform_paper_are_not_joins():
    image = crop().crop((0, 40, 40, 80))
    assert triangle_regions(image) == []
    assert triangle_regions(Image.new("RGB", (40, 80), "white")) == []


def answer(text, score=.999, classifier=None):
    ndl = {"text": text, "score": score, "engine": "NDLkotenOCR"}
    classifier = classifier or {"text": text, "score": .99, "engine": "Atlas classifier"}
    return {"candidates": [ndl], "votes": [ndl, classifier], "engines": []}


def test_separate_marker_suggestion_is_not_a_whole_crop_vote():
    from glyph_atlas.review.suggestions import Recognizer

    reader = Recognizer.__new__(Recognizer)
    results = iter([answer("シム"), answer("シ"), answer("▲")])
    reader._read_base = lambda _: next(results)
    result = reader.read(crop())
    assert result["candidates"][0]["text"] == "シ▲"
    assert result["candidates"][0]["verified"] is False
    assert [v["text"] for v in result["votes"]] == ["シム", "シム"]
    assert result["symbol_partition"]["accepted"]


def test_family_alternatives_remain_suggestions_and_do_not_confirm_exact_script():
    family = {"engine": "Atlas classifier", "score": .99, "text": None,
              "identity_scope": "family", "members": ["し", "𛁄", "シ"]}
    results = iter([answer("シ", .6, family), answer("▲")])
    partition = recognize_parts(crop(), lambda _: next(results))
    assert {c["text"] for c in partition["candidates"]} == {"し▲", "シ▲"}
    assert not partition["accepted"]


def test_marker_shape_without_recognition_does_not_inject_symbol():
    results = iter([answer("シ"), answer("ム")])
    assert recognize_parts(crop(), lambda _: next(results)) is None


def test_one_model_disagreement_prevents_symbol_confirmation():
    other = {"text": "ム", "score": .999, "engine": "Atlas classifier"}
    results = iter([answer("シ"), answer("▲", classifier=other)])
    assert recognize_parts(crop(), lambda _: next(results)) is None


def test_single_marker_vote_can_suggest_but_cannot_split():
    uncertain = {"text": None, "score": .2, "engine": "Atlas classifier"}
    results = iter([answer("シ"), answer("▲", classifier=uncertain)])
    partition = recognize_parts(crop(), lambda _: next(results))
    assert partition["candidates"][0]["text"] == "シ▲"
    assert not partition["accepted"]


@pytest.mark.parametrize("expected,accepted", [(None, True), ("シ▲", True), ("シム", False), ("シ", False)])
def test_split_never_discards_marker_to_fit_old_suggestion(expected, accepted):
    from types import SimpleNamespace

    from glyph_atlas.review.refine import SplitEngine

    results = iter([answer("シ"), answer("▲")])
    partition = recognize_parts(crop(), lambda _: next(results))
    model = SimpleNamespace(read=lambda _: {**answer("シム"), "symbol_partition": partition})
    result = SplitEngine(model).assess(crop(), expected, identity="U+30B7")
    assert result["accepted"] is accepted
    if accepted:
        assert result["text"] == ["シ", "▲"]
