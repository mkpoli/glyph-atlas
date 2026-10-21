import gzip
import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_history import A1, OLD, event, history, sources, sub, version
from test_atlas3_load import load
from test_atlas3_publish import (  # noqa: F401 -- layout is the fixture
    UNITS,
    ids,
    layout,
    retired_catalogue,
    retired_unit,
    run,
    write_source,
)


def check(monkeypatch, *flags):
    sys.modules.pop("check", None)
    sys.modules.pop("accounts", None)
    module = importlib.import_module("check")
    monkeypatch.setattr(sys, "argv", ["check.py", *flags])
    with pytest.raises(SystemExit) as stopped:
        module.main()
    return stopped.value.code


def test_a_fresh_build_passes_every_check(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    (root / "out" / "media").mkdir(exist_ok=True)
    assert check(monkeypatch) == 0


@pytest.mark.parametrize("change", ["UPDATE crops SET state = 1 WHERE kind = 0 AND state = 0",
                                    "UPDATE crops SET year = 1999 WHERE kind = 0",
                                    "UPDATE crops SET flags = 1 WHERE kind = 0",
                                    "DELETE FROM crops WHERE kind = 1"])
def test_a_crop_that_differs_from_its_live_record_fails(layout, monkeypatch, change):  # noqa: F811
    root, _publish, _remote = layout
    (root / "out" / "media").mkdir(exist_ok=True)
    db = sqlite3.connect(root / "out" / "atlas.sqlite")
    db.execute(change)
    db.commit()
    assert check(monkeypatch) == 1


@pytest.mark.parametrize("named", [None, "ex:a1", "ex:b1", "unset"])
def test_a_retired_crops_replacement_must_reach_a_live_crop(layout, monkeypatch, named):  # noqa: F811
    root, publish, _remote = layout
    (root / "out" / "media").mkdir(exist_ok=True)
    gone = retired_unit("ex:b1", "hl:p2", {"x": 0, "y": 0, "w": 10, "h": 10})
    run(publish, monkeypatch, retired_catalogue(root / "c.sqlite", [gone], [("ex:b1", "ex:gone"), ("ex:gone", "ex:a1")]))
    db = sqlite3.connect(root / "source-units.sqlite")
    db.execute(f"INSERT INTO units VALUES(100,{','.join('?' * len(UNITS))})", gone)
    db.commit()
    write_source(root, "unit_redirects", ("id", "target"), [("ex:b1", "ex:gone"), ("ex:gone", "ex:a1")])
    assert check(monkeypatch) == 0
    if named:
        rev = sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT max(rev) FROM pages").fetchone()[0]
        for path in (root / "out" / "pages").glob(f"*-{rev}.json.gz"):
            body = json.loads(gzip.decompress(path.read_bytes()))
            for e in body["crops"]:
                if e["id"] == "ex:b1":
                    # A text id, the retired crop itself (a loop), or nothing though its redirects reach a1.
                    if named == "unset":
                        del e["replaced_by"]
                    else:
                        e["replaced_by"] = ids(root)[named] if named == "ex:b1" else named
            path.write_bytes(gzip.compress(json.dumps(body).encode()))
        assert check(monkeypatch) == 1


def translated(root, monkeypatch, sources, history, **extra):
    """A history of reviews, rounds, a favourite, claims, form decisions and crop facts, translated by history.py."""
    old_ref = version("ex:a1", "p0", OLD)[0]
    box = {"x": 0, "y": 20, "w": 10, "h": 10}

    def claim(id, subject, predicate):
        return (id, None, subject, predicate, "", "", "fm:1", None, None, "observed", "u1", "2026-10-01T00:00:00Z",
                None, None, None, None, None)

    tables = dict(  # noqa: C408 -- the source tables by name, as sources() takes them
            events=[event("cf:1", "ex:a2", "r-b:s2", "r-b", 0, "2026-10-01T00:00:01Z", box),
                    event("cf:2", "ex:a2", "u1:s1", "u1", 1, "2026-10-01T00:00:02Z", box, verdict="wrong", issue="crop")],
            seen=[("ex:a1", "u1:s1", json.dumps(A1), "p1", "2026-10-01T00:00:02Z"),
                  ("ex:a1", "r-b:s2", json.dumps(OLD), "p0", "2026-10-01T00:00:01Z"),
                  ("codh:g1", "u1:s1", json.dumps(OLD), "x", "2026-10-01T00:00:02Z")],
            skips=[("ex:b1", "r-b:s2", "r-b", json.dumps(A1), "p1", "2026-10-01T00:00:01Z"),
                   ("ex:b1", "u1:s1", "r-b", json.dumps(A1), "p1", "2026-10-01T00:00:02Z")],
            unit_marks=[("ex:a1", "seen"), ("codh:g1", "seen")],
            favourites=[("user-1", "ex:a1", "2026-10-01T00:00:03Z")],
            assertions=[claim("cf:a", "ex:a1", "has_form"), claim("cf:b", "book:a", "date_produced")],
            assertion_evidence=[("cf:a", "crop", old_ref, None), ("cf:b", "source", "src:1", None)],
            current_claims=[("ex:a1", "has_form", "", "", "accepted", "fm:1", None, "[]", "[]", "[]", old_ref, "r", "t")],
            form_decisions=[(1, "d1", "2026-10-01T00:00:00Z", "u1", "cluster", "あ", None, None, "r",
                             json.dumps(["ex:gone", "codh:g1", "ex:a1"]), "", None, None, None)],
            assertion_premises=[("cf:a", "cf:b", "premise")],
            rejections=[("r-b:s2", "user-1", "wrong book", "2026-10-01T00:00:05Z")],
            corpus_follow=[("codh:g1",)],
            unit_suspects=[("ex:a1", 0.9, "い", "あ", "0,0,10,10"), ("ex:gone", 0.8, "う", "え", None)],
            unit_shapes=[("ex:a2", 3), ("ex:gone", 4)],
            forms=[("fm:1", "rp:1", "u1", "2026-10-01T00:00:00Z")],
            representations=[("rp:1", "unicode", "あ", None, None)],
            ledger_submissions=[("u1:a", "u1", "{}", json.dumps({"submission": "u1:a", "subject": "ex:a1", "replaced": None}),
                                 "2026-10-01T00:00:00Z")])
    sources(root, **{**tables, **extra})
    code, _out = history(monkeypatch, root)
    assert code == 0
    (root / "out" / "media").mkdir(exist_ok=True)


def report(root):
    return max((root / "out").glob("check-*.txt")).read_text()


def test_a_correct_history_translation_passes(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history)
    assert check(monkeypatch) == 0
    text = report(root)
    assert "ok   every events row is translated" in text
    assert "ok   every unit_suspects row is translated into suspects or accounted for (1 crop no longer exists)" in text
    assert "ok   every unit_shapes row is translated into shapes or accounted for (1 crop no longer exists)" in text
    assert "ok   every actor_stats row is translated" in text
    assert "ok   every skips row is translated or accounted for (1 an older skip by the same actor)" in text
    assert "info live marks that differ from those recomputed from seen marks and skips; the live ones move (0)" in text
    assert "skip every marked crop has its mark as its state: the local copy has not had this translation's marks" in text
    assert "skip every actors.user and favourites.user names an account: no accounts.sqlite" in text


@pytest.mark.parametrize(("change", "line"), [
    ("DELETE FROM events WHERE id = 2", "every events row is translated"),
    ("UPDATE seen SET cut = 0 WHERE cut = -1", "every seen row is translated"),
    ("UPDATE assertion_evidence SET cut = 0 WHERE kind = 'crop'", "every assertion_evidence row is translated"),
    ("UPDATE events SET actor = 3 - actor WHERE id = 1", "every events row is translated"),
    ("UPDATE submissions SET actor = 3 - actor WHERE id = 1", "every submissions row is translated"),
    ("UPDATE assertion_premises SET role = 'other'", "every assertion_premises row is translated"),
    ("UPDATE ledger_submissions SET response = json_remove(response, '$.crop')", "every ledger_submissions row is translated"),
    ("DELETE FROM rejections", "every rejections row is translated"),
    ("UPDATE rejections SET at = at + 1", "every rejections row is translated"),
    ("DELETE FROM corpus_follow", "every corpus_follow row is translated"),
    ("UPDATE suspects SET p = 0.1", "every unit_suspects row is translated into suspects"),
    ("DELETE FROM shapes", "every unit_shapes row is translated into shapes"),
    ("UPDATE actors SET via = 'account' WHERE user IS NULL", "every actors row is translated"),
    ("DELETE FROM actors WHERE user IS NULL", "every actors row is translated"),
    ("UPDATE actor_stats SET reviews = reviews + 1 WHERE actor = 1", "every actor_stats row is translated"),
    ("UPDATE actor_stats SET rounds = 0 WHERE actor = 2", "every actor_stats row is translated"),
])
def test_a_wrong_or_missing_translated_row_fails(layout, monkeypatch, change, line):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history)
    db = sqlite3.connect(root / "out" / "history.sqlite")
    db.execute(change)
    db.commit()
    assert check(monkeypatch) == 1
    assert f"FAIL (1) {line}" in report(root)


