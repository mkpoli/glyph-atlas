import importlib
import re
import sqlite3
import sys
from pathlib import Path

from test_atlas3_publish import layout, write_source  # noqa: F401 -- the fixture

INDEXES = Path(__file__).resolve().parents[1] / "apps/cloudflare/migrations-atlas/indexes-cutover.sql"
CUTOVER = re.findall(r"^CREATE INDEX (\w+)", INDEXES.read_text(), re.M)


def fill(publish, remote, monkeypatch, *flags):
    sys.modules.pop("fill", None)
    module = importlib.import_module("fill")
    monkeypatch.setattr(module, "send", remote.send)
    monkeypatch.setattr(module, "applied", lambda db, stamp: bool(remote.query(db, f"SELECT 1 FROM imports WHERE stamp='{stamp}'")))
    monkeypatch.setattr(sys, "argv", ["fill.py", *flags])
    module.main()


def unfilled(root, remote):
    """The layout as loaded before the cutover: no family column and none of its indexes."""
    for db in (sqlite3.connect(root / "out" / "atlas.sqlite"), remote.dbs["glyph-atlas-v3"]):
        db.executescript("".join(f"DROP INDEX {name};" for name in CUTOVER) + "ALTER TABLE crops DROP COLUMN family;")
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
    assert (root / "out" / "fill-report.txt").read_text().startswith("family: 0 crops change\ncrop rows written: 0\nindexes to build: none")
