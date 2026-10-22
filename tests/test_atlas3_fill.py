import importlib
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest
from test_atlas3_publish import ids, layout, run, write_source  # noqa: F401 -- the fixture

INDEXES = Path(__file__).resolve().parents[1] / "apps/cloudflare/migrations-atlas/indexes-cutover.sql"
CUTOVER = re.findall(r"^CREATE INDEX (\w+)", INDEXES.read_text(), re.MULTILINE)


def fill(publish, remote, monkeypatch, *flags):
    sys.modules.pop("fill", None)
    module = importlib.import_module("fill")
    monkeypatch.setattr(importlib.import_module("load_history"), "send", remote.send)
    monkeypatch.setattr(module, "send", remote.send)
    monkeypatch.setattr(module, "applied", lambda db, stamp: bool(remote.query(db, f"SELECT 1 FROM imports WHERE stamp='{stamp}'")))
    monkeypatch.setattr(sys, "argv", ["fill.py", *flags])
    module.main()


def unfilled(root, remote):
    """The layout as loaded before the cutover: no family column and none of its indexes."""
    for db in (sqlite3.connect(root / "out" / "atlas.sqlite"), remote.dbs["glyph-atlas-v3"]):
        db.executescript("".join(f"DROP INDEX {name};" for name in CUTOVER) + "ALTER TABLE crops DROP COLUMN family; ALTER TABLE crops DROP COLUMN priority;")
        db.commit()