def test_a_dropped_row_the_report_does_not_list_fails(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history)
    path = root / "out" / "history-report.txt"
    path.write_text(path.read_text().replace("dropped (1) an older skip by the same actor", ""))
    assert check(monkeypatch) == 1
    assert "1 rows an older skip by the same actor, history-report.txt lists 0" in report(root)


def test_marks_that_differ_from_the_live_ones_are_listed_and_pass(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history, unit_marks=[("ex:a1", "seen"), ("codh:g1", "hard"), ("ex:b1", "seen")])
    assert check(monkeypatch) == 0
    text = report(root)
    assert ("info live marks that differ from those recomputed from seen marks and skips; the live ones move (2)\n"
            "     codh:g1: live hard, recomputed seen\n     ex:b1: live seen, recomputed None") in text


def test_a_live_mark_missing_from_history_marks_fails(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history)
    (root / "source-unit_marks.sqlite").unlink()
    write_source(root, "unit_marks", ["id", "mark"], [("ex:a1", "seen"), ("codh:g1", "hard")])
    assert check(monkeypatch) == 1
    assert "codh:g1 (" in report(root) and "live hard, not in history-marks.json" in report(root)


@pytest.mark.parametrize(("users", "code"), [(["user-1"], 0), (["user-2"], 1)])
def test_every_actor_and_favourite_user_names_an_account(layout, monkeypatch, users, code):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history)
    db = sqlite3.connect(root / "accounts.sqlite")
    db.execute('CREATE TABLE "user"(id TEXT PRIMARY KEY)')
    db.executemany('INSERT INTO "user" VALUES(?)', [(u,) for u in users])
    db.commit()
    assert check(monkeypatch) == code


