import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from glyph_atlas.image_quality import METHOD, measure, tags
from glyph_atlas.schema import Box


def glyph(ink=40, paper=200, size=60):
    """A grey scan of a stroke: a dark bar on light paper, with a one-pixel soft edge."""
    image = Image.new("L", (size, size), paper)
    ImageDraw.Draw(image).rectangle((20, 8, 34, 52), fill=ink)
    return image.filter(ImageFilter.BoxBlur(1)).convert("RGB")


def test_a_clean_grey_scan_carries_no_tag_but_greyscale():
    quality = measure(glyph())
    assert quality["method"] == METHOD
    assert tags(quality) == ["greyscale"]


def test_a_thresholded_scan_is_binary_and_not_blurry():
    thresholded = glyph(ink=0, paper=255).point(lambda v: 0 if v < 128 else 255)
    assert tags(measure(thresholded)) == ["binary", "greyscale"]


def test_a_blurred_scan_is_blurry():
    assert "blurry" in tags(measure(glyph().filter(ImageFilter.GaussianBlur(4))))


def test_speckled_paper_is_noisy():
    rng = np.random.default_rng(0)
    pixels = np.asarray(glyph().convert("L"), dtype=np.float32) + rng.normal(0, 25, (60, 60))
    noisy = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8)).convert("RGB")
    assert "noisy" in tags(measure(noisy))
    assert "noisy" not in tags(measure(glyph()))


def test_a_colour_scan_is_not_greyscale():
    image = Image.new("RGB", (60, 60), (214, 196, 160))
    ImageDraw.Draw(image).rectangle((20, 8, 34, 52), fill=(60, 50, 40))
    assert "greyscale" not in tags(measure(image))


def test_small_comes_from_the_box_and_an_unknown_method_gives_no_pixel_tag():
    assert tags(None, Box(x=0, y=0, w=15, h=30)) == ["small"]
    assert tags({"method": "other"}, Box(x=0, y=0, w=30, h=30)) == []


def test_a_one_pixel_line_is_measured_without_error():
    assert measure(Image.new("L", (1, 12), 128))["sharpness"] == 0
