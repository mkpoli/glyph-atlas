import gzip
import json
import sqlite3

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from glyph_atlas.review import written_forms as wf
from glyph_atlas.review.written_forms import UNBOXED, judge, nearness, pairs, run, twins, whole, written


def test_pairs_need_both_characters_held_and_enough_tables_and_start_with_the_commoner():
    counts = {"国": 20, "國": 40, "島": 40, "嶋": 3, "禁": 50}
    found = pairs(counts)
    assert pairs(counts, sources=99) == {}
    assert list(found) == [("國", "国")] and len(found["國", "国"]) >= wf.SOURCES


def _crops(centre, count, seed=0, spread=0.05):
    points = np.random.default_rng(seed).normal(0, spread, size=(count, 8)).astype(np.float32)
    points[:, : len(centre)] += np.array(centre, dtype=np.float32)
    return points / np.linalg.norm(points, axis=1, keepdims=True)


FIRST, SECOND = [1, 0, 0], [0, 1, 0]


def test_a_label_partly_written_as_the_variant_parts():
    vectors = np.concatenate([_crops(FIRST, 30, 1), _crops(SECOND, 25, 2), _crops(SECOND, 20, 3)])
    own = np.array([True] * 55 + [False] * 20)
    found = judge(vectors, own, np.zeros(75, bool))
    assert found["strays"] == 0 and found["filed"] == 20 and found["told_apart"]
    assert found["rows"].tolist() == list(range(30, 55))
    assert (found["near"] >= wf.NEAR).all() and (found["near"] - found["own"] >= wf.MARGIN).all()


def test_the_split_is_taken_again_until_nothing_moves():
    # 48 of the label's crops are the variant's shape, so its mean starts halfway there and the four
    # crops past the middle start on the label's side; they cross once the two means are taken again.
    between = _crops([0.6, 0.8, 0], 4, 8, 0.01)
    vectors = np.concatenate([_crops(FIRST, 50, 1), _crops(SECOND, 48, 2), between, _crops(SECOND, 20, 3)])
    own = np.array([True] * 102 + [False] * 20)
    assert judge(vectors, own, np.zeros(122, bool))["rows"].tolist() == list(range(50, 102))


def test_a_label_mostly_of_the_variants_shape_is_not_told_apart():
    vectors = np.concatenate([_crops(FIRST, 15, 1), _crops(SECOND, 40, 2), _crops(SECOND, 20, 3)])
    found = judge(vectors, np.array([True] * 55 + [False] * 20), np.zeros(75, bool))
    assert found is None or not found["told_apart"]


def test_one_picture_filed_under_both_characters_is_left_out():
    variant = _crops(SECOND, 20, 3)
    vectors = np.concatenate([_crops(FIRST, 30, 1), variant[:4], variant])
    own = np.array([True] * 34 + [False] * 20)
    assert np.flatnonzero(twins(vectors, own)).tolist() == list(range(30, 38))
    found = judge(vectors, own, np.zeros(54, bool))
    assert found["rows"].size == 0 and found["filed"] == 16 and found["labelled"] == 30


def test_written_needs_nearness_a_lead_and_a_whole_box():
    assert written(0.8, 0.7, None) and written(0.9, 0.5, UNBOXED)
    assert not written(0.79, 0.2, None)
    assert not written(0.95, 0.86, None)
    assert not written(0.95, 0.5, "joined") and not written(0.95, 0.5, "blank")


def test_a_crop_of_neither_form_is_near_neither_side():
    vectors = np.concatenate([_crops(FIRST, 30, 1), _crops([0, 0.1, 1], 1, 4), _crops(SECOND, 20, 3)])
    own = np.array([True] * 31 + [False] * 20)
    found = judge(vectors, own, np.zeros(51, bool))
    assert found["rows"].tolist() == [30] and found["near"][0] < wf.NEAR


def test_strays_count_the_variants_crops_on_the_labels_side():
    vectors = np.concatenate([_crops(FIRST, 30, 1), _crops(FIRST, 6, 5), _crops(SECOND, 20, 3)])
    own = np.array([True] * 30 + [False] * 26)
    found = judge(vectors, own, np.zeros(56, bool))
    assert found["strays"] == 6 and found["filed"] == 26 and not found["told_apart"]


def test_one_stray_is_allowed_and_a_suspect_stray_counts():
    vectors = np.concatenate([_crops(FIRST, 30, 1), _crops(FIRST, 2, 5), _crops(SECOND, 20, 3)])
    own = np.array([True] * 30 + [False] * 22)
    assert judge(np.delete(vectors, 30, 0), np.delete(own, 30), np.zeros(51, bool))["told_apart"]
    assert not judge(vectors, own, np.arange(52) == 31)["told_apart"]


def test_suspects_move_no_mean_and_are_judged():
    # 60 suspect crops under the variant, opposite the label, would draw the variant's mean away from
    # the crops between.
    vectors = np.concatenate([_crops(FIRST, 40, 1), _crops([0.6, 0.8, 0], 3, 8, 0.01), _crops(SECOND, 1, 7),
                              _crops([-1, 0, 0], 60, 6), _crops(SECOND, 20, 3)])
    own = np.array([True] * 44 + [False] * 80)
    suspect = np.zeros(124, bool)
    suspect[43:104] = True
    found = judge(vectors, own, suspect)
    assert found["rows"].tolist() == [40, 41, 42, 43] and found["strays"] == 0
    assert found["near"][3] >= wf.NEAR


