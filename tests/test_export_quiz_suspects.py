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
    local = marks(tmp_path / "local.json", {"it's": {"p": 0.01, "reads_as": "ア"}})
    corpus = marks(tmp_path / "corpus.json", {"codh:1": {"p": 0.0, "reads_as": None}})
    assert module.export([local, corpus], tmp_path / "suspects.sql") == 2
    db = sqlite3.connect(":memory:")
    db.executescript(MIGRATION + "INSERT INTO unit_suspects VALUES('gone',0.02,NULL);")
    db.executescript((tmp_path / "suspects.sql").read_text())
    assert sorted(db.execute("SELECT id, p, reads_as FROM unit_suspects")) == [("codh:1", 0.0, None), ("it's", 0.01, "ア")]


@pytest.mark.parametrize("mark", [{"p": "0.1", "reads_as": None}, {"p": 2, "reads_as": None}, {"p": 0.1, "reads_as": 3}])
def test_a_malformed_mark_is_refused(module, tmp_path, mark):
    with pytest.raises(ValueError):
        module.export([marks(tmp_path / "m.json", {"a": mark})], tmp_path / "suspects.sql")


def test_two_files_that_disagree_are_refused(module, tmp_path):
    one = marks(tmp_path / "one.json", {"a": {"p": 0.01, "reads_as": "ア"}})
    two = marks(tmp_path / "two.json", {"a": {"p": 0.02, "reads_as": "ア"}})
    with pytest.raises(ValueError, match="differently"):
        module.export([one, two], tmp_path / "suspects.sql")
