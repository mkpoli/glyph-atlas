import gzip
import json

from glyph_atlas.review.written_form_sheets import choose, keys, render
from glyph_atlas.review.written_forms import PAGE_BITS


def _rows(label, other, book, scores):
    return [(label, other, book, f"{label}{book}-{n}", score) for n, score in enumerate(scores)]


def test_labels_and_books_with_most_crops_come_first_and_the_likest_crops():
    rows = _rows("時", "时", 1, [0.9]) + _rows("時", "时", 2, [0.81, 0.95, 0.9]) + _rows("座", "坐", 1, [0.9] * 5)
    found = choose(rows, per_book=2)
    assert [(s["label"], s["crops"]) for s in found] == [("座", 5), ("時", 4)]
    assert [(b["book"], b["crops"]) for b in found[1]["books"]] == [(2, 3), (1, 1)]
    assert found[1]["books"][0]["ids"] == ["時2-1", "時2-2"]


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
    sections = [{"label": "時", "other": "时", "crops": 78, "books": [
        {"book": 1, "crops": 77, "ids": ["a"]}, {"book": 2, "crops": 1, "ids": ["gone"]}]},
        {"label": "座", "other": "坐", "crops": 1, "books": [{"book": 1, "crops": 1, "ids": ["gone"]}]}]
    page = render(sections, {1: "北海随筆 <1>"}, {"a": picture})
    assert page.count('<div class="book">') == 1 and page.count("<img") == 1 and page.count("<section>") == 1
    assert "北海随筆 &lt;1&gt;" in page and ">77<" in page and "時 → 时" in page
