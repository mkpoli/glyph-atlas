"""The export reads a frozen copy of the dataset, which holds everything the crop records are built from."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

from glyph_atlas import ainu_geometry

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS / "export_cloudflare.py"
sys.path.insert(0, str(SCRIPTS))


def load():
    spec = importlib.util.spec_from_file_location("export_cloudflare", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_frozen_copy_carries_the_crop_geometry_flags(tmp_path):
    dataset, frozen = tmp_path / "dataset", tmp_path / "frozen"
    dataset.mkdir(); frozen.mkdir()
    (dataset / "units.parquet").write_bytes(b"units")
    mark = {"flags": ["sparse-ink"], "box": {"x": 1, "y": 2, "w": 3, "h": 4}}
    (dataset / ainu_geometry.FILE).write_text(json.dumps({"method": ainu_geometry.METHOD, "units": {"ar:a:1": mark}}))
    load().freeze(dataset, frozen)
    assert (frozen / "units.parquet").read_bytes() == b"units"
    assert ainu_geometry.load(frozen) == {"ar:a:1": mark}


def test_a_copy_of_chosen_pages_holds_their_units_and_reviews_alone(tmp_path):
    dataset, frozen = tmp_path / "dataset", tmp_path / "frozen"
    dataset.mkdir(); frozen.mkdir()
    with sqlite3.connect(dataset / "review.sqlite") as db:
        db.executescript("""
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, target_id TEXT NOT NULL);
            CREATE INDEX events_target ON events (target_id);
            CREATE TABLE revisions (target_id TEXT PRIMARY KEY, revision INTEGER NOT NULL);
            CREATE TABLE revision_bases (target_id TEXT PRIMARY KEY, base INTEGER NOT NULL);
            CREATE TABLE lines (id TEXT PRIMARY KEY, document_id TEXT, page_id TEXT, seq INTEGER, data TEXT NOT NULL);
            CREATE TABLE units (id TEXT PRIMARY KEY, document_id TEXT, page_id TEXT, line_id TEXT, seq INTEGER,
                                active INTEGER NOT NULL, data TEXT NOT NULL);
            CREATE INDEX units_page ON units (page_id);
            CREATE TABLE imported_records (table_name TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
                                           PRIMARY KEY (table_name, id));
            CREATE TABLE forms (id TEXT PRIMARY KEY);
            INSERT INTO meta VALUES ('loaded', '1');
            INSERT INTO forms VALUES ('f');
        """)
        for page, document in (("p1", "d1"), ("p2", "d2")):
            db.execute("INSERT INTO imported_records VALUES ('documents', ?, '{}')", (document,))
            db.execute("INSERT INTO imported_records VALUES ('pages', ?, ?)", (page, json.dumps({"document_id": document})))
            db.execute("INSERT INTO lines VALUES (?, ?, ?, 0, '{}')", (f"{page}:l", document, page))
            db.execute("INSERT INTO imported_records VALUES ('lines', ?, '{}')", (f"{page}:l",))
            for n in (1, 2):
                unit = f"{page}:u{n}"
                db.execute("INSERT INTO units VALUES (?, ?, ?, ?, ?, 1, '{}')", (unit, document, page, f"{page}:l", n))
                db.execute("INSERT INTO imported_records VALUES ('units', ?, '{}')", (unit,))
                db.execute("INSERT INTO events (id, target_id) VALUES (?, ?)", (f"e:{unit}", unit))
                db.execute("INSERT INTO revisions VALUES (?, 1)", (unit,))
                db.execute("INSERT INTO revision_bases VALUES (?, 3)", (unit,))
    load().freeze(dataset, frozen, {"p2"})
    with sqlite3.connect(frozen / "review.sqlite") as db:
        column = lambda sql: sorted(r[0] for r in db.execute(sql))
        assert column("SELECT id FROM units") == ["p2:u1", "p2:u2"]
        assert column("SELECT id FROM lines") == ["p2:l"]
        assert column("SELECT target_id FROM events") == ["p2:u1", "p2:u2"]
        assert column("SELECT target_id FROM revisions") == ["p2:u1", "p2:u2"]
        assert column("SELECT target_id FROM revision_bases") == ["p2:u1", "p2:u2"]
        assert column("SELECT table_name || ' ' || id FROM imported_records") == [
            "documents d2", "lines p2:l", "pages p2", "units p2:u1", "units p2:u2"]
        assert column("SELECT value FROM meta") == ["1"] and column("SELECT id FROM forms") == ["f"]
        assert column("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL") == ["events_target", "units_page"]