def test_after_the_load_every_marked_crop_that_was_pending_has_its_mark_as_state(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    translated(root, monkeypatch, sources, history)
    load(publish, remote, monkeypatch)
    assert check(monkeypatch) == 0
    # ex:a1 was pending and takes its mark; codh:g1 was checked and keeps its state.
    assert "ok   every marked crop has its mark as its state (1 not pending at the load keep theirs)" in report(root)
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    local.execute("UPDATE crops SET state = 0 WHERE id = ?", (ids(root)["ex:a1"],))
    local.commit()
    assert check(monkeypatch) == 1
    assert f"{ids(root)['ex:a1']}: state pending, marked seen" in report(root)


def test_at_the_cutover_every_skipped_check_fails(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history)
    assert check(monkeypatch) == 0
    assert check(monkeypatch, "--cutover") == 1
    text = report(root)
    assert not [line for line in text.splitlines() if line.startswith("skip ")]
    assert "FAIL (1) every marked crop has its mark as its state" in text
    assert "FAIL (1) every actors.user and favourites.user names an account" in text


def test_at_the_cutover_a_missing_translation_fails(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    (root / "out" / "media").mkdir(exist_ok=True)
    assert check(monkeypatch, "--cutover") == 1
    assert "FAIL (1) history" in report(root)


def test_rows_loaded_with_no_marks_set_fail(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    translated(root, monkeypatch, sources, history)
    load(publish, remote, monkeypatch)
    # The rows went in and the run stopped before the marks.
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    local.execute("DELETE FROM imports WHERE stamp LIKE '%-marks.%'")
    local.commit()
    assert check(monkeypatch) == 1
    assert "the rows of this translation are loaded and its marks are not set" in report(root)


@pytest.mark.parametrize("listed", ["dropped (1) crop no longer exists", ""])
def test_drops_the_report_counts_otherwise_fail(layout, monkeypatch, listed):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history)
    path = root / "out" / "history-report.txt"
    text = path.read_text()
    # unit_shapes lists its one drop as two, or drops none of the rows the mirror lacks.
    head, tail = text.split("unit_shapes:", 1)
    path.write_text(head + "unit_shapes:" + tail.replace("dropped (1) crop no longer exists", listed.replace("(1)", "(2)"), 1))
    assert check(monkeypatch) == 1
    assert f"1 rows crop no longer exists, history-report.txt lists {2 if listed else 0}" in report(root)


def test_a_dropped_fact_on_a_crop_the_mirror_has_fails(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history)
    db = sqlite3.connect(root / "out" / "history.sqlite")
    db.execute("DELETE FROM suspects")
    db.commit()
    assert check(monkeypatch) == 1
    assert "ex:a1" in report(root) and "not translated" in report(root)


def test_reviews_of_an_undone_submission_do_not_count_in_actor_stats(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history,
               submissions=[sub("u1:s1", "u1", "2026-10-01T00:00:02Z"), sub("r-b:s2", "r-b", "2026-10-01T00:00:01Z", undone=1)])
    stats = sqlite3.connect(root / "out" / "history.sqlite").execute(
        "SELECT a.key, s.reviews, s.rounds FROM actor_stats s JOIN actors a ON a.id = s.actor ORDER BY a.key").fetchall()
    assert stats == [("r-b", 0, 0), ("u1", 1, 1)]
    assert check(monkeypatch) == 0


def test_a_glyph_corpus_units_lacks_is_no_corpus_glyph(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history)
    (root / "source-corpus_units.sqlite").unlink()
    write_source(root, "corpus_units", ("id", "character", "family", "visual_group", "shuffle", "object", "offset", "size",
                                        "production", "named", "style", "document", "label"), [])
    assert check(monkeypatch) == 1
    assert "cut None expected, 0 translated" in report(root)


def test_an_event_on_a_tombstone_takes_its_cut_from_the_tombstone_record(layout, monkeypatch):  # noqa: F811
    root, publish, _remote = layout
    sources(root, events=[event("cf:1", "ex:gone", "u1:s1", "u1", 0, "2026-10-01T00:00:02Z")])
    assert history(monkeypatch, root)[0] == 1
    monkeypatch.setattr(sys, "argv", ["publish.py", str(root / "out" / "history-tombstones.sqlite")])
    publish.main()
    assert history(monkeypatch, root)[0] == 0
    (root / "out" / "media").mkdir(exist_ok=True)
    assert check(monkeypatch) == 0
    assert "info tombstones with no record, so no revision to check their events against (1)" in report(root)
    db = sqlite3.connect(root / "out" / "history.sqlite")
    db.execute("UPDATE events SET cut = -1")
    db.commit()
    assert check(monkeypatch) == 1
    assert "cut 0 expected, -1 translated" in report(root)


def test_an_event_past_its_crops_revision_fails(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history)
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    local.execute("UPDATE crops SET revision = 1 WHERE id = ?", (ids(root)["ex:a2"],))
    local.commit()
    assert check(monkeypatch) == 1
    assert f"FAIL (1) no event is past its crop's revision\n     {ids(root)['ex:a2']}: event at revision 2, crop at 1" in report(root)


def bundle_of(root, cid):
    rev = sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT rev FROM pages WHERE id=?", (cid >> 24,)).fetchone()[0]
    return root / "out" / "pages" / f"{cid >> 24}-{rev}.json.gz"


def test_every_cut_minus_one_a_row_cites_is_in_its_bundle_with_its_earlier_box(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    translated(root, monkeypatch, sources, history)
    load(publish, remote, monkeypatch)
    assert check(monkeypatch) == 0
    assert "skip every crop cited at cut -1 has that cut in its bundle with its earlier box (1 crops)" in report(root)
    monkeypatch.setattr(sys, "argv", ["publish.py", str(root / "out" / "history-cuts.sqlite")])
    publish.main()
    assert check(monkeypatch) == 0
    assert "ok   every crop cited at cut -1 has that cut in its bundle with its earlier box (1 crops)" in report(root)
    path = bundle_of(root, ids(root)["ex:a1"])
    body = json.loads(gzip.decompress(path.read_bytes()))
    for e in body["crops"]:
        for c in e.get("cuts", []):
            c["box"] = {"x": 6, "y": 5, "w": 10, "h": 10}
    path.write_bytes(gzip.compress(json.dumps(body).encode()))
    assert check(monkeypatch) == 1
    assert "ex:a1" in report(root) and "bundle cut -1 [(6, 5, 10, 10)]" in report(root)


def accounts_copy(root, sessions):
    db = sqlite3.connect(root / "accounts.sqlite")
    db.executescript('''CREATE TABLE "user"(id TEXT PRIMARY KEY);
        CREATE TABLE "session"(id TEXT PRIMARY KEY, userId TEXT NOT NULL REFERENCES "user"(id));
        CREATE TABLE verification(id TEXT PRIMARY KEY);
        CREATE TABLE rateLimit(id TEXT PRIMARY KEY);
        INSERT INTO "user" VALUES('user-1');''')
    db.executemany('INSERT INTO "session" VALUES(?, \'user-1\')', [(s,) for s in sessions])
    db.commit()
    db.close()


def accounts_load(root, monkeypatch):
    sys.modules.pop("accounts", None)
    accounts = importlib.import_module("accounts")
    monkeypatch.setattr(accounts, "COPY", root / "accounts.sqlite")
    monkeypatch.setattr(accounts, "LOADS", root / "accounts-load")
    accounts.load(False)


def test_every_session_is_in_the_accounts_load(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history)
    accounts_copy(root, ["s1", "s2"])
    assert check(monkeypatch) == 0
    assert "skip the parts loaded into glyph-atlas-accounts rebuild accounts.sqlite" in report(root)
    accounts_load(root, monkeypatch)
    assert check(monkeypatch) == 0
    assert "ok   the parts loaded into glyph-atlas-accounts rebuild accounts.sqlite, every session among them (2 sessions)" in report(root)
    # A session the parts lack: drop it from the part that inserts it.
    for part in (root / "accounts-load").glob("*/*.sql"):
        part.write_text(part.read_text().replace("\nINSERT INTO \"session\"(\"id\",\"userId\") VALUES('s2','user-1');", ""))
    assert check(monkeypatch) == 1
    assert "session: 2 rows here, 1 there" in report(root)


def accounts_remote(root, remote):
    """The old database holding the copy's tables, and glyph-atlas-accounts loaded from its parts."""
    remote.dbs["glyph-atlas"] = sqlite3.connect(":memory:")
    sqlite3.connect(root / "accounts.sqlite").backup(remote.dbs["glyph-atlas"])
    remote.dbs["glyph-atlas-accounts"] = target = sqlite3.connect(":memory:")
    for part in sorted((root / "accounts-load").glob("*/*.sql")):
        target.executescript("BEGIN;\n" + part.read_text() + "COMMIT;\n")


@pytest.mark.parametrize(("database", "change", "line"), [
    (None, None, None),
    ("glyph-atlas", "INSERT INTO \"session\" VALUES('s3', 'user-1')",
     "FAIL (1) accounts.sqlite equals the auth tables of glyph-atlas, schema, row count and checksum\n     session: 2 rows here, 3 there"),
    ("glyph-atlas-accounts", "DELETE FROM \"session\" WHERE id = 's2'",
     "FAIL (1) glyph-atlas-accounts equals accounts.sqlite, schema, row count and checksum\n     session: 2 rows here, 1 there"),
])
def test_at_the_cutover_the_accounts_chain_is_compared_remotely(layout, monkeypatch, database, change, line):  # noqa: F811
    root, _publish, remote = layout
    translated(root, monkeypatch, sources, history)
    accounts_copy(root, ["s1", "s2"])
    accounts_load(root, monkeypatch)
    accounts_remote(root, remote)
    if change:
        remote.dbs[database].execute(change)
    assert check(monkeypatch, "--cutover") == 1
    text = report(root)
    if line:
        assert line in text
    else:
        assert "ok   accounts.sqlite equals the auth tables of glyph-atlas" in text
        assert "ok   glyph-atlas-accounts equals accounts.sqlite" in text


@pytest.mark.parametrize(("change", "line"), [
    ("UPDATE submissions SET request = '{\"x\":1}' WHERE id = 1", "submissions (1,): differs"),
    ("DELETE FROM seen WHERE cut = -1", "not in the local copy"),
    ("INSERT INTO favourites VALUES('user-9', 1, 0)", "in the local copy, not in history.sqlite"),
])
def test_the_loaded_copy_equals_the_translation_row_for_row(layout, monkeypatch, change, line):  # noqa: F811
    root, publish, remote = layout
    translated(root, monkeypatch, sources, history)
    load(publish, remote, monkeypatch)
    assert check(monkeypatch) == 0
    assert "ok   the local copy holds every translated row as history.sqlite has it, and no other" in report(root)
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    local.execute(change)
    local.commit()
    assert check(monkeypatch) == 1
    assert line in report(root)


@pytest.mark.parametrize(("change", "line"), [
    ("UPDATE events SET actor = 1 WHERE id > 2", "actor 'u1' expected, 'r-b' set"),
    ("UPDATE events SET at = at + 1 WHERE id > 2", "at 1790812802000 expected, 1790812802001 set"),
    ("UPDATE events SET cut = -1 WHERE id > 2", "cut 0 expected, -1 set"),
    ("INSERT INTO events(crop, revision, cut, gen, actor, submission, at, kind, state) VALUES(1, 99, 0, 0, 1, 1, 0, 3, 3)",
     "past the translation's events and sets no live mark"),
])
def test_a_mark_event_names_the_latest_round_that_showed_the_crop(layout, monkeypatch, change, line):  # noqa: F811
    root, publish, remote = layout
    translated(root, monkeypatch, sources, history)
    load(publish, remote, monkeypatch)
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    local.execute(change)
    local.commit()
    assert check(monkeypatch) == 1
    assert line in report(root)


@pytest.mark.parametrize(("change", "line"), [
    (None, None),
    ("DELETE FROM imports WHERE stamp LIKE '%-marks.%'", "-marks.marks-"),
    ("UPDATE seen SET at = at + 1", "FAIL (1) glyph-atlas-v3 holds every loaded table as the local copy does"),
    ("DELETE FROM favourites", "favourites: 1 rows here, 0 there"),
    ("UPDATE crops SET revision = revision + 1 WHERE id = {a1}", "FAIL (1) glyph-atlas-v3 gives each marked crop"),
])
def test_at_the_cutover_glyph_atlas_v3_is_compared_with_the_local_copy(layout, monkeypatch, capsys, change, line):  # noqa: F811
    root, publish, remote = layout
    translated(root, monkeypatch, sources, history)
    load(publish, remote, monkeypatch)
    if change:
        remote.dbs["glyph-atlas-v3"].execute(change.format(a1=ids(root)["ex:a1"]))
        remote.dbs["glyph-atlas-v3"].commit()
    capsys.readouterr()
    # The cuts are not published and there is no accounts copy, so the cutover run fails either way.
    assert check(monkeypatch, "--cutover") == 1
    text = report(root)
    if line:
        assert line in text
    else:
        assert "glyph-atlas-v3: about" in capsys.readouterr().out
        assert "ok   glyph-atlas-v3 holds the " in text
        assert "ok   glyph-atlas-v3 holds every loaded table as the local copy does" in text
        assert "ok   glyph-atlas-v3 gives each marked crop the local copy's state and revision (checked 1, seen 1)" in text


def test_without_cutover_nothing_remote_is_read(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    translated(root, monkeypatch, sources, history)
    load(publish, remote, monkeypatch)
    accounts_copy(root, ["s1"])
    accounts_load(root, monkeypatch)

    def refuse(database, sql):
        raise AssertionError(f"read {database}")
    monkeypatch.setattr(publish, "query", refuse)
    assert check(monkeypatch) == 0


def test_at_the_cutover_the_count_of_marks_left_must_be_recorded(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    translated(root, monkeypatch, sources, history)
    load(publish, remote, monkeypatch)
    for left in (root / "out" / "history-load").glob("*-marks/left"):
        left.unlink()
    assert check(monkeypatch) == 0
    check(monkeypatch, "--cutover")
    assert "FAIL (1) every marked crop has its mark as its state" in report(root)
    assert "-marks/left, the count of marks the load left" in report(root)


def test_tied_rounds_mark_the_crop_by_the_highest_submission_id(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    # r-c:s3 and u1:s1 showed ex:a1 at one time; u1:s1 is numbered after r-c:s3 and is the one.
    translated(root, monkeypatch, sources, history,
               submissions=[sub("u1:s1", "u1", "2026-10-01T00:00:02Z"), sub("r-b:s2", "r-b", "2026-10-01T00:00:01Z"),
                            sub("r-c:s3", "r-c", "2026-10-01T00:00:02Z")],
               seen=[("ex:a1", "r-c:s3", json.dumps(A1), "p1", "2026-10-01T00:00:02Z"),
                     ("ex:a1", "u1:s1", json.dumps(A1), "p1", "2026-10-01T00:00:02Z"),
                     ("codh:g1", "u1:s1", json.dumps(OLD), "x", "2026-10-01T00:00:02Z")])
    db = sqlite3.connect(root / "out" / "history.sqlite")
    u1 = db.execute("SELECT id FROM actors WHERE key='u1'").fetchone()[0]
    s1 = db.execute("SELECT id FROM submissions WHERE key='u1:s1'").fetchone()[0]
    assert s1 > db.execute("SELECT id FROM submissions WHERE key='r-c:s3'").fetchone()[0]
    marks = {m["crop"]: m for m in json.loads((root / "out" / "history-marks.json").read_text())}
    assert marks[ids(root)["ex:a1"]]["actor"] == u1
    load(publish, remote, monkeypatch)
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert local.execute("SELECT actor, submission FROM events WHERE crop = ? ORDER BY id DESC LIMIT 1",
                         (ids(root)["ex:a1"],)).fetchone() == (u1, s1)
    assert check(monkeypatch) == 0
    # The other tied round fails.
    rc = db.execute("SELECT id, actor FROM submissions WHERE key='r-c:s3'").fetchone()
    local.execute("UPDATE events SET submission = ?, actor = ? WHERE crop = ? AND kind = 3", (*rc, ids(root)["ex:a1"]))
    local.commit()
    assert check(monkeypatch) == 1
    assert "submission 'u1:s1' expected, 'r-c:s3' set" in report(root)


def test_a_mark_event_is_no_review_in_actor_stats(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    box = {"x": 0, "y": 20, "w": 10, "h": 10}
    translated(root, monkeypatch, sources, history,
               events=[event("cf:1", "ex:a2", "r-b:s2", "r-b", 0, "2026-10-01T00:00:01Z", box),
                       event("cf:2", "ex:a2", "u1:s1", "u1", 1, "2026-10-01T00:00:02Z", box, verdict="seen")])
    stats = sqlite3.connect(root / "out" / "history.sqlite").execute(
        "SELECT a.key, s.reviews FROM actor_stats s JOIN actors a ON a.id = s.actor ORDER BY a.key").fetchall()
    assert stats == [("r-b", 1), ("u1", 0)]
    assert check(monkeypatch) == 0


@pytest.mark.parametrize(("change", "line"), [
    ("DELETE FROM representations", "every representations row is translated"),
    ("UPDATE representations SET value = 'い'", "every representations row is translated"),
    ("UPDATE forms SET anchor = 'rp:none'", "every form's anchor names a representation"),
])
def test_a_representation_must_move_as_it_is_and_name_every_anchor(layout, monkeypatch, change, line):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history)
    db = sqlite3.connect(root / "out" / "history.sqlite")
    # Representations are never changed in the site's database; here a wrong translation is staged.
    db.executescript(f"DROP TRIGGER representation_kept; DROP TRIGGER representation_kept_delete; {change};")
    assert check(monkeypatch) == 1
    assert f"FAIL (1) {line}" in report(root)


def test_a_null_key_in_the_local_copy_is_reported_not_raised(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    translated(root, monkeypatch, sources, history)
    load(publish, remote, monkeypatch)
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    local.execute("INSERT INTO forms VALUES(NULL, NULL, 'x', 'y')")
    local.commit()
    assert check(monkeypatch) == 1
    assert "forms (None,): in the local copy, not in history.sqlite" in report(root)


def test_a_tombstones_current_box_is_its_retired_bundle_entrys(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    sources(root)
    sys.modules.pop("check", None)
    module = importlib.import_module("check")
    entries = {"ex:gone": {"retired": True, "box": {"x": 1, "y": 2, "w": 3, "h": 4}}}
    cuts = module.Cuts(entries.get)
    cuts.want(["ex:gone", "ex:a1"])
    cuts.load()
    assert cuts.current("ex:gone") == (1, 2, 3, 4)
    assert cuts.current("ex:a1") == (0, 0, 10, 10)
    assert cuts.current("ex:nothing") is None
    assert cuts.cut("ex:gone", {"x": 1, "y": 2, "w": 3, "h": 4}) == 0
