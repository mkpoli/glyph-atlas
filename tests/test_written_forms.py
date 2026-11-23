import numpy as np

from glyph_atlas.review import written_forms as wf
from glyph_atlas.review.written_forms import pairs, scores


def test_pairs_need_both_characters_held_and_enough_tables():
    counts = {"国": 40, "國": 20, "島": 40, "嶋": 3, "禁": 50}
    found = pairs(counts)
    assert ("国", "國") in found and len(found["国", "國"]) >= wf.SOURCES
    assert not any("嶋" in pair or "禁" in pair for pair in found)


def _crops(centre, count, rng):
    points = rng.normal(centre, 0.05, size=(count, 2)).astype(np.float32)
    return points / np.linalg.norm(points, axis=1, keepdims=True)


def test_a_crop_written_as_the_other_character_scores_above_its_books_other_crops():
    rng = np.random.default_rng(0)
    first, second = _crops([1, 0], 30, rng), _crops([0, 1], 30, rng)
    # The last crop filed under the first character is written as the second.
    first[-1] = second[0]
    vectors = np.concatenate([first, second])
    own = [True] * 30 + [False] * 30
    books = [n % 3 for n in range(60)]
    found = scores(vectors, own, books)
    assert found[29] > 0 and (found[:29] < 0).all() and (found[30:] < 0).all()
    assert int(np.argmax(found)) == 29


def test_a_book_is_scored_without_its_own_crops():
    rng = np.random.default_rng(1)
    # Book 9 holds every crop of the second character but four: left out, too few remain.
    vectors = np.concatenate([_crops([1, 0], 20, rng), _crops([0, 1], 12, rng)])
    own = [True] * 20 + [False] * 12
    books = [n % 2 for n in range(20)] + [9] * 8 + [0, 0, 1, 1]
    found = scores(vectors, own, books)
    assert np.isnan(found[20:28]).all() and not np.isnan(found[:20]).any() and not np.isnan(found[28:]).any()
