"""A crop imported with its redrawn box before the import recorded the recut gets it afterwards."""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import pytest
import record_pending_recuts
from test_cloudflare_import import baseline, make_store, payload, redrawn, remote, site_row

from glyph_atlas.review import cloudflare_import as bridge
from glyph_atlas.review.store import Store

BOX = {"x": 12, "y": 11, "w": 26, "h": 38}


@pytest.fixture
def store(tmp_path, monkeypatch):
    return make_store(tmp_path, monkeypatch)


def imported_without_recut(store):
    """The store as an import before the recut left it: the box and the review, and no recut."""
    publication = baseline(store)
    first, after = remote(publication, issue="crop", character=None, current=False)
    second, after = redrawn(publication, BOX, before=after)
    bridge.ingest_cloudflare(store, payload(first, second), apply=True)
    with sqlite3.connect(store.directory / "review.sqlite") as db:
        db.execute("DELETE FROM events WHERE field='recut'")
    Store(store.directory, rebuilding=True).rebuild()
    return publication, after["revision"]


def run(store, tmp_path, monkeypatch, capsys, live, *args):
    path = tmp_path / "live.jsonl"
    path.write_text(json.dumps({"id": "u", **live}) + "\n")
    monkeypatch.setattr(sys, "argv", ["record_pending_recuts.py", str(store.directory), "--live", str(path), *args])
    record_pending_recuts.main()
    return json.loads(capsys.readouterr().out)


def test_a_pending_recut_is_recorded_past_the_live_revision_once(store, tmp_path, monkeypatch, capsys):
    publication, live = imported_without_recut(store)
    assert Store(store.directory).revision("u") == live, "the collision the refresh refused"
    row = site_row(publication, revision=live, box=BOX)
    preview = run(store, tmp_path, monkeypatch, capsys, row)
    assert preview["counts"] == {"due": 1} and Store(store.directory).revision("u") == live
    applied = run(store, tmp_path, monkeypatch, capsys, row, "--apply")
    assert applied["counts"] == {"recorded": 1} and applied["items"][0]["revision"] > live
    assert Store(store.directory).revision("u") == applied["items"][0]["revision"]
    again = run(store, tmp_path, monkeypatch, capsys, row, "--apply")
    assert again["counts"] == {"recorded": 1} and "revision" not in again["items"][0]


def test_a_live_box_other_than_the_stores_is_left_alone(store, tmp_path, monkeypatch, capsys):
    publication, live = imported_without_recut(store)
    row = site_row(publication, revision=live, box={**BOX, "x": 13})
    assert run(store, tmp_path, monkeypatch, capsys, row, "--apply")["counts"] == {"other-box": 1}
    assert Store(store.directory).revision("u") == live
