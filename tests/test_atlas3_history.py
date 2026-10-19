import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_publish import layout, write_source  # noqa: F401 -- the fixture

COLUMNS = {
    "submissions": "id,actor,request,response,at,undone",
    "events": "id,submission,target,actor,expected_revision,before_data,after_data,event,snapshot,kind,at,processed",
    "seen": "target,submission,box,image_sha256,at",
    "skips": "target,submission,actor,box,image_sha256,at",
    "actors": "actor,user_id,via,at",
    "favourites": "user_id,unit,at",
    "written_forms": "id,submission,target,actor,revision,pixels,label,form,request,at",
    "assertions": ("id,submission,subject,predicate,scope,slot,object,value,alternative_set,tier,asserted_by,asserted_at,"
                   "confidence,confidence_scheme,method,run,legacy"),
    "assertion_actions": "id,submission,assertion,action,actor,at,reason",
    "assertion_evidence": "assertion,kind,ref,locator",
    "assertion_premises": "assertion,premise,role",
    "current_claims": "subject,predicate,scope,slot,status,object,value,members,supporting,claims,crop_version,resolver,at",
    "ledger_submissions": "id,actor,request,response,at",
    "rejections": "submission,by,reason,at",
    "form_decisions": "seq,id,at,actor,kind,family,form,cluster,revision,units,note,issue,character,written_family",
    "forms": "id,anchor,created_by,created_at",
    "corpus_follow": "id",
    "unit_suspects": "id,p,reads_as,label,box",
    "unit_shapes": "id,shape_order",
    "unit_marks": "id,mark",
    "crop_versions": "id,unit,pixels,box,document,image,at",
}
A1 = {"x": 0, "y": 0, "w": 10, "h": 10}
OLD = {"x": 5, "y": 5, "w": 10, "h": 10}


def sub(key, actor, at, undone=0):
    return (key, actor, "{}", "{}", at, undone)


def event(id, target, submission, actor, expected, at, before_box=A1, label="あ", verdict="match", issue=None):
    evidence = {"kind": "character-review", "verdict": verdict, "issue": issue, "note": "",
                "snapshot": {"character": {"style": "unassessed"}}}
    return (id, submission, target, actor, expected, json.dumps({"box": before_box, "page_id": "hl:p9"}),
            json.dumps({"label": label, "state": "checked", "box": A1}), json.dumps({"evidence": json.dumps(evidence)}),
            "{}", "review", at, 0)


def version(unit, pixels, box):
    text = ",".join(str(box[k]) for k in "xywh")
    return (f"{unit}@{pixels}@{text}", unit, pixels, text, "book:a", f"/atlas/media/{pixels}.webp", "2026-10-03T00:00:00Z")


def sources(root, **tables):
    base = {
        "submissions": [sub("u1:s1", "u1", "2026-10-01T00:00:02Z"), sub("r-b:s2", "r-b", "2026-10-01T00:00:01Z")],
        "crop_versions": [version("ex:a1", "p1", A1), version("ex:a1", "p0", OLD), version("ex:a2", "p2", {"x": 0, "y": 20, "w": 10, "h": 10})],
        "actors": [("u1", "user-1", "account", "2026-10-01T00:00:00Z")],
    }
    base.update(tables)
    for table, cols in COLUMNS.items():
        write_source(root, table, cols.split(","), base.get(table, []))


def history(monkeypatch, root):
    for name in ("history",):
        sys.modules.pop(name, None)
    module = importlib.import_module("history")
    monkeypatch.setattr(sys, "argv", ["history.py"])
    with pytest.raises(SystemExit) as stopped:
        module.main()
    return stopped.value.code, sqlite3.connect(root / "out" / "history.sqlite")


def ids(root):
    return dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))


