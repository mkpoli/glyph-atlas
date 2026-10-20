import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

from test_dates import make

from glyph_atlas import dates

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("export_dates_cloudflare", ROOT / "scripts" / "export_dates_cloudflare.py")
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


def write(tmp_path, claims, production="printed"):
    (tmp_path / "claims.jsonl").write_text("".join(c.model_dump_json() + "\n" for c in claims))
    found = dates.resolve(claims, production)
    (tmp_path / "resolved.jsonl").write_text(json.dumps(
        {"document": "d", **{axis: r.as_dict() for axis, r in found.items()}}, ensure_ascii=False) + "\n")


def test_dates_become_ledger_claims_with_their_source_as_evidence(tmp_path):
    claim = make("printed", 1659, text="万治２")
    write(tmp_path, [claim])
    path = tmp_path / "book-facts-input.sqlite"
    counts = export.export(tmp_path, path, stamp="2026-10-03T00:00:00Z")
    assert counts == {"claims": 1, "documents": 1, "dated_axes": 1, "slots": 1, "stamp": "2026-10-03T00:00:00Z"}
    db = sqlite3.connect(path)
    identity, subject, predicate, value, tier, by, kind, ref, locator = db.execute("SELECT * FROM date_claims").fetchone()
    assert (identity, subject, predicate, tier, by) == (claim.id, "d", "date_printed", "attested", "source:kokusho")
    assert (kind, ref, locator) == ("source", "kokusho", "kokusho#printed")
    assert json.loads(value)["text"] == "万治２" and json.loads(value)["of"] == "witness"
    assert db.execute("SELECT label, status, kind, uncertain, resolver FROM document_dating").fetchall() == [
        ("1659", "single", "printed", 0, dates.RESOLVER)]
    assert json.loads(db.execute("SELECT claims FROM document_dating").fetchone()[0])
    assert dict(db.execute("SELECT name, at FROM written")) == {
        "date_claims": "2026-10-03T00:00:00Z", "document_dating": "2026-10-03T00:00:00Z"}


def test_an_export_replaces_the_one_before(tmp_path):
    old, kept = make("printed", 1659, text="万治２"), make("copied", 1700)
    write(tmp_path, [old, kept])
    path = tmp_path / "book-facts-input.sqlite"
    export.export(tmp_path, path, stamp="2026-10-03T00:00:00Z")
    write(tmp_path, [kept], production="handwritten")
    export.export(tmp_path, path, stamp="2026-10-04T00:00:00Z")
    db = sqlite3.connect(path)
    assert db.execute("SELECT id FROM date_claims").fetchall() == [(kept.id,)]
    assert db.execute("SELECT kind FROM document_dating").fetchall() == [("copied",)]
    assert db.execute("SELECT at FROM written WHERE name='date_claims'").fetchone() == ("2026-10-04T00:00:00Z",)
