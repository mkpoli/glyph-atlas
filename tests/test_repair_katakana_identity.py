"""A crop identified as the hiragana of the katakana it is written in takes the katakana, as its own claim."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from glyph_atlas import tables
from glyph_atlas.review.store import ReviewRequest, Store
from glyph_atlas.schema import Box, Document, Line, Page, Unit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("repair_katakana_identity", ROOT / "scripts" / "repair_katakana_identity.py")
repair = importlib.util.module_from_spec(_spec)
sys.modules["repair_katakana_identity"] = repair
_spec.loader.exec_module(repair)

LINE = "p:l0"


def unit(ident: str, seq: int, code: str, text: str, script: str, **extra) -> Unit:
    return Unit(id=ident, document_id="d", page_id="p", line_id=LINE, seq=seq,
                box=Box(x=0, y=seq * 20, w=10, h=20), unicode=code, text_source=text, script=script, **extra)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    root = tmp_path / "dataset"
    root.mkdir()
    tables.write(root / "documents.parquet", [Document(id="d", title="t")], Document)
    tables.write(root / "pages.parquet", [Page(id="p", document_id="d", seq=0, image="x", width=100, height=200)], Page)
    tables.write(root / "lines.parquet", [Line(id=LINE, page_id="p", seq=0, text_raw="バガかパ", text="バガかパ",
                                               box=Box(x=0, y=0, w=10, h=100))], Line)
    tables.write(root / "units.parquet", [
        unit("ba", 0, "U+3070", "バ", "katakana"),                       # written バ, identified ば
        unit("ga", 1, "U+304C", "ガ", "katakana", review="reviewed"),    # a person reviewed it as が
        unit("ka", 2, "U+304B", "か", "hiragana"),                       # nothing to repair
        unit("pa", 3, "U+3071", "パ", "katakana"),                       # a person flagged it
    ], Unit)
    store = Store(root)
    store.record(ReviewRequest(target_id="pa", field="review", new="disputed", client_id="reviewer"))
    return store


def test_the_katakana_becomes_the_identity_and_a_person_s_decision_stays(store: Store, tmp_path: Path):
    plan = repair.plan_dataset(store)
    assert [row["id"] for row in plan.repairs] == ["ba"]
    assert sorted(row["id"] for row in plan.conflicts) == ["ga", "pa"]
    before = store.revision("ba")
    assert repair.apply_dataset(store, plan.repairs) == 1
    assert store.unit("ba").unicode == "U+30D0" and store.revision("ba") == before + 1
    event = store.events()[-1]
    assert (event.field, event.role, event.new) == ("unicode", "model", "U+30D0")
    assert json.loads(event.evidence)["kind"] == "katakana-identity"
    assert store.unit("ga").unicode == "U+304C" and store.unit("pa").unicode == "U+3071"
    assert repair.plan_dataset(store).repairs == [], "a second run finds nothing to repair"
    conflicts = tmp_path / "conflicts.tsv"
    repair.write_conflicts(conflicts, plan.conflicts)
    assert len(conflicts.read_text().splitlines()) == 3


def site(tmp_path: Path) -> sqlite3.Connection:
    from cloudflare_schema import schema

    db = sqlite3.connect(tmp_path / "site.sqlite")
    schema(db)
    for ident, character, reading, revision in (("ba", "ば", "バ", 4), ("seen", "ぶ", "ブ", 2)):
        data = json.dumps({"id": ident, "label": character, "reading": reading, "script": "katakana", "revision": revision})
        db.execute("INSERT INTO units(id,origin,character,reading,family,production,category,state,revision,quiz,"
                   "priority,shuffle,data,snapshot,context,visual) VALUES(?,'local',?,?,?,'unknown','kana','pending',?,"
                   "1,1,0,?,?,'{}','{}')", (ident, character, reading, f"U+{ord(character):04X}", revision, data,
                                            json.dumps({"character": json.loads(data)})))
    db.commit()
    return db


def test_the_site_takes_the_same_repair_once_and_never_over_a_review(tmp_path: Path):
    db = site(tmp_path)
    rows = [{"id": "ba", "character": "ば", "reading": "バ", "revision": 4, "state": "pending", "script": "katakana", "reviewed": 0},
            {"id": "seen", "character": "ぶ", "reading": "ブ", "revision": 2, "state": "flagged", "script": "katakana", "reviewed": 1},
            {"id": "drift", "character": "ぼ", "reading": "ボ", "revision": 1, "state": "pending", "script": "katakana", "reviewed": 0}]
    local = {"ba": ("U+30D0", 5), "drift": ("U+307C", 3)}
    plan = repair.plan_site(rows, local)
    assert [item["id"] for item in plan.repairs] == ["ba"]
    assert {item["id"]: item["reason"].split(":")[0] for item in plan.conflicts} == {
        "seen": "decided on the site", "drift": "the local store holds ('U+307C', 3)"}
    out = tmp_path / "d1"
    assert repair.write_site_parts(out, plan, "built") == 1
    part = (out / "parts" / "001.sql").read_text()
    for _ in range(2):
        db.executescript(part)
    character, family, revision, data, snapshot = db.execute(
        "SELECT character, family, revision, data, snapshot FROM units WHERE id='ba'").fetchone()
    assert (character, family, revision) == ("バ", "U+30D0", 5), "applied once, then guarded by the revision"
    assert json.loads(data)["label"] == "バ" and json.loads(snapshot)["character"]["revision"] == 5
    assert db.execute("SELECT character FROM units WHERE id='seen'").fetchone() == ("ぶ",)
    assert "time-travel restore" in (out / "apply.sh").read_text()


def test_only_the_katakana_of_the_same_kana_counts():
    assert repair.written_katakana("U+3070", "バ", "katakana") == "バ"
    assert repair.written_katakana("U+3070", "パ", "katakana") is None
    assert repair.written_katakana("U+3070", "バ", "hiragana") is None
    assert repair.written_katakana("U+30D0", "バ", "katakana") is None
    assert repair.written_katakana("U+3070 U+3099", "バ", "katakana") is None
