"""The suspect SQL replaces the table from every marks file and loads into the migrated schema."""
import importlib
import json
import sqlite3
from pathlib import Path

import pytest

MIGRATION = Path("apps/cloudflare/migrations/0017_unit_suspects.sql").read_text()


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    return importlib.import_module("export_quiz_suspects")


def marks(path, suspects):
    path.write_text(json.dumps({"suspects": suspects}))
    return path


def test_the_sql_replaces_every_mark(module, tmp_path):
    box = {"x": 1, "y": 2, "w": 3, "h": 4}
    local = marks(tmp_path / "local.json", {"it's": {"p": 0.01, "reads_as": "ア", "label": "イ", "box": None}})
    corpus = marks(tmp_path / "corpus.json", {"codh:1": {"p": 0.0, "reads_as": None, "label": "な", "box": box}})
    assert module.export([local, corpus], tmp_path / "suspects.sql") == 2
    db = sqlite3.connect(":memory:")
    db.executescript(MIGRATION + "INSERT INTO unit_suspects VALUES('gone',0.02,NULL,'あ',NULL);")
    db.executescript((tmp_path / "suspects.sql").read_text())
    assert sorted(db.execute("SELECT id, p, reads_as, label, json_extract(box, '$.h') FROM unit_suspects")) == [
        ("codh:1", 0.0, None, "な", 4), ("it's", 0.01, "ア", "イ", None)]


@pytest.mark.parametrize("mark", [{"p": "0.1", "reads_as": None, "label": "a", "box": None},
                                  {"p": 2, "reads_as": None, "label": "a", "box": None},
                                  {"p": 0.1, "reads_as": 3, "label": "a", "box": None},
                                  {"p": 0.1, "reads_as": None, "box": None},
                                  {"p": 0.1, "reads_as": None, "label": "a", "box": {"x": 1}}])
def test_a_malformed_mark_is_refused(module, tmp_path, mark):
    with pytest.raises(ValueError):
        module.export([marks(tmp_path / "m.json", {"a": mark})], tmp_path / "suspects.sql")


def test_an_earlier_file_decides_a_crop_it_scored(module, tmp_path):
    hosted = tmp_path / "hosted.json"
    hosted.write_text(json.dumps({"scored_ids": ["a", "b"], "suspects": {"a": {"p": 0.01, "reads_as": "ア", "label": "イ", "box": None}}}))
    corpus = marks(tmp_path / "corpus.json", {"a": {"p": 0.02, "reads_as": "ア", "label": "イ", "box": None},
                                              "b": {"p": 0.02, "reads_as": None, "label": "イ", "box": None},
                                              "c": {"p": 0.03, "reads_as": None, "label": "ウ", "box": None}})
    assert module.export([hosted, corpus], tmp_path / "suspects.sql") == 2
    sql = (tmp_path / "suspects.sql").read_text()
    # a: the hosted mark; b: scored clean on the hosted side, so the corpus mark is dropped; c: the corpus mark.
    assert "'a',0.01," in sql and "'b'," not in sql and "'c',0.03," in sql
