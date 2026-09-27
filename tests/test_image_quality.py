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
    pixels = np.asarray(glyph().convert("L"), dtype=np.float32) + rng.normal(0, 40, (60, 60))
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


def test_a_blank_crop_is_not_blurry():
    assert "blurry" not in tags(measure(Image.new("L", (40, 40), 230)))


def test_a_transparent_crop_is_measured_on_white():
    image = Image.new("RGBA", (40, 40), (200, 30, 30, 0))
    ImageDraw.Draw(image).rectangle((10, 5, 20, 35), fill=(0, 0, 0, 255))
    assert measure(image)["chroma"] < 1


def page_image(tmp_path):
    image = Image.new("RGB", (100, 80), (200, 200, 200))
    ImageDraw.Draw(image).rectangle((20, 10, 30, 60), fill=(40, 40, 40))
    path = tmp_path / "page.png"
    image.save(path)
    return path


def test_the_index_measures_each_crop_once_and_again_when_its_box_moves(tmp_path):
    from glyph_atlas.image_quality import connect, index_units, lookup
    from glyph_atlas.schema import Page, Unit

    path = page_image(tmp_path)
    page = Page(id="p:0", document_id="p", seq=0, image="https://example.org/p.jpg", width=100, height=80,
                sha256="a" * 64)
    units = [Unit(id="u1", page_id="p:0", box=Box(x=15, y=5, w=20, h=60)),
             Unit(id="u2", page_id="p:0", box=Box(x=90, y=70, w=20, h=20)),
             Unit(id="u3", page_id="p:0")]
    db = connect(tmp_path / "index.sqlite")
    assert index_units(db, units, {"p:0": page}, lambda url: path) == {"measured": 1, "box-outside-page": 1}
    assert index_units(db, units[:1], {"p:0": page}, lambda url: path) == {}
    assert lookup(db)["u1"]["method"] == METHOD
    moved = units[0].model_copy(update={"box": Box(x=16, y=5, w=20, h=60)})
    assert index_units(db, [moved], {"p:0": page}, lambda url: path) == {"measured": 1}


def test_a_page_missing_from_the_cache_or_of_another_size_is_skipped(tmp_path):
    from glyph_atlas.image_quality import connect, index_units
    from glyph_atlas.schema import Page, Unit

    path = page_image(tmp_path)
    unit = Unit(id="u1", page_id="p:0", box=Box(x=15, y=5, w=20, h=60))
    page = Page(id="p:0", document_id="p", seq=0, image="https://example.org/p.jpg", width=100, height=80,
                sha256="a" * 64)
    db = connect(tmp_path / "index.sqlite")
    assert index_units(db, [unit], {"p:0": page}, lambda url: None) == {"page-not-cached": 1}
    scaled = page.model_copy(update={"width": 200, "height": 160})
    assert index_units(db, [unit], {"p:0": scaled}, lambda url: path) == {"page-size-differs": 1}


def test_a_high_resolution_brush_edge_is_not_blurry():
    """A sharp edge spread over a few pixels of a large scan is sharp at the scale of the glyph."""
    big = glyph(size=60).resize((300, 300), Image.Resampling.BICUBIC)
    assert "blurry" not in tags(measure(big))


def test_scan_tags_come_from_the_page_median():
    from glyph_atlas.image_quality import page_summary
    clean, rough = measure(glyph()), {**measure(glyph()), "noise": 0.2}
    assert "noisy" not in tags(page_summary([clean, clean, rough]))
    assert "noisy" in tags(page_summary([rough, rough, clean]))
    assert page_summary([{"method": "other"}]) is None
