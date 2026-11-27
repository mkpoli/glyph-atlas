import sqlite3

import numpy as np

from glyph_atlas.review.agreed_marks import facts, forms, measure, numbers, verdicts


def _unit(*rows):
    vectors = np.array(rows, dtype=np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


# Rows 0 to 3 are written one way and rows 4 to 6 another; all seven are one 字種.
VECTORS = _unit([1, 0], [1, 0.1], [1, 0.2], [1, 0.3], [0, 1], [0.1, 1], [0.2, 1])
FAMILY = ["な"] * 7


def test_a_crop_among_its_own_form_is_agreed_and_one_among_another_form_is_flagged_with_it():
    labels = ["な", "な", "な", "𛂂", "𛂂", "𛂂", "𛂂"]
    found = forms(labels, FAMILY, [1, 2, 3, 4, 5, 6, 7], VECTORS, k=2)
    assert found[0] == (2, None)      # rows 1 and 2 carry な
    assert found[3] == (0, "な")      # filed 𛂂, written like the な crops
    assert found[4] == (2, None)
    assert found[2] == (1, None)      # rows 1 and 3 disagree: left to a reviewer


def test_crops_of_the_same_book_and_twins_do_not_judge_a_crop():
    labels = ["な", "な", "な", "𛂂", "𛂂", "𛂂", "𛂂"]
    # Rows 1 and 2 share row 0's book, so row 0 is judged by rows 3 and then the far ones.
    assert forms(labels, FAMILY, [1, 1, 1, 4, 5, 6, 7], VECTORS, k=2, rows=[0])[0] == (0, "𛂂")
    twins = _unit([1, 0], [1, 0], [1, 0.1], [1, 0.2], [0, 1])
    found = forms(["な", "𛂂", "な", "な", "𛂂"], ["な"] * 5, [1, 2, 3, 4, 5], twins, k=2, rows=[0])
    assert found[0] == (2, None)      # row 1 is the same image filed twice


def test_a_crop_short_of_neighbours_or_of_another_family_counts_none():
    labels = ["な", "な", "な", "に"]
    vectors = _unit([1, 0], [1, 0.1], [1, 0.2], [1, 0.05])
    found = forms(labels, ["な", "な", "な", "に"], [1, 2, 3, 4], vectors, k=2)
    assert found[0] == (2, None) and found[3] == (0, None)
    assert forms(labels[:3], ["な"] * 3, [1, 1, 3], vectors[:3], k=2)[0] == (0, None)


def test_a_row_is_judged_under_the_label_asked():
    labels = ["𛂂", "な", "な", "な", "𛂂", "𛂂", "𛂂"]
    found = forms(labels, FAMILY, [1, 2, 3, 4, 5, 6, 7], VECTORS, k=2, rows=[0], asked={0: "な"})
    assert found[0] == (2, None)
    assert forms(labels, FAMILY, [1, 2, 3, 4, 5, 6, 7], VECTORS, k=2, rows=[0])[0] == (0, "な")


def _atlas(path, crops, events=()):
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE graphemes(id INTEGER PRIMARY KEY, text TEXT, head INTEGER);
        CREATE TABLE crops(id INTEGER PRIMARY KEY, char INTEGER, book INTEGER, kind INTEGER, state INTEGER, cut INTEGER);
        CREATE TABLE events(id INTEGER PRIMARY KEY, crop INTEGER, at INTEGER, kind INTEGER, issue INTEGER, label INTEGER);
        INSERT INTO graphemes VALUES (1, 'な', NULL), (2, '𛂂', 1), (3, 'に', NULL), (4, '\uFA38', 5), (5, '器', NULL);""")
    db.executemany("INSERT INTO crops VALUES (?, ?, ?, ?, ?, ?)", crops)
    db.executemany("INSERT INTO events(crop, at, kind, issue, label) VALUES (?, ?, ?, ?, ?)", events)
    db.commit()
    db.close()


def test_facts_and_numbers_read_only_the_crops_of_the_index(tmp_path):
    mirror = sqlite3.connect(tmp_path / "mirror.sqlite")
    mirror.execute("CREATE TABLE ids(text TEXT PRIMARY KEY, id INTEGER)")
    mirror.executemany("INSERT INTO ids VALUES (?, ?)", [("a", 10), ("b", 11), ("z", 99)])
    mirror.commit()
    assert numbers(["a", "missing", "b"], tmp_path / "mirror.sqlite", chunk=1) == [10, None, 11]
    _atlas(tmp_path / "atlas.sqlite", [(10, 2, 5, 0, 0, 0), (11, 4, 6, 0, 1, 2), (99, 1, 7, 1, 0, 0)])
    # A compatibility ideograph keeps its own text, apart from the unified character it folds to.
    assert facts([10, None, 11], tmp_path / "atlas.sqlite", chunk=1) == {
        0: ("𛂂", "な", 5, 0, 0, 0), 2: ("\uFA38", "器", 6, 0, 1, 2)}


def test_a_verdict_is_a_confirmation_or_a_named_character_and_never_a_crop_only_shown(tmp_path):
    crops = [(n, 1, 1, 0, 3, 0) for n in (10, 11, 12, 13)] + [(14, 1, 1, 1, 0, 0)]
    events = [(10, 1, 0, None, 1), (11, 1, 0, None, 1), (11, 2, 1, 0, 2),  # confirmed; confirmed, then named 𛂂
              (12, 1, 3, None, None),                                    # only shown
              (13, 1, 1, 2, None), (14, 1, 0, None, 1)]                  # a bad cut; a corpus glyph
    _atlas(tmp_path / "atlas.sqlite", crops, events)
    assert verdicts([10, 11, 12, 13, 14], tmp_path / "atlas.sqlite") == {0: ("kept", "な"), 1: ("changed", "𛂂")}


def test_measure_judges_a_changed_crop_under_the_label_of_the_index():
    # Row 0 was な and a reviewer named 𛂂; row 4 is confirmed 𛂂; row 3 was な and was named に, another 字種.
    labels = ["𛂂", "な", "な", "に", "𛂂", "𛂂", "𛂂"]
    families = ["な", "な", "な", "に", "な", "な", "な"]
    was = ["な", "な", "な", "な", "𛂂", "𛂂", "𛂂"]
    judged = {0: ("changed", "𛂂"), 3: ("changed", "に"), 4: ("kept", "𛂂")}
    result = measure(was, judged, labels, families, [n + 1 for n in range(7)], VECTORS, k=2)
    assert result == {"kept": {"agreed": 1, "named": 0}, "form": {"agreed": 1, "named": 0}}
