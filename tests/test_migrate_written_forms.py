"""Written forms move into the ledger: forms, confirmations recovered from saved requests, clears, unknowns."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

from test_review_atlas import LINE, dataset  # noqa: F401

from glyph_atlas import evidence, representation
from glyph_atlas.review.store import ReviewRequest, Store

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import migrate_written_forms as migrate
from cloudflare_schema import schema

PIXELS = "a" * 64
CURRENT = f"hk:1@{PIXELS}@1,2,3,4"


def form_of(value: str) -> str:
    return representation.anchored_form(representation.typed(value))


def test_what_a_row_said_is_read_from_its_form_or_its_saved_request():
    row = {"label": "還", "form": None}
    assert migrate.chosen({**row, "form": "𮟃", "typed": "𮟃"}) == ("form", "𮟃")
    assert migrate.chosen({**row, "typed": "還"}) == ("form", "還"), "the label typed is a confirmation"
    assert migrate.chosen({**row, "typed": "U+9084"}) == ("form", "還"), "in the notation the picker took"
    assert migrate.chosen({**row, "typed": None}) == ("clear", None)
    assert migrate.chosen({**row, "typed": " "}) == ("clear", None)
    assert migrate.chosen({**row, "typed": Ellipsis}) == ("unknown", None), "a request that is lost says nothing"
    assert migrate.chosen({**row, "typed": "𮟃"}) == ("unknown", None), "a stored null for another value is not explained"


def journal_row(identity, actor, form, typed, at, seen=CURRENT, pixels=PIXELS):
    request = "not json" if typed is Ellipsis else json.dumps({"target": "hk:1", "input": {"id": identity, "form": typed}})
    return {"id": identity, "target": "hk:1", "actor": actor, "pixels": pixels, "label": "還", "form": form,
            "request": request, "at": at, "seen": seen}


def site():
    db = sqlite3.connect(":memory:", isolation_level=None)
    schema(db)
    db.execute("INSERT INTO units(id,origin,character,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual) "
               "VALUES('hk:1','local','還','unknown','kanji','pending',0,1,1,1,?,'{}','{}','{}')",
               (json.dumps({"id": "hk:1", "image_sha256": PIXELS, "box": {"x": 1, "y": 2, "w": 3, "h": 4}}),))
    return db


def test_the_site_journal_becomes_claims_that_d1_resolves(tmp_path, monkeypatch):
    journal = [journal_row("cf:1", "ann", "⿺辶𦊷", "⿺辶𦊷", "2026-09-30T00:00:00.000Z"),
               # ann chose the label itself, which the layer stored as no form: a confirmation.
               journal_row("cf:2", "ann", None, "還", "2026-09-30T00:01:00.000Z"),
               # bob saved on pixels the crop no longer has: his claim is kept and stands on nothing.
               journal_row("cf:3", "bob", "𮟃", "𮟃", "2026-09-30T00:02:00.000Z", seen=None, pixels="b" * 64),
               # cat cleared a field she had never set, and dan's request is lost.
               journal_row("cf:4", "cat", None, None, "2026-09-30T00:03:00.000Z"),
               journal_row("cf:5", "dan", None, Ellipsis, "2026-09-30T00:04:00.000Z")]
    monkeypatch.setattr(migrate, "d1", lambda sql: journal)
    report = migrate.migrate_d1(tmp_path)
    assert {k: report[k] for k in ("journal", "form", "confirmation", "unknown", "clear-refused")} == \
        {"journal": 5, "form": 2, "confirmation": 1, "unknown": 1, "clear-refused": 1}
    sql = "".join((tmp_path / part).read_text() for part in report["parts"])
    db = site()
    for _ in range(2):
        db.executescript(sql)
        assert db.execute("SELECT count(*) FROM assertions WHERE predicate='has_form'").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM forms").fetchone()[0] == 3, "one form for each value chosen"
        row = db.execute("SELECT status,object,supporting,crop_version FROM current_claims WHERE subject='hk:1'").fetchone()
        assert (row[0], row[1], row[3]) == ("asserted", form_of("還"), CURRENT)
    legacy = [r[0] for r in db.execute("SELECT legacy FROM assertions WHERE predicate='has_form' ORDER BY asserted_at")]
    assert legacy == ["written_forms:cf:1", "written_forms:cf:2", "written_forms:cf:3"]
    stale = db.execute("SELECT e.ref FROM assertion_evidence e JOIN assertions a ON a.id=e.assertion WHERE a.asserted_by='bob'").fetchone()[0]
    assert stale == f"hk:1@{'b' * 64}@?"
    assert db.execute("SELECT reason FROM assertion_actions WHERE action='retract'").fetchone()[0].startswith("superseded by mg:cf:2:")


def test_an_empty_journal_writes_no_part(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate, "d1", lambda sql: [])
    assert migrate.migrate_d1(tmp_path)["parts"] == []


def test_a_datasets_written_form_events_become_its_ledger_claims(dataset):  # noqa: F811
    unit = LINE + ":u0"
    store = Store(dataset)
    page = next(iter(store.pages().values()))
    pixels = page.sha256
    for client, form, typed in (("ann", "𮟃", "𮟃"), ("bob", None, "あ"), ("ann", None, None)):
        store.record(ReviewRequest(target_type="unit", target_id=unit, field="written_form", new=form, client_id=client,
                                   evidence=json.dumps({"kind": "written-form-review", "label": "あ",
                                                        "request": {"id": str(uuid4()), "image_sha256": pixels, "form": typed}})))
    preview = migrate.migrate_dataset(dataset, None, apply=False)
    assert {k: preview[k] for k in ("journal", "form", "confirmation", "clear")} == {"journal": 3, "form": 1, "confirmation": 1, "clear": 1}
    version = evidence.crop_version(unit, pixels, store.unit(unit).box)
    assert store.claims(unit, version)["history"] == [], "a preview writes nothing"
    migrate.migrate_dataset(dataset, None, apply=True)
    migrate.migrate_dataset(dataset, None, apply=True)
    claims = store.claims(unit, version)
    assert len(claims["history"]) == 2, "a second run adds nothing"
    [row] = claims["current"]
    assert (row["status"], row["object"]) == ("asserted", form_of("あ")), "ann's form is cleared; bob's confirmation stands"
    assert store.revision(unit) == 0, "an archived written form moved no revision, and moves none now"


def test_an_archived_written_form_event_changes_no_unit_on_a_rebuild(dataset):  # noqa: F811
    unit = LINE + ":u0"
    store = Store(dataset)
    store.record(ReviewRequest(target_type="unit", target_id=unit, field="written_form", new="𮟃", client_id="ann"))
    assert store.rebuild()["events"] == 1
    assert "written_form" not in store.unit(unit).model_dump()
