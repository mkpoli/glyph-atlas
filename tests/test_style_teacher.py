import importlib.util
import sys
from pathlib import Path

import numpy as np
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


def test_whole_calligraphers_are_held_out():
    rows = [{"writer": f"w{i}", "script": script, "style": train.STYLES[script]}
            for script, n in (("行", 13), ("隶", 4)) for i in range(n)]
    parts = train.split(rows)
    assert parts == train.split(list(reversed(rows)))
    by_style = {}
    for name, part in parts.items():
        by_style.setdefault(name.rsplit("-", 1)[1], []).append(part)
    assert sorted(by_style["行"]).count("test") == 3 and by_style["行"].count("val") == 1
    assert sorted(by_style["隶"]) == ["test", "train", "train", "val"]
