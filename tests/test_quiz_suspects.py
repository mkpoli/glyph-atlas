"""Quick review suspects: crops the classifier gives almost no probability under their own label."""
import json

import numpy as np
import pytest

from glyph_atlas.review import quiz_suspects

CLASSES = ["U+30A2", "U+30A4", "U+4EEE", "U+5047", "U+3078", "U+304A", "U+592A", "U+5927", "U+305F", "U+30BF", "other"]


def row(**weights):
    """One softmax row over `CLASSES`, from weights named ア, イ, 仮, 假, へ, お, 太, 大, た, タ and other."""
    order = ["ア", "イ", "仮", "假", "へ", "お", "太", "大", "た", "タ", "other"]
    values = np.array([weights.get(name, 0.0) for name in order])
    return values / values.sum()


@pytest.fixture(scope="module")
def labels():
    return quiz_suspects.Labels(CLASSES)


def test_a_crop_read_as_another_character_names_it(labels):
    assert labels.judge(np.stack([row(ア=.97, イ=.03)]), ["イ"]) == [{"p": 0.03, "reads_as": "ア"}]


def test_enough_probability_on_the_label_is_not_a_suspect(labels):
    assert labels.judge(np.stack([row(ア=.9, イ=.1)]), ["イ"]) == [None]


def test_a_merged_family_counts_as_the_label(labels):
    # 仮 and 假 are one family to the classifier: a 仮 read as 假 is no suspect.
    assert labels.judge(np.stack([row(假=.99, イ=.01)]), ["仮"]) == [None]


def test_a_suspect_without_one_clear_reading_names_none(labels):
    assert labels.judge(np.stack([row(ア=.45, other=.54, イ=.01)]), ["イ"]) == [{"p": 0.01, "reads_as": None}]
    assert labels.judge(np.stack([row(other=.99, イ=.01)]), ["イ"]) == [{"p": 0.01, "reads_as": None}]


def test_an_unfamiliar_kanji_is_a_suspect_only_when_it_reads_as_another_character(labels):
    assert labels.judge(np.stack([row(other=.99, 仮=.01)]), ["仮"]) == [None]
    assert labels.judge(np.stack([row(ア=.99, 仮=.01)]), ["仮"]) == [{"p": 0.01, "reads_as": "ア"}]


def test_a_label_the_classifier_never_learnt_is_never_a_suspect(labels):
    assert labels.judge(np.stack([row(ア=1.)]), ["ヰ"]) == [None]


def test_marks_are_read_from_the_dataset_and_follow_a_new_file(tmp_path):
    assert quiz_suspects.load(tmp_path) == {}
    (tmp_path / "quiz-suspects.json").write_text(json.dumps({"suspects": {"a": {"p": 0.01, "reads_as": "ア"}}}))
    assert quiz_suspects.load(tmp_path) == {"a": {"p": 0.01, "reads_as": "ア"}}
    (tmp_path / "quiz-suspects.json").write_text(json.dumps({"suspects": {"b": {"p": 0.02, "reads_as": None}}}))
    assert list(quiz_suspects.load(tmp_path)) == ["b"]


def test_a_katakana_read_as_its_hiragana_is_no_suspect(labels):
    # ヘ has no class of its own; the classifier's へ is the same shape, read the same way.
    assert labels.judge(np.stack([row(へ=.99, ア=.01)]), ["ヘ"]) == [None]
    assert labels.judge(np.stack([row(ア=.99, へ=.01)]), ["ヘ"]) == [{"p": 0.01, "reads_as": "ア"}]


def test_a_mark_holds_only_for_the_label_and_box_it_was_made_for():
    box = {"x": 1, "y": 2, "w": 3, "h": 4}
    mark = {"p": 0.01, "reads_as": "ア", "label": "イ", "box": box}
    assert quiz_suspects.current(mark, "イ", {**box, "x": 1.0}) == {"p": 0.01, "reads_as": "ア"}
    assert quiz_suspects.current(mark, "ア", box) is None
    assert quiz_suspects.current(mark, "イ", {**box, "h": 5}) is None
    assert quiz_suspects.current({**mark, "box": None}, "イ", None) == {"p": 0.01, "reads_as": "ア"}
    assert quiz_suspects.current(None, "イ", box) is None


def test_a_look_alike_is_expected_and_not_a_suspect():
    labels = quiz_suspects.Labels(CLASSES, {frozenset(("太", "大"))})
    assert labels.judge(np.stack([row(大=.99, 太=.01)]), ["太"]) == [None]
    assert labels.judge(np.stack([row(ア=.99, 太=.01)]), ["太"]) == [{"p": 0.01, "reads_as": "ア"}]


def test_a_kanji_read_as_the_kana_it_is_the_jibo_of_is_not_a_suspect(labels):
    # 於 is the 字母 of お: written in cursive, the two are one shape.
    assert labels.judge(np.stack([row(お=.99, ア=.01)]), ["於"]) == [None]


