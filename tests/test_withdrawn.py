import importlib.util
import json
import sqlite3
from pathlib import Path

import yaml

from glyph_atlas import withdrawn

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("withdraw", ROOT / "scripts" / "withdraw_documents.py")
withdraw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(withdraw)


def test_every_withdrawal_states_its_reason_and_evidence():
    data = yaml.safe_load(withdrawn.PATH.read_text(encoding="utf-8"))
    for entry in data["withdrawals"]:
        assert entry["reason"] and entry["evidence"] and entry["decided"] and entry["documents"]
    assert "hl:68eb3417ed31bd40b47322289459eeec" in withdrawn.documents()


def test_the_corpus_range_holds_exactly_the_document_glyphs():
    low, high = withdrawn.corpus_range("hl:ab")
    inside = ["hl:ab_0_000:be9c:1", "hl:ab_33_016:be9c:25"]
    outside = ["hl:ab", "hl:abc_0_000:be9c:1", "hl:aa_0_000:x:1", "hl:ac_0_000:x:1"]
    assert all(low <= i < high for i in inside) and not any(low <= o < high for o in outside)


def site():
    db = sqlite3.connect(":memory:")
    for path in sorted((ROOT / "apps/cloudflare/migrations").glob("*.sql")):
        db.executescript(path.read_text())
    db.execute("PRAGMA foreign_keys=ON")
    for unit, document in (("ex:1", "hl:gone"), ("ex:2", "hl:gone"), ("ex:3", "hl:kept")):
        db.execute("INSERT INTO units(id,origin,character,production,category,state,revision,quiz,priority,shuffle,"
                   "data,snapshot,context,visual,document) VALUES(?,'local','字','unknown','kanji','pending',0,1,0,0,?,"
                   "'{}','{}','{}',?)",
                   (unit, json.dumps({"image": f"/atlas/media/{unit[3]}a.webp", "context_image": f"/atlas/media/{unit[3]}b.webp",
                                      "image_sha256": "page", "box": {"x": 1, "y": 2, "w": 3, "h": 4}}), document))
        for key in ("a", "b"):
            db.execute("INSERT INTO media VALUES(?,'pack',0,1,'image/webp')", (unit[3] + key,))
    # A trigram of the kept book whose last crop is a withdrawn one, and a pair wholly in the kept book.
    db.executemany("INSERT INTO unit_ngrams(first,size,second,third,text,document) VALUES(?,?,?,?,?,?)",
                   [("ex:3", 3, "ex:3", "ex:1", "字字字", "hl:kept"), ("ex:3", 2, "ex:3", None, "字字", "hl:kept")])
    db.execute("INSERT INTO submissions(id,actor,request,response,at) VALUES('s','a','{}','{}','t')")
    db.execute("INSERT INTO seen VALUES('ex:1','s','{\"x\":1,\"y\":2,\"w\":3,\"h\":4}','h','t')")
    assert db.execute("SELECT count(*) FROM unit_marks").fetchone()[0] == 1
    for glyph in ("hl:gone_0_000:x:1", "hl:gone_1_002:x:4", "hl:gonearound_0_000:x:1", "hl:kept_0_000:x:1"):
        db.execute("INSERT INTO corpus_units(id,character,shuffle,object,offset,size) VALUES(?,'字',0,'o',0,1)", (glyph,))
        db.execute("INSERT INTO corpus_gallery VALUES(?,0,'o',0,'{}')", (glyph,))
    # A round wrote this glyph's `units` row, which names no document, and a reviewer has seen it.
    db.execute("INSERT INTO units(id,origin,character,production,category,state,revision,quiz,priority,shuffle,"
               "data,snapshot,context,visual) VALUES('hl:gone_0_000:x:1','corpus','字','unknown','kanji','pending',"
               "0,1,0,0,'{\"source_revision\":\"glyph\"}','{}','{}','{}')")
    db.execute("INSERT INTO seen VALUES('hl:gone_0_000:x:1','s',NULL,'h','t')")
    # A claim about each book's crop, one accepted, and a claim about the round's glyph.
    # The last was made on a cut of ex:2 the site never had.
    for claim, subject, version in (("cf:1", "ex:1", "ex:1@page@1,2,3,4"), ("cf:2", "ex:3", "ex:3@page@1,2,3,4"),
                                    ("cf:3", "hl:gone_0_000:x:1", "hl:gone_0_000:x:1@glyph@"), ("cf:4", "ex:2", "ex:2@page@9,9,9,9")):
        db.execute("INSERT INTO assertions(id,subject,predicate,value,tier,asserted_by,asserted_at) "
                   "VALUES(?,?,'has_form','\"unreadable\"','observed','a','t')", (claim, subject))
        db.execute("INSERT INTO assertion_evidence(assertion,kind,ref) VALUES(?,'crop',?)", (claim, version))
        db.execute("INSERT INTO assertion_actions(id,assertion,action,actor,at) VALUES(?,?,'accept','b','t')", (claim + "a", claim))
    return db


def test_the_statements_take_a_withdrawn_document_off_the_site_and_nothing_else():
    db = site()
    sql = "\n".join(withdraw.statements({"hl:gone"}))
    for _ in range(2):
        db.executescript(sql)
        assert [r for r, in db.execute("SELECT id FROM units")] == ["ex:3"]
        assert sorted(r for r, in db.execute("SELECT key FROM media")) == ["3a", "3b"]
        assert db.execute("SELECT count(*) FROM seen").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM unit_marks").fetchone()[0] == 0
        assert db.execute("SELECT first,size FROM unit_ngrams").fetchall() == [("ex:3", 2)]
        assert db.execute("SELECT coalesce(sum(n),0) FROM unit_counts WHERE document IS NULL OR document<>'hl:kept'"
                          ).fetchone()[0] == 0
        kept = ["hl:gonearound_0_000:x:1", "hl:kept_0_000:x:1"]
        assert sorted(r for r, in db.execute("SELECT id FROM corpus_units")) == kept
        assert sorted(r for r, in db.execute("SELECT id FROM corpus_gallery")) == kept
        assert db.execute("SELECT count(*) FROM metadata WHERE key='units_refreshed_at'").fetchone()[0] == 1
        assert [r for r, in db.execute("SELECT unit FROM crop_versions")] == ["ex:3"], "the versions go with their crops"
        for table, column in (("assertions", "id"), ("assertion_evidence", "assertion"), ("assertion_actions", "assertion")):
            assert [r for r, in db.execute(f"SELECT {column} FROM {table}")] == ["cf:2"], f"the claims go with their crops: {table}"


def test_a_release_cuts_no_crop_of_a_withdrawn_document(monkeypatch):
    from glyph_atlas import export
    from glyph_atlas.schema import Document, Licence, Rights

    document = Document(id="hl:gone", title="t", image_rights=Rights(licence=Licence.PDM, attribution="a"))
    assert export.images_ok(document, Licence.CC_BY_SA_4)
    monkeypatch.setattr(withdrawn, "documents", lambda: frozenset({"hl:gone"}))
    assert not export.images_ok(document, Licence.CC_BY_SA_4)


def test_form_clustering_admits_no_glyph_of_a_withdrawn_document(tmp_path, monkeypatch):
    from glyph_atlas import form_quality

    monkeypatch.setenv("ATLAS_FORM_DECISIONS", str(tmp_path / "decisions.jsonl"))
    admission = form_quality.Admission(tmp_path)
    monkeypatch.setattr(withdrawn, "documents", lambda: frozenset({"hl:gone"}))
    assert admission.reason({"id": "hl:gone_0_000:x:1", "document_id": "hl:gone"}) == "withdrawn"
