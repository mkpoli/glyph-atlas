import importlib.util
import random
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from glyph_atlas import style
from glyph_atlas.style_teacher import CLASSES, SIZE, binarise, prepare, tensor

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("style_train", ROOT / "models" / "style" / "train.py")
train = importlib.util.module_from_spec(_spec)
sys.modules["style_train"] = train
_spec.loader.exec_module(train)


def glyph(ink="black", paper="white"):
    image = Image.new("L", (80, 60), paper)
    ImageDraw.Draw(image).rectangle([30, 20, 40, 45], fill=ink)
    return image


def test_every_class_is_a_style():
    assert all(name in style.vocabulary() for name in CLASSES)


def test_ink_is_found_on_either_polarity():
    assert binarise(glyph()).sum() == binarise(glyph(ink="white", paper="black")).sum() == 11 * 26


def test_a_prepared_crop_is_dark_ink_cut_to_a_white_square():
    for image in (glyph(), glyph(ink="white", paper="black")):
        prepared = np.asarray(prepare(image))
        assert prepared.shape == (SIZE, SIZE)
        assert prepared[0, 0] == 255 and prepared[SIZE // 2, SIZE // 2] == 0
    assert tensor(prepare(glyph())).shape == (3, SIZE, SIZE)


def test_a_blank_crop_prepares_to_blank_paper():
    assert (np.asarray(prepare(Image.new("L", (20, 20), 200))) == 255).all()


def test_a_calligrapher_sits_in_one_split_and_every_script_in_all_three():
    rows = [{"writer": f"w{i}", "script": script, "style": train.STYLES[script]}
            for script, n in (("行", 13), ("草", 6), ("隶", 3)) for i in range(n)]
    rows += [{"writer": "w0", "script": "楷", "style": "regular"}, {"writer": "w1", "script": "楷", "style": "regular"},
             {"writer": "w2", "script": "楷", "style": "regular"}]
    parts = train.split(rows)
    assert parts == train.split(list(reversed(rows)))
    assert set(parts) == {row["writer"] for row in rows}
    for script in ("行", "草", "隶", "楷"):
        held = {parts[row["writer"]] for row in rows if row["script"] == script}
        assert held == {"train", "val", "test"}, script


def test_a_script_with_too_few_calligraphers_is_refused():
    rows = [{"writer": f"w{i}", "script": "隶", "style": "clerical"} for i in range(2)]
    with pytest.raises(ValueError, match="clerical has no calligrapher left for train"):
        train.split(rows)


def test_augmenting_keeps_the_canvas_and_the_paper():
    image = prepare(glyph())
    out = train.augment(image, random.Random(1))
    assert out.size == image.size and np.asarray(out)[0, 0] == 255


def test_a_bold_character_keeps_its_polarity():
    image = Image.new("L", (40, 40), "white")
    ImageDraw.Draw(image).rectangle([2, 2, 37, 37], fill="black")
    assert binarise(image).mean() > 0.5


def test_a_speck_is_not_part_of_the_character():
    image = glyph()
    ImageDraw.Draw(image).point((2, 2), fill="black")
    assert np.asarray(prepare(image)).shape == np.asarray(prepare(glyph())).shape
    assert (np.asarray(prepare(image)) == np.asarray(prepare(glyph()))).all()


def test_transparent_paper_is_white():
    image = Image.new("RGBA", (80, 60), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle([30, 20, 40, 45], fill=(0, 0, 0, 255))
    assert binarise(image).sum() == 11 * 26


def test_the_suggestion_sample_is_stable_and_nested():
    spec = importlib.util.spec_from_file_location("style_suggest", ROOT / "models" / "style" / "suggest.py")
    suggest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suggest)
    ids = [f"u{i}" for i in range(2000)]
    small = {i for i in ids if suggest.sampled(i, 0.05)}
    large = {i for i in ids if suggest.sampled(i, 0.2)}
    assert small < large and 40 < len(small) < 160


def test_a_document_proposal_needs_enough_crops_and_is_clear_only_above_the_line():
    spec = importlib.util.spec_from_file_location("style_propose", ROOT / "models" / "style" / "propose_documents.py")
    propose = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(propose)
    rows = ([{"document_id": "a", "style": "regular", "checkpoint_sha256": "x"}] * 9
            + [{"document_id": "a", "style": "running", "checkpoint_sha256": "x"}]
            + [{"document_id": "b", "style": s, "checkpoint_sha256": "x"} for s in ["running"] * 6 + ["cursive"] * 4]
            + [{"document_id": "c", "style": "regular", "checkpoint_sha256": "x"}] * 3)
    found = {doc["document_id"]: doc for doc in propose.proposals(rows, min_crops=10, clear=0.8)}
    assert set(found) == {"a", "b"}
    assert (found["a"]["style"], found["a"]["clear"]) == ("regular", True)
    assert (found["b"]["style"], found["b"]["clear"], found["b"]["counts"]) == ("running", False, {"running": 6, "cursive": 4})