def test_too_few_crops_on_a_side_judge_nothing():
    vectors = np.concatenate([_crops(FIRST, 30, 1), _crops(SECOND, wf.MIN_SIDE - 1, 3)])
    assert judge(vectors, np.array([True] * 30 + [False] * (wf.MIN_SIDE - 1)), np.zeros(39, bool)) is None


def test_nearness_is_the_mean_of_the_nearest():
    others = np.array([[1, 0], [0.6, 0.8], [0, 1]], dtype=np.float32)
    assert np.allclose(nearness(np.array([[1, 0]], dtype=np.float32), others, k=2), [0.8])


def test_a_box_is_judged_against_its_page(tmp_path):
    crops = [{"slot": n, "box": {"w": 40, "h": 40}} for n in range(6)] + [{"slot": "6", "box": {"w": 40, "h": 80}}, {"slot": 7}]
    with gzip.open(tmp_path / "7-1.json.gz", "wt", encoding="utf-8") as handle:
        json.dump({"crops": crops}, handle)
    page = 7 << wf.PAGE_BITS
    assert whole({"a": page | 1, "b": page | 6, "c": page | 7, "d": 8 << wf.PAGE_BITS}, tmp_path) == {"a": None, "b": "joined"}


def test_run_reads_labels_boxes_and_live_marks_and_writes_what_it_finds(tmp_path):
    # 国 and 國 are a pair of the variant tables: 30 crops of 國, three of them written 国 (one in a
    # joined box), and 20 of 国. The suspect mark on the first names a character the crop left.
    vectors = np.concatenate([_crops(FIRST, 30, 1), _crops(SECOND, 3, 2), _crops(SECOND, 20, 3)])
    ids = [f"t:{n}" for n in range(53)]
    similar, pages = tmp_path / "similar", tmp_path / "pages"
    similar.mkdir(), pages.mkdir()
    pq.write_table(pa.table({"id": ids, "label": ["國"] * 53}), similar / "units.parquet")
    np.save(similar / "vectors.npy", vectors.astype(np.float16))
    number = {identity: (7 << wf.PAGE_BITS) | n for n, identity in enumerate(ids)}
    with sqlite3.connect(tmp_path / "mirror.sqlite") as db:
        db.execute("CREATE TABLE ids(text TEXT, id INTEGER)")
        db.executemany("INSERT INTO ids VALUES (?,?)", number.items())
    with sqlite3.connect(tmp_path / "atlas.sqlite") as db:
        db.executescript("""CREATE TABLE graphemes(id INTEGER, text TEXT); CREATE TABLE crops(id INTEGER, book INTEGER, char INTEGER);
            CREATE TABLE suspects(crop INTEGER, label TEXT); INSERT INTO graphemes VALUES (1,'國'),(2,'国');""")
        db.executemany("INSERT INTO crops VALUES (?,?,?)",
                       [(number[i], 5, 1 if n < 33 else 2) for n, i in enumerate(ids)])
        db.execute("INSERT INTO suspects VALUES (?, '島')", (number["t:0"],))
    boxes = [{"slot": n, "box": {"w": 40, "h": 90 if n == 31 else 40}} for n in range(53) if n != 32]
    with gzip.open(pages / "7-9.json.gz", "wt", encoding="utf-8") as handle:
        json.dump({"crops": []}, handle)
    with gzip.open(pages / "7-10.json.gz", "wt", encoding="utf-8") as handle:
        json.dump({"crops": boxes}, handle)
    result = run(similar, mirror=tmp_path / "mirror.sqlite", atlas=tmp_path / "atlas.sqlite", pages=pages)
    found = {row["id"]: row for row in pq.read_table(similar / wf.FILE).to_pylist()}
    assert {i: (row["gate"], row["written"], row["label"], row["other"], row["book"]) for i, row in found.items()} == {
        "t:30": (None, True, "國", "国", 5), "t:31": ("joined", False, "國", "国", 5), "t:32": (UNBOXED, True, "國", "国", 5)}
    assert result["written"] == 2 and result["told_apart"] == 1 and result["items"][0]["written"] == 2
    assert wf._held(ids, tmp_path / "mirror.sqlite", tmp_path / "atlas.sqlite")[2] == set()
    with sqlite3.connect(tmp_path / "atlas.sqlite") as db:
        db.execute("UPDATE suspects SET label='國'")
    assert wf._held(ids, tmp_path / "mirror.sqlite", tmp_path / "atlas.sqlite")[2] == {number["t:0"]}
    # With most of 國 in the shape of 国 the pair is not told apart, and nothing of it is written.
    np.save(similar / "vectors.npy", np.concatenate([_crops(FIRST, 12, 1), _crops(SECOND, 41, 3)]).astype(np.float16))
    result = run(similar, mirror=tmp_path / "mirror.sqlite", atlas=tmp_path / "atlas.sqlite", pages=pages)
    assert result["told_apart"] == 0 and result["judged"] == 0
