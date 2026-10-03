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


def site():
    """A database with the tables the parts write to, as the migrations make them."""
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);\n"
                     "CREATE TABLE units(id TEXT PRIMARY KEY, data TEXT, crop_version TEXT);\n")
    for name in ("0048_assertion_ledger.sql", "0052_document_dates.sql"):
        db.executescript((ROOT / "apps/cloudflare/migrations" / name).read_text())
    return db


def write(tmp_path, claims, production="printed"):
    (tmp_path / "claims.jsonl").write_text("".join(c.model_dump_json() + "\n" for c in claims))
    found = dates.resolve(claims, production)
    (tmp_path / "resolved.jsonl").write_text(json.dumps(
        {"document": "d", **{axis: r.as_dict() for axis, r in found.items()}}, ensure_ascii=False) + "\n")


def apply(db, tmp_path, stamp):
    groups, counts = export.statements(tmp_path, stamp)
    for group in groups:
        db.executescript("".join(group))
    return counts


def test_dates_become_ledger_claims_with_their_source_as_evidence(tmp_path):
    claim = make("printed", 1659, text="万治２")
    write(tmp_path, [claim])
    db = site()
    counts = apply(db, tmp_path, "2026-10-03T00:00:00Z")
    assert counts == {"claims": 1, "documents": 1, "dated_axes": 1, "slots": 1}
    subject, predicate, tier, by = db.execute("SELECT subject,predicate,tier,asserted_by FROM assertions").fetchone()
    assert (subject, predicate, tier, by) == ("d", "date_printed", "attested", "source:kokusho")
    assert db.execute("SELECT kind,ref,locator FROM assertion_evidence").fetchall() == [("source", "kokusho", "kokusho#printed")]
    status, value = db.execute("SELECT status,value FROM current_claims").fetchone()
    assert status == "asserted" and json.loads(value)["text"] == "万治２" and json.loads(value)["of"] == "witness"
    assert db.execute("SELECT label,status,kind FROM document_dating").fetchall() == [("1659", "single", "printed")]
    assert db.execute("SELECT value FROM metadata WHERE key='dates_at'").fetchone() == ('"2026-10-03T00:00:00Z"',)
    assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='date_export'").fetchone() == (0,)


def test_a_date_no_source_states_any_more_is_retracted_and_a_rerun_changes_nothing(tmp_path):
    old, kept = make("printed", 1659, text="万治２"), make("copied", 1700)
    write(tmp_path, [old, kept])
    db = site()
    apply(db, tmp_path, "2026-10-03T00:00:00Z")
    write(tmp_path, [kept], production="handwritten")
    apply(db, tmp_path, "2026-10-04T00:00:00Z")
    apply(db, tmp_path, "2026-10-04T00:00:00Z")
    assert db.execute("SELECT assertion,action,actor FROM assertion_actions").fetchall() == [(old.id, "retract", "source:kokusho")]
    assert db.execute("SELECT predicate FROM current_claims ORDER BY 1").fetchall() == [("date_copied",)]
    assert db.execute("SELECT kind,export FROM document_dating").fetchall() == [("copied", "2026-10-04T00:00:00Z")]


def test_a_reading_that_changes_and_a_claim_that_returns_are_asserted_anew(tmp_path):
    first = make("printed", 1659, text="万治２")
    db = site()
    write(tmp_path, [first])
    apply(db, tmp_path, "2026-10-03T00:00:00Z")
    changed = make("printed", 1660, text="万治２")
    write(tmp_path, [changed])
    apply(db, tmp_path, "2026-10-04T00:00:00Z")
    assert json.loads(db.execute("SELECT value FROM current_claims").fetchone()[0])["start"] == 1660
    write(tmp_path, [first])
    apply(db, tmp_path, "2026-10-05T00:00:00Z")
    standing = db.execute("SELECT id FROM assertions a WHERE NOT EXISTS (SELECT 1 FROM assertion_actions x "
                          "WHERE x.assertion=a.id AND x.action='retract')").fetchall()
    assert standing == [(f"{first.id}@2026-10-05T00:00:00Z",)]
    assert json.loads(db.execute("SELECT value FROM current_claims").fetchone()[0])["start"] == 1659
    assert db.execute("SELECT count(*) FROM assertion_evidence WHERE assertion=?", (standing[0][0],)).fetchone() == (1,)
