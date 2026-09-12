"""OCR suggestions preserve sequence boundaries and upstream image preprocessing."""
import numpy as np
from PIL import Image

from glyph_atlas.review.suggestions import decode, preprocess


def test_ocr_stops_at_eos_and_keeps_repeated_characters():
    logits = np.full((1, 5, 4), -9.)
    for position, token in enumerate([1, 1, 2, 0, 3]):
        logits[0, position, token] = 9
    assert decode(logits, 'アカシ')[0]['text'] == 'アアカ'
    logits[0, 0] = [9, -9, -9, -9]
    assert decode(logits, 'アカシ') == []


def test_ocr_alternatives_do_not_repeat_the_best_candidate():
    logits = np.array([[[-8, 3, 2, 1], [5, -8, -8, -8]]], dtype=float)
    results = decode(logits, 'アカシ')
    assert [r['text'] for r in results] == ['ア', 'カ', 'シ']
    assert results[0]['score'] > results[1]['score']


def test_vertical_crop_rotation_and_channel_order():
    pixels = np.zeros((6, 2, 3), dtype=np.uint8)
    pixels[:3] = [255, 0, 0]
    pixels[3:] = [0, 0, 255]
    result = preprocess(Image.fromarray(pixels), (6, 2))
    assert result.shape == (1, 3, 2, 6) and result.dtype == np.float32
    assert np.all(result[0, :, :, :3] == np.array([-1, -1, 1])[:, None, None])
    assert np.all(result[0, :, :, 3:] == np.array([1, -1, -1])[:, None, None])
