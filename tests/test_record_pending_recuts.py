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
    forget_recuts(store)
    return publication, after


def forget_recuts(store):
    """Take the recuts out of the journal, and the revisions the imports recorded with them."""
    with sqlite3.connect(store.directory / "review.sqlite") as db:
        db.execute("DELETE FROM events WHERE field='recut'")
    Store(store.directory, rebuilding=True).rebuild()
    with sqlite3.connect(store.directory / "review.sqlite") as db:
        db.execute("UPDATE cloudflare_imports SET local_revision=? WHERE rowid=(SELECT max(rowid) FROM cloudflare_imports)",
                   (Store(store.directory).revision("u"),))


def run(store, tmp_path, monkeypatch, capsys, live, *args):
    path = tmp_path / "live.jsonl"
    path.write_text(json.dumps({"id": "u", **live}) + "\n")
    monkeypatch.setattr(sys, "argv", ["record_pending_recuts.py", str(store.directory), "--live", str(path), *args])
    record_pending_recuts.main()
    return json.loads(capsys.readouterr().out)


def test_a_pending_recut_is_recorded_past_the_live_revision_once(store, tmp_path, monkeypatch, capsys):
    publication, after = imported_without_recut(store)
    live = after["revision"]
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
    publication, after = imported_without_recut(store)
    live = after["revision"]
    row = site_row(publication, revision=live, box={**BOX, "x": 13})
    assert run(store, tmp_path, monkeypatch, capsys, row, "--apply")["counts"] == {"other-box": 1}
    assert Store(store.directory).revision("u") == live


def test_a_redraw_followed_by_another_imported_review_is_still_due(store, tmp_path, monkeypatch, capsys):
    publication, after = imported_without_recut(store)
    # A review on the old cut, imported after the redraw: the last import brought no box back.
    record, after = remote(publication, before=after, issue="crop", character=None)
    _, report = bridge.ingest_cloudflare(store, payload(record), apply=True)
    assert report["counts"] == {"imported": 1}, report
    forget_recuts(store)
    live = after["revision"]
    applied = run(store, tmp_path, monkeypatch, capsys, site_row(publication, revision=live, box=BOX), "--apply")
    assert applied["counts"] == {"recorded": 1} and applied["items"][0]["revision"] > live