def test_seen_and_skips_map_to_the_cut_their_box_names(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    sources(root, seen=[("ex:a1", "u1:s1", json.dumps(A1), "p1", "2026-10-01T00:00:02Z"),
                        ("ex:a1", "r-b:s2", json.dumps(OLD), "p0", "2026-10-01T00:00:01Z"),
                        ("codh:g1", "u1:s1", json.dumps(OLD), "x", "2026-10-01T00:00:02Z")],
            skips=[("ex:a1", "r-b:s2", "r-b", json.dumps(OLD), "p0", "2026-10-01T00:00:01Z")])
    code, out = history(monkeypatch, root)
    assert code == 0
    i = ids(root)
    assert sorted(out.execute("SELECT submission, crop, cut, at FROM seen")) == sorted(
        [(2, i["ex:a1"], 0, 1790812802000), (1, i["ex:a1"], -1, 1790812801000), (2, i["codh:g1"], 0, 1790812802000)])
    assert list(out.execute("SELECT crop, cut FROM skips")) == [(i["ex:a1"], -1)]
    cuts = json.loads((root / "out" / "history-cuts.json").read_text())
    assert cuts == {"ex:a1": [{"cut": -1, "box": OLD, "pixels": "p0", "image": "/atlas/media/p0.webp"}]}


def test_a_box_no_recorded_version_has_takes_the_cut_below_the_oldest(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    sources(root, seen=[("ex:a1", "u1:s1", json.dumps({"x": 9, "y": 9, "w": 9, "h": 9}), "p1", "2026-10-01T00:00:02Z")])
    code, out = history(monkeypatch, root)
    assert code == 0
    # ex:a1 has a recorded earlier version (cut -1), so the unrecorded one is -2.
    assert out.execute("SELECT cut FROM seen").fetchall() == [(-2,)]
    assert "1 events, seen marks and skips name a box no recorded version has" in (root / "out" / "history-report.txt").read_text()


def test_an_event_on_a_crop_the_mirror_lacks_waits_for_its_tombstone_to_be_numbered(layout, monkeypatch):  # noqa: F811
    root, publish, _remote = layout
    sources(root, events=[event("cf:1", "ex:gone", "u1:s1", "u1", 0, "2026-10-01T00:00:02Z")])
    code, out = history(monkeypatch, root)
    assert code == 1
    assert out.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    tombstones = json.loads((root / "out" / "history-tombstones.json").read_text())
    assert [(t["text_id"], t["page_key"], t["record"]["box"]) for t in tombstones] == [("ex:gone", "hl:p9", A1)]
    assert "tombstone not yet numbered" in (root / "out" / "history-report.txt").read_text()
    # The publisher numbers the tombstone from its catalogue; the next run places the event on it.
    monkeypatch.setattr(sys, "argv", ["publish.py", str(root / "out" / "history-tombstones.sqlite")])
    publish.main()
    code, out = history(monkeypatch, root)
    assert code == 0
    assert out.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_events_take_their_revision_cut_codes_and_translated_ids(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    box = {"x": 0, "y": 20, "w": 10, "h": 10}
    sources(root, events=[event("cf:2", "ex:a2", "u1:s1", "u1", 1, "2026-10-01T00:00:02Z", box, verdict="wrong", issue="crop"),
                          event("cf:1", "ex:a2", "r-b:s2", "r-b", 0, "2026-10-01T00:00:01Z", box)])
    code, out = history(monkeypatch, root)
    assert code == 0
    a2 = ids(root)["ex:a2"]
    # Actors 1.. in key order, submissions 1.. in time order.
    assert list(out.execute("SELECT id, key, user FROM actors")) == [(1, "r-b", None), (2, "u1", "user-1")]
    assert list(out.execute("SELECT id, key, actor FROM submissions")) == [(1, "r-b:s2", 1), (2, "u1:s1", 2)]
    rows = list(out.execute("SELECT crop, revision, cut, actor, submission, kind, issue, x, y FROM events ORDER BY id"))
    verdict = dict(out.execute("SELECT key, code FROM codes WHERE field='kind'"))
    issue = dict(out.execute("SELECT key, code FROM codes WHERE field='issue'"))
    assert rows[0] == (a2, 1, 0, 1, 1, verdict["match"], None, 0, 0)
    assert rows[1][:2] == (a2, 2) and rows[1][5:7] == (verdict["wrong"], issue["crop"])


def test_events_at_one_revision_are_numbered_by_time(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    box = {"x": 0, "y": 20, "w": 10, "h": 10}
    sources(root, events=[event("cf:2", "ex:a2", "u1:s1", "u1", 0, "2026-10-01T00:00:02Z", before_box=box),
                          event("cf:1", "ex:a2", "r-b:s2", "r-b", 0, "2026-10-01T00:00:01Z", before_box=box)])
    code, out = history(monkeypatch, root)
    assert code == 0
    assert list(out.execute("SELECT revision, actor FROM events ORDER BY revision")) == [(1, 1), (2, 2)]
    assert "1 crops with two events at one expected revision" in (root / "out" / "history-report.txt").read_text()


def test_an_event_past_the_crops_revision_stops_the_move(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    sources(root, events=[event("cf:1", "ex:a1", "u1:s1", "u1", 0, "2026-10-01T00:00:02Z")])
    code, _out = history(monkeypatch, root)
    assert code == 1
    assert "revision 1 above the crop's 0" in (root / "out" / "history-report.txt").read_text()


def test_marks_are_recomputed_and_the_live_ones_written(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    sources(root,
            seen=[("ex:a1", "u1:s1", json.dumps(A1), "p1", "2026-10-01T00:00:02Z"),
                  ("ex:b1", "u1:s1", json.dumps(A1), "p1", "2026-10-01T00:00:02Z")],
            skips=[("ex:a1", "u1:s1", "u1", json.dumps(A1), "p1", "2026-10-01T00:00:02Z"),
                   ("ex:a1", "r-b:s2", "r-b", json.dumps(A1), "p1", "2026-10-01T00:00:01Z")],
            unit_marks=[("ex:a1", "seen"), ("ex:a2", "seen")])
    code, _out = history(monkeypatch, root)
    assert code == 0
    i = ids(root)
    report = (root / "out" / "history-report.txt").read_text()
    assert f"{i['ex:b1']}: seen" in report and f"{i['ex:a2']}: seen" in report
    assert f"{i['ex:a1']}: live seen, recomputed hard" in report
    marks = json.loads((root / "out" / "history-marks.json").read_text())
    assert marks == [{"crop": i["ex:a1"], "state": "seen", "at": 1790812802000, "actor": 2},
                     {"crop": i["ex:a2"], "state": "seen", "at": None, "actor": None}]


def test_a_form_claim_takes_the_cut_its_evidence_names(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    old_ref = version("ex:a1", "p0", OLD)[0]

    def claim(id, subject, predicate):
        return (id, None, subject, predicate, "", "", "fm:1", None, None, "observed", "u1", "2026-10-01T00:00:00Z",
                None, None, None, None, None)

    sources(root,
            assertions=[claim("cf:a", "ex:a1", "has_form"), claim("cf:b", "book:a", "date_produced")],
            assertion_evidence=[("cf:a", "crop", old_ref, None), ("cf:b", "source", "src:1", None)],
            current_claims=[("ex:a1", "has_form", "", "", "accepted", "fm:1", None, "[]", "[]", "[]", old_ref, "r", "t"),
                            ("book:a", "date_produced", "", "", "accepted", None, "1700", "[]", "[]", "[]", None, "r", "t")])
    code, out = history(monkeypatch, root)
    assert code == 0
    a1 = ids(root)["ex:a1"]
    assert sorted(out.execute("SELECT id, crop, cut FROM assertions")) == [("cf:a", a1, -1), ("cf:b", None, None)]
    assert sorted(out.execute("SELECT kind, crop, cut FROM assertion_evidence")) == [("crop", a1, -1), ("source", None, None)]
    assert sorted(out.execute("SELECT subject, crop, cut FROM current_claims")) == [("book:a", None, None), ("ex:a1", a1, -1)]


def test_script_facts_on_crops_that_no_longer_exist_are_dropped(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    sources(root, unit_suspects=[("ex:a1", 0.9, "い", "あ", None), ("ex:gone", 0.8, "い", "あ", None)],
            unit_shapes=[("ex:gone", 3)],
            form_decisions=[(1, "d1", "2026-10-01T00:00:00Z", "u1", "cluster", "あ", None, None, "r",
                             json.dumps(["codh:g1"]), "", None, None, None)])
    code, out = history(monkeypatch, root)
    assert code == 0
    assert list(out.execute("SELECT crop, label FROM suspects")) == [(ids(root)["ex:a1"], "あ")]
    assert out.execute("SELECT count(*) FROM shapes").fetchone()[0] == 0
    assert list(out.execute("SELECT crops FROM form_decisions")) == [(json.dumps([ids(root)["codh:g1"]]),)]
    report = (root / "out" / "history-report.txt").read_text()
    assert "unit_suspects: in 2, out 1, dropped 1" in report and "dropped (1) crop no longer exists" in report
