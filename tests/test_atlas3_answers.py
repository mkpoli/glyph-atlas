import gzip
import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_publish import layout  # noqa: F401 -- the fixture

SHARDS = 256


def answers(publish, monkeypatch, *flags):
    sys.modules.pop("answers", None)
    module = importlib.import_module("answers")
    uploaded = []
    monkeypatch.setattr(module, "query", publish.query)
    monkeypatch.setattr(module, "upload", lambda files, prefix: uploaded.append((prefix, [f.name for f in files])))
    monkeypatch.setattr(sys, "argv", ["answers.py", *flags])
    module.main()
    return uploaded


def shard_of(root, char):
    pub = max(p for p in (root / "out" / "pub").iterdir() if not p.name.startswith("pointer-"))
    return json.loads(gzip.decompress((pub / "counts" / f"{char % SHARDS}.json.gz").read_bytes()))


def test_a_publication_counts_each_character_and_moves_the_pointer_last(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    uploaded = answers(publish, monkeypatch)
    atlas = sqlite3.connect(root / "out" / "atlas.sqlite")
    char = atlas.execute("SELECT id FROM graphemes WHERE text='い'").fetchone()[0]
    body = shard_of(root, char)
    assert body["cell"] == ["kind", "style", "state", "quiz", "decade", "visual_group", "n"]
    # い: one checked crop of book:a, witnessed in 1700.
    state = atlas.execute("SELECT code FROM codes WHERE field='state' AND key='checked'").fetchone()[0]
    assert [c[2] for c in body["chars"][str(char)]] == [state]
    assert body["chars"][str(char)][0][4] == 1700 and body["chars"][str(char)][0][-1] == 1
    assert sum(len(names) for prefix, names in uploaded if prefix.endswith("/counts/")) == SHARDS
    assert uploaded[-1] == ("v3/", ["pointer.json"])
    assert remote.query("glyph-atlas-v3", "SELECT gen FROM generation")[0]["gen"] == 2
    pointer = json.loads(gzip.decompress(next((root / "out" / "pub").glob("pointer-*/pointer.json")).read_bytes()))
    assert pointer["through_gen"] == 1


def test_a_dry_run_bumps_nothing_and_uploads_nothing(layout, monkeypatch):  # noqa: F811
    _root, publish, remote = layout
    assert answers(publish, monkeypatch, "--dry-run") == []
    assert remote.query("glyph-atlas-v3", "SELECT gen FROM generation")[0]["gen"] == 1


def test_review_events_on_the_new_layout_stop_a_publication_before_the_pointer_moves(layout, monkeypatch):  # noqa: F811
    _root, publish, remote = layout
    remote.dbs["glyph-atlas-v3"].execute("INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind) VALUES(1,1,0,1,1,1,0,0)")
    module_uploads = []
    with pytest.raises(SystemExit):
        module_uploads = answers(publish, monkeypatch)
    assert module_uploads == []


def test_a_bump_another_run_made_stops_this_one(layout, monkeypatch):  # noqa: F811
    _root, publish, _remote = layout
    real = publish.query

    def racing(database, sql):
        if sql.startswith("UPDATE generation"):
            real(database, "UPDATE generation SET gen = gen + 1")
        return real(database, sql)

    monkeypatch.setattr(publish, "query", racing)
    with pytest.raises(SystemExit):
        answers(publish, monkeypatch)