def test_the_fill_writes_each_crop_once_then_builds_the_indexes_and_a_rerun_sends_nothing(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    (tmp_path / "source-corpus_units.sqlite").unlink()
    write_source(tmp_path, "corpus_units", ("id", "character", "family", "visual_group", "shuffle", "object", "offset", "size",
                                            "production", "named", "style", "document", "label"),
                 [("codh:g1", None, "U+304B", None, 7, "corpus-0001.bin", 0, 100, "printed", 0, "unassessed", "codh:b", "か")])
    # The layout's corpus glyph had a character from its review overlay; the overlay now names none.
    units = sqlite3.connect(tmp_path / "source-units.sqlite")
    units.execute("UPDATE units SET character=NULL, family='U+304B' WHERE id='codh:g1'")
    units.commit()
    unfilled(root, remote)
    fill(publish, remote, monkeypatch)
    # A dry run changes nothing.
    assert "family" not in {r[1] for r in remote.dbs["glyph-atlas-v3"].execute("PRAGMA table_info(crops)")}
    fill(publish, remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    g1 = sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT id FROM ids WHERE text='codh:g1'").fetchone()[0]
    ka = db.execute("SELECT id FROM graphemes WHERE text='か'").fetchone()[0]
    assert db.execute("SELECT family FROM crops WHERE id=?", (g1,)).fetchone()[0] == ka
    # The other crops' records name no family.
    assert db.execute("SELECT count(*) FROM crops WHERE family IS NOT NULL").fetchone()[0] == 1
    # The rows went before every index, and every index was built.
    run = max((root / "out" / "fill-load").glob("fill-*"))
    sent = "".join(p.read_text() for p in sorted(run.glob("*.sql")))
    assert sent.rindex("UPDATE crops") < min(sent.index(f"CREATE INDEX {name} ") for name in CUTOVER)
    for target in (db, sqlite3.connect(root / "out" / "atlas.sqlite")):
        assert set(CUTOVER) <= {r[0] for r in target.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    before = len(list((root / "out" / "fill-load").glob("fill-*")))
    fill(publish, remote, monkeypatch, "--send")
    assert len(list((root / "out" / "fill-load").glob("fill-*"))) == before
    assert (root / "out" / "fill-report.txt").read_text().startswith("family: 0 crops change\npriority: 0 crops change\ncluster: 0 crops change\ncrop rows written: 0\nindexes to build: none")


def deferred(root, remote, monkeypatch):
    from test_atlas3_crop_facts import decisions, facts, glyph_decided, sources
    glyph_decided(root, remote)
    decisions(root, remote, (1, "d1", "2025-01-02", "U+304B:c1"))
    sources(root)
    unfilled(root, remote)
    units = sqlite3.connect(root / "source-units.sqlite")
    units.execute("UPDATE units SET family='U+304B',priority=0 WHERE id='ex:a1'")
    units.commit()
    facts(root, remote, monkeypatch, "--send", "--defer-crops")
    return sqlite3.connect(root / "out" / "mirror.sqlite")


def test_deferred_facts_fill_all_columns_once_and_unlock_only_at_completion(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    mirror = deferred(root, remote, monkeypatch)
    db = remote.dbs["glyph-atlas-v3"]
    assert db.execute("SELECT count(*) FROM crops WHERE cluster IS NOT NULL").fetchone()[0] == 0
    assert db.execute("SELECT loading FROM form_state").fetchone()[0] == 1
    assert mirror.execute("SELECT run FROM pending").fetchone()[0].startswith("fill/source/")
    from test_atlas3_crop_facts import facts
    with pytest.raises(SystemExit, match="fill.py"):
        facts(root, remote, monkeypatch, "--send")
    with pytest.raises(SystemExit, match="fill.py"):
        run(publish, monkeypatch, None)
    fill(publish, remote, monkeypatch, "--send")
    a1 = ids(root)["ex:a1"]
    family, priority, cluster = db.execute("SELECT family,priority,cluster FROM crops WHERE id=?", (a1,)).fetchone()
    assert family is not None and priority == 0 and cluster is not None
    plans = list((root / "out" / "fill-load").glob("fill-*"))
    sent = "".join(part.read_text() for part in sorted(plans[0].glob("*.sql")))
    assert len(re.findall(rf"UPDATE crops SET [^;]*WHERE id={a1};", sent)) == 1
    assert sent.index("SET loading=0") > sent.rindex("CREATE INDEX")
    assert db.execute("SELECT loading FROM form_state").fetchone()[0] == 0
    assert mirror.execute("SELECT count(*) FROM pending").fetchone()[0] == 0
    fill(publish, remote, monkeypatch, "--send")
    assert len(list((root / "out" / "fill-load").glob("fill-*"))) == 1


@pytest.mark.parametrize("damage", ["missing", "changed", "duplicate"])
def test_invalid_cluster_lists_stop_before_any_schema_or_crop_write(layout, monkeypatch, damage):  # noqa: F811
    root, publish, remote = layout
    deferred(root, remote, monkeypatch)
    db = remote.dbs["glyph-atlas-v3"]
    name = db.execute("SELECT list FROM form_clusters LIMIT 1").fetchone()[0]
    path = root / "out" / "forms" / f"{name}.json.gz"
    if damage == "missing":
        path.unlink()
    else:
        import gzip
        import hashlib
        body = json.loads(gzip.decompress(path.read_bytes()))
        if damage == "changed":
            body["key"] = "wrong"
        else:
            body["members"][1] = body["members"][0]
        raw = json.dumps(body).encode()
        # Also update the hash, to exercise identity/duplicate checks rather than just corruption.
        changed = name.split("-")[0] + "-" + hashlib.sha256(raw).hexdigest()[:12]
        (path.parent / f"{changed}.json.gz").write_bytes(gzip.compress(raw))
        for target in (db, sqlite3.connect(root / "out" / "atlas.sqlite")):
            target.execute("UPDATE form_clusters SET list=? WHERE list=?", (changed, name))
            target.commit()
    with pytest.raises(SystemExit, match="cluster list"):
        fill(publish, remote, monkeypatch, "--send")
    assert "family" not in {r[1] for r in db.execute("PRAGMA table_info(crops)")}
    assert db.execute("SELECT loading FROM form_state").fetchone()[0] == 1


def test_fill_resumes_the_same_plan_after_remote_index_commit_before_local_replay(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    mirror = deferred(root, remote, monkeypatch)
    real = remote.send
    monkeypatch.setattr(publish, "PART_BYTES", 1)
    stopped = False

    def interrupted(database, path, stamp):
        nonlocal stopped
        result = real(database, path, stamp)
        if not stopped and "CREATE INDEX" in path.read_text():
            stopped = True
            raise RuntimeError("lost response after index commit")
        return result

    monkeypatch.setattr(remote, "send", interrupted)
    with pytest.raises(RuntimeError, match="lost response"):
        fill(publish, remote, monkeypatch, "--send")
    plan = mirror.execute("SELECT run FROM pending").fetchone()[0]
    assert plan.startswith("fill/plan/")
    assert remote.dbs["glyph-atlas-v3"].execute("SELECT loading FROM form_state").fetchone()[0] == 1
    # Resume must neither inspect new source state nor produce new stamps for remaining indexes.
    (root / "source-units.sqlite").unlink()
    fill(publish, remote, monkeypatch, "--send")
    assert len(list((root / "out" / "fill-load").glob("fill-*"))) == 1
    for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        assert db.execute("SELECT loading FROM form_state").fetchone()[0] == 0
        assert set(CUTOVER) <= {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert not mirror.execute("SELECT 1 FROM pending").fetchone()


def test_a_deferred_run_with_no_crop_changes_still_releases_forms(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    deferred(root, remote, monkeypatch)
    fill(publish, remote, monkeypatch, "--send")
    from test_atlas3_crop_facts import facts
    facts(root, remote, monkeypatch, "--send", "--defer-crops")
    assert remote.dbs["glyph-atlas-v3"].execute("SELECT loading FROM form_state").fetchone()[0] == 1
    fill(publish, remote, monkeypatch, "--send")
    assert "crop rows written: 0" in (root / "out" / "fill-report.txt").read_text()
    assert remote.dbs["glyph-atlas-v3"].execute("SELECT loading FROM form_state").fetchone()[0] == 0
    facts(root, remote, monkeypatch, "--send", "--defer-crops")
    assert not sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT 1 FROM pending").fetchone()


@pytest.mark.parametrize("damage", ["missing", "changed", "wrong-build"])
def test_saved_fill_checks_plan_integrity_and_database_ownership_before_resuming(layout, monkeypatch, damage):  # noqa: F811
    root, publish, remote = layout
    mirror = deferred(root, remote, monkeypatch)
    real = remote.send

    def interrupted(database, path, stamp):
        if "UPDATE crops" in path.read_text():
            raise RuntimeError("disconnected before rows")
        return real(database, path, stamp)

    monkeypatch.setattr(remote, "send", interrupted)
    with pytest.raises(RuntimeError):
        fill(publish, remote, monkeypatch, "--send")
    marker = mirror.execute("SELECT run FROM pending").fetchone()[0]
    folder = root / "out" / "fill-load" / marker.removeprefix("fill/plan/")
    part = next(folder.glob("rows-*.sql"))
    if damage == "missing":
        part.unlink()
    elif damage == "changed":
        part.write_text(part.read_text() + "-- changed\n")
    else:
        db = remote.dbs["glyph-atlas-v3"]
        db.execute("DELETE FROM imports WHERE stamp LIKE 'build:%'")
        db.commit()
    monkeypatch.setattr(remote, "send", real)
    with pytest.raises(SystemExit, match="not loaded from build" if damage == "wrong-build" else "incomplete or changed"):
        fill(publish, remote, monkeypatch, "--send")
    assert mirror.execute("SELECT run FROM pending").fetchone()[0] == marker
    assert remote.dbs["glyph-atlas-v3"].execute("SELECT loading FROM form_state").fetchone()[0] == 1


def test_an_index_on_a_table_a_later_load_creates_waits_for_the_next_fill(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    unfilled(root, remote)
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    schema = local.execute("SELECT sql FROM sqlite_master WHERE name='book_dates'").fetchone()[0]
    for db in (local, remote.dbs["glyph-atlas-v3"]):
        db.execute("DROP TABLE book_dates")
        db.commit()
    fill(publish, remote, monkeypatch, "--send")
    assert "indexes waiting for their table: book_date_axis on book_dates" in (root / "out" / "fill-report.txt").read_text()
    assert set(CUTOVER) - {"book_date_axis"} <= {r[0] for r in local.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    # books.py creates the table; the next fill builds the index alone.
    for db in (local, remote.dbs["glyph-atlas-v3"]):
        db.execute(schema)
        db.commit()
    fill(publish, remote, monkeypatch, "--send")
    report = (root / "out" / "fill-report.txt").read_text()
    assert "crop rows written: 0\nindexes to build: book_date_axis\n" in report
    for db in (local, remote.dbs["glyph-atlas-v3"]):
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='book_date_axis'").fetchone()
