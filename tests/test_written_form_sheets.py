import gzip
import json

from glyph_atlas.review.written_form_sheets import PAGE_BITS, choose, keys, render


def _rows(label, other, book, scores):
    return [(label, other, book, f"{label}{book}-{n}", score) for n, score in enumerate(scores)]


def test_books_come_furthest_leaning_first_and_a_lone_book_makes_no_section():
    rows = (_rows("時", "时", 1, [-0.2] * 5) + _rows("時", "时", 2, [0.1, 0.4, 0.2, 0.1, 0.3])
            + _rows("時", "时", 3, [0.9] * 4)          # too few crops to judge the book by
            + _rows("国", "國", 1, [0.5] * 6))          # one book only
    found = choose(rows, per_book=2)
    assert [(s["label"], s["other"]) for s in found] == [("時", "时")]
    assert [b["book"] for b in found[0]["books"]] == [2, 1]
    assert found[0]["books"][0]["ids"] == ["時2-1", "時2-4"] and found[0]["books"][0]["crops"] == 5


def test_sections_are_ordered_by_their_furthest_leaning_book():
    rows = (_rows("時", "时", 1, [0.1] * 5) + _rows("時", "时", 2, [0.0] * 5)
            + _rows("島", "嶋", 1, [0.4] * 5) + _rows("島", "嶋", 2, [-0.9] * 5))
    assert [s["label"] for s in choose(rows)] == ["島", "時"]
    assert [s["label"] for s in choose(rows, sections=1)] == ["島"]


def test_a_crops_key_comes_from_the_newest_bundle_of_its_page(tmp_path):
    def bundle(rev, crops):
        with gzip.open(tmp_path / f"7-{rev}.json.gz", "wt", encoding="utf-8") as handle:
            json.dump({"crops": crops}, handle)

    bundle(2, [{"slot": 3, "image": "/atlas/media/old.webp"}])
    bundle(10, [{"slot": "3", "image": "/atlas/media/new.webp"}, {"slot": 4, "record_place": ["packs/p.bin", 0, 9]}])
    crops = {"a": (7 << PAGE_BITS) | 3, "b": (7 << PAGE_BITS) | 4, "c": (8 << PAGE_BITS) | 1}
    assert keys(crops, tmp_path) == {"a": "new"}


def test_the_page_shows_a_row_for_each_book_with_pictures(tmp_path):
    picture = tmp_path / "a.webp"
    picture.write_bytes(b"RIFF")
    sections = [{"label": "時", "other": "时", "books": [
        {"book": 1, "mean": 0.16, "crops": 77, "ids": ["a"]}, {"book": 2, "mean": -0.2, "crops": 9, "ids": ["a", "gone"]}]}]
    page, drawn = render(sections, {1: "北海随筆 <1>"}, {"a": picture})
    assert drawn == 1 and render(sections, {}, {}) == (render([], {}, {})[0], 0)
    assert page.count('<div class="book">') == 2 and page.count("<img") == 2
    assert "北海随筆 &lt;1&gt;" in page and "+0.16" in page and ">77<" in page and "時 → 时" in page


def test_a_character_in_many_books_shows_both_ends():
    rows = [row for book in range(30) for row in _rows("時", "时", book, [book / 100] * 5)]
    books = [b["book"] for b in choose(rows)[0]["books"]]
    assert books == [29, 28, 27, 26, 25, 24, 23, 22, 3, 2, 1, 0]
