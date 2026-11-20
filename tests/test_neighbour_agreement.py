import sqlite3

import numpy as np

from glyph_atlas.review import neighbour_agreement as na
from glyph_atlas.review.neighbour_agreement import evaluate, reviewed, streak, streaks


def test_a_streak_ends_at_the_first_neighbour_under_another_character():
    assert streak("ナ", ["な", "ナ", "レ", "ナ"]) == 2  # な is the same kana
    assert streak("者", ["は", "者"]) == 2              # 者 is written for は
    assert streak("禁", ["制", "禁"]) == 0
    assert streak("禁", ["禁", None, "禁"]) == 1
    assert streak(None, ["禁"]) == 0


def _unit(*rows):
    vectors = np.array(rows, dtype=np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def test_streaks_leave_out_the_page_and_stop_at_another_label():
    # Rows 0 to 3 lie close together and row 4 far away; row 1 shares row 0's page.
    vectors = _unit([1, 0], [1, 0.01], [1, 0.05], [1, 0.1], [0, 1])
    labels = ["禁", "制", "禁", "禁", "制"]
    found, floor = streaks(labels, [1, 1, 2, 3, 4], vectors, k=3)
    assert found.tolist() == [2, 0, 0, 1, 0]
    assert floor[0] > 0.99 and floor[1] == 0


def test_a_row_is_judged_under_the_label_asked_and_stays_a_neighbour_under_its_own():
    vectors = _unit([1, 0], [1, 0.05], [1, 0.1], [0, 1])
    labels = ["禁", "禁", "禁", "制"]
    found, _ = streaks(labels, [1, 2, 3, 4], vectors, k=2, asked={0: "制"})
    assert found.tolist() == [0, 2, 2, 0]


def _atlas(path, events):
    with sqlite3.connect(path) as db:
        db.executescript("""CREATE TABLE graphemes(id INTEGER PRIMARY KEY, text TEXT);
            CREATE TABLE crops(id INTEGER PRIMARY KEY, char INTEGER);
            CREATE TABLE events(id INTEGER PRIMARY KEY, crop INTEGER, at INTEGER, kind INTEGER, issue INTEGER, label INTEGER);
            INSERT INTO graphemes VALUES (1,'禁'),(2,'制'),(3,'ナ');""")
        db.executemany("INSERT INTO events(crop, at, kind, issue, label) VALUES (?,?,?,?,?)", events)
    return path


def test_a_review_counts_for_the_label_it_was_given_on(tmp_path):
    atlas = _atlas(tmp_path / "atlas.sqlite", [
        (10, 1, na.MATCH, None, 1),              # confirmed as 禁
        (11, 1, na.WRONG, 0, 2),                 # filed 禁 when indexed, corrected to 制
        (12, 1, na.WRONG, 0, 1),                 # the index already holds the correction
        (13, 1, na.WRONG, 2, 1),                 # a bad crop of 禁
        (14, 1, na.WRONG, 0, 2), (14, 2, na.MATCH, None, 2),  # the last verdict counts
        (99, 1, na.MATCH, None, 1),              # not in the index
    ])
    crops = [10, 11, 12, 13, 14, None]
    was = ["禁", "禁", "禁", "禁", "禁", "禁"]
    labels = ["禁", "制", "禁", "禁", "制", "禁"]
    assert reviewed(crops, was, labels, atlas) == {0: ("confirmed", "禁"), 1: ("character", "禁"),
                                                   3: ("crop", "禁"), 4: ("confirmed", "制")}


def test_the_measurement_counts_each_kind_reaching_each_streak():
    verdicts = {0: ("confirmed", "禁"), 1: ("confirmed", "禁"), 2: ("character", "禁"), 3: ("crop", "禁")}
    result = evaluate(["a", "b", "c", "d"], verdicts, np.array([20, 7, 1, 20]), k=20)
    assert result["judged"] == {"confirmed": 2, "character": 1, "crop": 1}
    assert result["bars"][0] == {"streak": 1, "character": 1, "confirmed": 2, "crop": 1}
    assert result["bars"][-1] == {"streak": 20, "character": 0, "confirmed": 1, "crop": 1}
    assert result["wrong_and_agreed"] == [{"id": "d", "label": "禁", "kind": "crop", "streak": 20}]
