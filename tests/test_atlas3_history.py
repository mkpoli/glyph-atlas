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
    "representations": "id,scheme,value,namespace,version",
    "corpus_follow": "id",
    "form_bases": "id,character,family",
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
    assert cuts == {"ex:a1": {"cut_at": "2026-10-03T00:00:00Z", "encodings": [],
                              "cuts": [{"cut": -1, "box": OLD, "pixels": "p0", "image": "/atlas/media/p0.webp", "at": "2026-10-03T00:00:00Z"}]},
                    "ex:a2": {"cut_at": "2026-10-03T00:00:00Z", "cuts": [], "encodings": []}}


def test_another_encoding_under_the_same_box_is_the_current_cut_still_named_by_its_version(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    # As the 35 corpus glyphs of the 19 October copy: a source revision changed under the same box and
    # image, and the old site recorded both; the overlay names the newer.
    from test_atlas3_publish import UNITS, unit
    box = {"x": 3054, "y": 1048, "w": 91, "h": 102}
    overlay = ("codh:g1", "corpus", "か", None, None, "printed", "kana", "checked", 2, 0, 1, 1, "codh:b", "unassessed", None,
               json.dumps({"box": box, "source_revision": "51174cc2", "image": "/atlas/media/0ecf.webp"}))
    (root / "source-units.sqlite").unlink()
    write_source(root, "units", UNITS, [unit("ex:a1", "hl:p1", A1, "あ"), overlay])
    text = ",".join(str(box[k]) for k in "xywh")
    pair = [(f"codh:g1@51174cc2@{text}", "codh:g1", "51174cc2", text, "codh:b", "/atlas/media/0ecf.webp", "2026-10-13T16:53:07.985Z"),
            (f"codh:g1@7a90147c@{text}", "codh:g1", "7a90147c", text, "codh:b", "/atlas/media/0ecf.webp", "2026-09-26T10:58:08.863Z")]
    sources(root, crop_versions=pair, assertion_evidence=[("c1", "crop", f"codh:g1@7a90147c@{text}", None)],
            assertions=[("c1", None, "codh:g1", "has_form", "", "", "f:1", None, None, "observed", "u1", "2026-10-01T00:00:00Z",
                         None, None, "form-picker", None, None)])
    code, out = history(monkeypatch, root)
    assert code == 0
    cuts = json.loads((root / "out" / "history-cuts.json").read_text())
    assert cuts["codh:g1"] == {"cut_at": "2026-10-13T16:53:07.985Z", "cuts": [],
                               "encodings": [{"pixels": "7a90147c", "image": "/atlas/media/0ecf.webp", "at": "2026-09-26T10:58:08.863Z"}]}
    # Evidence on the older encoding names the current cut.
    assert out.execute("SELECT cut FROM assertion_evidence").fetchall() == [(0,)]


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


def test_an_event_on_a_crop_reviewed_since_the_last_publish_is_kept(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    sources(root, events=[event("cf:1", "ex:a1", "u1:s1", "u1", 0, "2026-10-01T00:00:02Z")])
    # The copy holds the review; the local copy's row waits for the next publish.
    db = sqlite3.connect(root / "source-units.sqlite")
    db.execute("UPDATE units SET revision = 1 WHERE id = 'ex:a1'")
    db.commit()
    code, out = history(monkeypatch, root)
    assert code == 0
    assert list(out.execute("SELECT revision FROM events")) == [(1,)]
    assert "1 crops reviewed since the local copy was published" in (root / "out" / "history-report.txt").read_text()


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

    def claim(id, subject, predicate, submission=None):
        return (id, submission, subject, predicate, "", "", "fm:1", None, None, "observed", "u1", "2026-10-01T00:00:00Z",
                None, None, None, None, None)

    current_ref = version("ex:a2", "p2", {"x": 0, "y": 20, "w": 10, "h": 10})[0]
    saved = json.dumps({"submission": "u1:w", "subject": "ex:a2", "replaced": None, "retracted": []})
    sources(root,
            assertions=[claim("cf:a", "ex:a1", "has_form"), claim("cf:b", "book:a", "date_produced"),
                        claim("cf:w", "ex:a2", "writes_word", "u1:w")],
            assertion_evidence=[("cf:a", "crop", old_ref, None), ("cf:b", "source", "src:1", None),
                                ("cf:w", "crop", current_ref, None)],
            ledger_submissions=[("u1:w", "u1", "{}", saved, "2026-10-01T00:00:00Z"),
                                ("u1:d", "u1", "{}", json.dumps({"submission": "u1:d", "subject": "book:a"}), "2026-10-01T00:00:00Z")],
            current_claims=[("ex:a1", "has_form", "", "", "accepted", "fm:1", None, "[]", "[]", "[]", old_ref, "r", "t"),
                            ("book:a", "date_produced", "", "", "accepted", None, "1700", "[]", "[]", "[]", None, "r", "t")])
    code, out = history(monkeypatch, root)
    assert code == 0
    a1 = ids(root)["ex:a1"]
    a2 = ids(root)["ex:a2"]
    # Every predicate whose subject is a crop takes the crop and cut, a word claim as a form claim does.
    assert sorted(out.execute("SELECT id, crop, cut FROM assertions")) == [("cf:a", a1, -1), ("cf:b", None, None), ("cf:w", a2, 0)]
    assert sorted(out.execute("SELECT kind, crop, cut FROM assertion_evidence")) == [("crop", a1, -1), ("crop", a2, 0), ("source", None, None)]
    # A saved response on a crop names it by integer id and the cut its claims rest on; another subject keeps its key only.
    responses = {k: json.loads(v) for k, v in out.execute("SELECT id, response FROM ledger_submissions")}
    assert responses["u1:w"] == {"submission": "u1:w", "subject": "ex:a2", "replaced": None, "retracted": [], "crop": a2, "cut": 0}
    assert responses["u1:d"] == {"submission": "u1:d", "subject": "book:a"}
    assert sorted(out.execute("SELECT subject, crop, cut FROM current_claims")) == [("book:a", None, None), ("ex:a1", a1, -1)]


def test_representations_move_as_they_are(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    rows = [("rp:1", "unicode", "あ", None, None), ("rp:2", "pua", "\ue000", "ns", "1")]
    sources(root, forms=[("fm:1", "rp:1", "u1", "2026-10-01T00:00:00Z")], representations=rows)
    code, out = history(monkeypatch, root)
    assert code == 0
    assert sorted(out.execute("SELECT * FROM representations")) == rows
    report = (root / "out" / "history-report.txt").read_text()
    assert "representations: in 2, out 2" in report


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


TWO_EARLIER = [version("ex:a1", "p1", A1), version("ex:a1", "p0", OLD), version("ex:a1", "pz", {"x": 5, "y": 5, "w": 5, "h": 5})]


def test_a_crop_with_two_earlier_boxes_stops_the_move_and_keeps_its_current_box(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    sources(root, crop_versions=TWO_EARLIER, seen=[("ex:a1", "u1:s1", json.dumps(A1), "p1", "2026-10-01T00:00:02Z")])
    code, out = history(monkeypatch, root)
    assert code == 1
    assert "more than one earlier box" in (root / "out" / "history-report.txt").read_text()
    assert out.execute("SELECT cut FROM seen").fetchall() == [(0,)]


def test_a_box_no_cut_has_on_a_crop_with_two_earlier_boxes_is_not_guessed(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    sources(root, crop_versions=TWO_EARLIER, seen=[("ex:a1", "u1:s1", json.dumps({"x": 9, "y": 9, "w": 9, "h": 9}), "p1",
                                                    "2026-10-01T00:00:02Z")])
    code, out = history(monkeypatch, root)
    assert code == 1
    assert out.execute("SELECT count(*) FROM seen").fetchone()[0] == 0


def test_a_followed_glyphs_first_character_moves_as_a_grapheme(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    sources(root, form_bases=[("codh:g1", "か", "U+304B"), ("ex:gone", "あ", "U+3042")])
    code, out = history(monkeypatch, root)
    assert code == 0
    atlas = sqlite3.connect(root / "out" / "atlas.sqlite")
    ka = atlas.execute("SELECT id FROM graphemes WHERE text='か'").fetchone()[0]
    assert out.execute("SELECT crop, char FROM form_bases").fetchall() == [(ids(root)["codh:g1"], ka)]


def test_an_event_keeps_its_recorded_issue_and_what_it_replaced(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    box = {"x": 0, "y": 20, "w": 10, "h": 10}
    reading = list(event("cf:1", "ex:a2", "u1:s1", "u1", 0, "2026-10-01T00:00:02Z", box, verdict="wrong", issue="reading"))
    redrawn = {"x": 1, "y": 21, "w": 9, "h": 9}
    reading[5] = json.dumps({"box": redrawn, "box_pending": True, "state": "flagged", "issue": "reading", "page_id": "hl:p9"})
    sources(root, events=[tuple(reading)])
    code, out = history(monkeypatch, root)
    assert code == 0
    issue = dict(out.execute("SELECT key, code FROM codes WHERE field='issue'"))
    state = dict(out.execute("SELECT key, code FROM codes WHERE field='state'"))
    # The event keeps the issue as recorded; what the crop held before reads `reading` as `character`, as the
    # old site renamed it (its migration 0055).
    assert list(out.execute("SELECT issue, reverses, was_state, was_issue, was_x, was_y, was_w, was_h, kept_issue FROM events")) == [
        (issue["reading"], None, state.get("flagged", 2), issue["character"], 1, 21, 9, 9, None)]


def test_a_batch_that_left_a_reported_crop_reported_moves_with_its_kept_issue(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    box = {"x": 0, "y": 20, "w": 10, "h": 10}
    kept = list(event("cf:1", "ex:a2", "u1:s1", "u1", 0, "2026-10-01T00:00:02Z", box, verdict="match"))
    kept[6] = json.dumps({"label": "あ", "state": "flagged", "issue": "crop", "box": box})
    sources(root, events=[tuple(kept)])
    code, out = history(monkeypatch, root)
    assert code == 0
    issue = dict(out.execute("SELECT key, code FROM codes WHERE field='issue'"))
    # The answer matched; the crop stays reported for its box.
    assert list(out.execute("SELECT issue, kept_issue FROM events")) == [(None, issue["crop"])]


def test_events_keep_the_label_shown_and_the_character_named(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout

    def reviewed(id, expected, at, evidence, label):
        # ex:a2 is at revision 3, so three events fit below it.
        box = {"x": 0, "y": 20, "w": 10, "h": 10}
        return (id, "u1:s1", "ex:a2", "u1", expected, json.dumps({"box": box, "page_id": "hl:p1", "label": "あ"}),
                json.dumps({"label": label, "state": "checked", "box": box}), json.dumps({"evidence": json.dumps(evidence)}),
                "{}", "review", at, 0)
    sources(root, events=[
        # A round names its label; a single review shows the label in its snapshot, and may name a
        # character that leaves the label as it was.
        reviewed("cf:1", 0, "2026-10-01T00:00:01Z", {"kind": "visual-quiz", "label": "あ", "verdict": "wrong", "issue": "character",
                                                     "suggested_character": "U+3044"}, "い"),
        reviewed("cf:2", 1, "2026-10-01T00:00:02Z", {"kind": "character-review", "verdict": "wrong", "issue": "character",
                                                     "suggested_character": "U+3044",
                                                     # A record from before readings left them, its reading not its label.
                                                     "snapshot": {"character": {"label": "い", "reading": "イ"}},
                                                     "correction": {"unicode": "U+3044", "reading": "ヰ"}}, "い"),
        reviewed("cf:3", 2, "2026-10-01T00:00:03Z", {"kind": "character-review", "verdict": "match", "issue": None,
                                                     "snapshot": {"character": {"label": "い"}}}, "い")])
    code, out = history(monkeypatch, root)
    assert code == 0
    text = dict(sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT id, text FROM graphemes"))
    rows = [(r, text.get(s), text.get(n), text.get(label), reading)
            for r, s, n, label, reading in out.execute("SELECT revision, shown, named, label, reading FROM events ORDER BY revision")]
    assert rows == [(1, "あ", "い", "い", None), (2, "い", "い", "い", "イ"), (3, "い", None, "い", None)]
    assert [r for (r,) in out.execute("SELECT saved_reading FROM events ORDER BY revision")] == [None, "ヰ", None]
    # Each keeps the old journal's id, which the review export names it by.
    assert [i for (i,) in out.execute("SELECT legacy FROM events ORDER BY revision")] == ["cf:1", "cf:2", "cf:3"]


def test_corpus_rows_follow_the_old_rows_and_keep_each_flag(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    sources(root)
    flag = {"finding": "box-relabel", "realigned": "所", "transcribed": None, "at": "2026-10-02T00:00:00Z"}
    db = sqlite3.connect(root / "source-units.sqlite")
    db.execute("UPDATE units SET data=? WHERE id='codh:g1'", (json.dumps({"flag": flag}),))
    db.commit()
    code, out = history(monkeypatch, root)
    assert code == 0
    g1 = ids(root)["codh:g1"]
    assert list(out.execute("SELECT crop, seq FROM corpus_rows")) == [(g1, 1)]
    assert list(out.execute("SELECT crop, finding, realigned, transcribed, at FROM corpus_flags")) == [
        (g1, "box-relabel", "所", None, 1790899200000)]


def test_a_submissions_events_are_numbered_in_its_requests_order(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    at = "2026-10-01T00:00:03Z"
    request = json.dumps({"input": {"answers": [{"id": "ex:b1"}, {"id": "ex:a2"}, {"id": "ex:a1"}]}, "target": None})
    a2 = {"x": 0, "y": 20, "w": 10, "h": 10}
    # The event ids sort the other way round from the answers, as random ids may.
    sources(root, submissions=[sub("u1:s1", "u1", "2026-10-01T00:00:02Z"), ("u1:r", "u1", request, "{}", at, 0)],
            events=[event("cf:a", "ex:a1", "u1:r", "u1", 0, at), event("cf:b", "ex:a2", "u1:r", "u1", 0, at, a2),
                    event("cf:c", "ex:b1", "u1:r", "u1", 0, at)])
    i = ids(root)
    # Each crop reviewed once since its publication.
    atlas = sqlite3.connect(root / "out" / "atlas.sqlite")
    atlas.execute("UPDATE crops SET revision=1 WHERE id IN (?, ?)", (i["ex:a1"], i["ex:b1"]))
    atlas.commit()
    code, out = history(monkeypatch, root)
    assert code == 0
    assert [c for (c,) in out.execute("SELECT crop FROM events ORDER BY id")] == [i["ex:b1"], i["ex:a2"], i["ex:a1"]]