def test_look_alikes_measured_for_another_checkpoint_are_refused(tmp_path):
    checkpoint = tmp_path / "best.pt"
    checkpoint.write_bytes(b"one")
    path = tmp_path / "lookalikes.json"
    measured = {"checkpoint": quiz_suspects._digest(checkpoint), "rate": quiz_suspects.LOOKALIKE_RATE,
                "misreads": quiz_suspects.LOOKALIKE_MISREADS, "pairs": [["大", "太"]]}
    path.write_text(json.dumps(measured))
    assert quiz_suspects.load_lookalikes(path, checkpoint) == {frozenset(("太", "大"))}
    path.write_text(json.dumps({**measured, "misreads": 1}))
    with pytest.raises(RuntimeError, match="thresholds"):
        quiz_suspects.load_lookalikes(path, checkpoint)
    path.write_text("{")
    with pytest.raises(RuntimeError, match="not a look-alikes file"):
        quiz_suspects.load_lookalikes(path, checkpoint)
    path.write_text(json.dumps(measured))
    checkpoint.write_bytes(b"two")
    with pytest.raises(RuntimeError, match="another checkpoint"):
        quiz_suspects.load_lookalikes(path, checkpoint)
    with pytest.raises(RuntimeError, match="lookalikes"):
        quiz_suspects.load_lookalikes(tmp_path / "missing.json", checkpoint)


def test_a_kanji_read_as_the_hiragana_it_is_the_cursive_of_is_not_a_suspect(labels):
    # た is the cursive of 太; タ comes from 多, so a 太 read as タ is still a suspect.
    assert labels.judge(np.stack([row(た=.99, 太=.01)]), ["太"]) == [None]
    assert labels.judge(np.stack([row(タ=.99, 太=.01)]), ["太"]) == [{"p": 0.01, "reads_as": "タ"}]


def test_the_kana_origins_table_covers_every_modern_hiragana():
    from glyph_atlas import refs

    origins = refs.kana_origins()
    covered = {kana for kanas in origins.values() for kana in kanas}
    assert covered == set("あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわゐゑをん")
    assert origins["太"] == {"た"} and origins["曽"] == origins["曾"] == {"そ"}


def test_a_katakana_that_only_sounds_like_the_label_s_kana_is_still_a_suspect():
    # イ is part of 伊; 以 read as イ is not 以 written in cursive.
    labels = quiz_suspects.Labels(CLASSES)
    assert labels.expected("以", "い")
    assert not labels.expected("以", "イ")


def test_the_origins_builder_reads_the_whole_field(monkeypatch):
    import importlib
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    build = importlib.import_module("build_kana_origins")
    assert build.origin("| 平仮名字源 = 和の[[草書体]]|Unicode平仮名=308F") == "和の草書体"
    assert build.origin("|平仮名字源=無の[[草書体|草書]]|x=1") == "無の草書"
    page = {"revisions": [{"revid": 7, "slots": {"main": {"content": "|平仮名字源=川または州の[[草書体]]"}}}]}
    assert [row[1] for row in build.rows({"つ": page})] == ["川", "州"]


def test_a_reading_reviewers_found_to_be_a_cursive_form_is_not_a_suspect(labels, monkeypatch):
    from glyph_atlas import refs

    assert ("可", "一") in refs.suspect_forms()
    monkeypatch.setattr(labels, "forms", frozenset({("太", "ア")}))
    assert labels.judge(np.stack([row(ア=.99, 太=.01)]), ["太"]) == [None]
    assert labels.judge(np.stack([row(イ=.99, 太=.01)]), ["太"]) == [{"p": 0.01, "reads_as": "イ"}]


def test_catalogue_crops_are_marked_under_the_label_and_box_they_are_served_with(tmp_path, monkeypatch):
    import sqlite3

    catalogue = tmp_path / "atlas.sqlite"
    with sqlite3.connect(catalogue) as db:
        db.execute("CREATE TABLE units (id TEXT PRIMARY KEY, data TEXT)")
        box = {"x": 1, "y": 2, "w": 3, "h": 4}
        db.executemany("INSERT INTO units VALUES(?,?)", [
            ("a", json.dumps({"label": "イ", "box": box, "image": "/atlas/media/" + "0" * 64 + ".webp"})),
            ("no-image", json.dumps({"label": "イ", "box": box, "image": "/atlas/characters/x/image"})),
        ])
    seen = {}

    def mark(crops, target, **kwargs):
        seen.update({c[0]: (c[1], c[2]) for c in crops})
        return {"scored": len(crops)}

    monkeypatch.setattr(quiz_suspects, "_mark", mark)
    assert quiz_suspects.compute_catalogues([catalogue], tmp_path / "out.json", checkpoint=tmp_path) == {"scored": 1}
    assert seen == {"a": ("イ", box)}


def test_a_crop_in_two_catalogues_is_scored_once_as_the_later_serves_it(tmp_path, monkeypatch):
    import sqlite3

    image = "/atlas/media/" + "0" * 64 + ".webp"
    for name, label in (("one", "イ"), ("two", "ア")):
        with sqlite3.connect(tmp_path / f"{name}.sqlite") as db:
            db.execute("CREATE TABLE units (id TEXT PRIMARY KEY, data TEXT)")
            db.execute("INSERT INTO units VALUES(?,?)", ("a", json.dumps({"label": label, "box": None, "image": image})))
    seen = []
    monkeypatch.setattr(quiz_suspects, "_mark", lambda crops, target, **kwargs: seen.extend((c[0], c[1]) for c in crops) or {})
    quiz_suspects.compute_catalogues([tmp_path / "one.sqlite", tmp_path / "two.sqlite"], tmp_path / "out.json",
                                     checkpoint=tmp_path)
    assert seen == [("a", "ア")]
