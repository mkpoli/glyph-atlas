import io
import re

from PIL import Image, ImageDraw

from glyph_atlas.tone import crop_tone, paper_tone

PAPER = (222, 207, 180)


def distance(hex_colour, rgb):
    values = [int(hex_colour[i:i + 2], 16) for i in (1, 3, 5)]
    return max(abs(a - b) for a, b in zip(values, rgb, strict=True))


def written(size=(90, 120), paper=PAPER, ink=(30, 25, 20)):
    """A crop of a character whose strokes run off its edges, as a tight crop's do."""
    image = Image.new("RGB", size, paper)
    draw = ImageDraw.Draw(image)
    w, h = size
    draw.rectangle((w * 0.4, 0, w * 0.55, h), fill=ink)  # a vertical stroke through the top and bottom
    draw.rectangle((0, h * 0.45, w, h * 0.55), fill=ink)  # a horizontal one through both sides
    draw.ellipse((w * 0.2, h * 0.2, w * 0.8, h * 0.8), outline=ink, width=6)
    return image


def test_the_tone_is_the_paper_not_the_ink():
    assert distance(paper_tone(written()), PAPER) <= 2


def test_a_rubbing_is_its_dark_ground():
    ground = (24, 22, 26)
    assert distance(paper_tone(written(paper=ground, ink=(235, 235, 230))), ground) <= 2


def test_the_tone_is_six_hex_digits():
    assert re.fullmatch(r"#[0-9a-f]{6}", paper_tone(written()))


def test_a_flat_crop_is_its_own_colour():
    assert paper_tone(Image.new("RGB", (40, 50), (1, 2, 3))) == "#010203"


def test_tiny_and_unusual_images():
    assert paper_tone(Image.new("RGB", (1, 1), (200, 100, 50))) == "#c86432"
    assert paper_tone(Image.new("L", (3, 7), 128)) == "#808080"
    assert distance(paper_tone(Image.new("RGBA", (30, 30), PAPER + (255,))), PAPER) == 0
    assert distance(paper_tone(written(size=(1200, 300))), PAPER) <= 2


def test_crop_tone_reads_a_file_with_its_size():
    buffer = io.BytesIO()
    written(size=(47, 51)).save(buffer, "WEBP", quality=90)
    buffer.seek(0)
    found = crop_tone(buffer)
    assert found["image_size"] == [47, 51]
    assert distance(found["tone"], PAPER) <= 4
