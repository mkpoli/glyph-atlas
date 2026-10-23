import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from test_atlas3_publish import crops, ids, layout, run  # noqa: F401 -- the fixture

# New corpus records sit in a pack named by its content (address_packs.py).
PACK = f"packs/{'1' * 64}.bin"

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "atlas3"))
flags = importlib.import_module("flags")


def entry(n, form="𛁈", realigned="所", label="シ"):
    return {"id": f"hl:a_1_000:x:{n}", "label": label, "realigned": realigned, "form": form, "line_id": "hl:a_1_000", "box": [1, 2, 3, 4]}


def test_a_glyph_is_checked_against_the_form_the_site_shows():
    assert flags.glyph(entry(1)) == ("hl:a_1_000:x:1", "𛁈", "box-relabel", "所", "シ")
    assert flags.glyph(entry(2, realigned=None))[3] is None


def test_a_family_code_point_names_no_shown_character_and_a_glyph_is_listed_once():
    with pytest.raises(ValueError):
        flags.glyph(entry(1, form="U+3057"))
    with pytest.raises(ValueError):
        flags.glyph(entry(1, form=None))
    with pytest.raises(ValueError):
        flags.rows([entry(1), entry(1)])


def corpus_catalogue(path, glyphs):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE corpus_units(id,character,family,visual_group,shuffle,object,offset,size,production,named,style,document,label)")
    db.executemany("INSERT INTO corpus_units VALUES(?,?,NULL,NULL,7,?,?,10,'printed',0,'unassessed','codh:b',NULL)",
                   [(g, c, PACK, i * 10) for i, (g, c) in enumerate(glyphs)])
    db.commit()
    return path


def write(tmp_path, monkeypatch, name, entries):
    (tmp_path / f"{name}.json").write_text(json.dumps(entries))
    flags.main([str(tmp_path / f"{name}.json"), str(tmp_path / f"{name}.sqlite")])
    return tmp_path / f"{name}.sqlite"


def glyph(id, form, realigned="所", label="シ"):
    return {"id": id, "form": form, "realigned": realigned, "label": label}


@pytest.fixture
def glyphs(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    run(publish, monkeypatch, corpus_catalogue(tmp_path / "corpus.sqlite", [("codh:g1", "が"), ("codh:g2", "き"), ("codh:g3", "く")]))
    return root, publish, remote


def test_a_flag_sends_a_corpus_glyph_to_the_flagged_view_once(glyphs, monkeypatch, tmp_path):
    root, publish, remote = glyphs
    g2, g3 = ids(root)["codh:g2"], ids(root)["codh:g3"]
    path = write(tmp_path, monkeypatch, "f", [glyph("codh:g3", "く", label="ク"), glyph("codh:g2", "き", realigned=None),
                                              glyph("codh:g1", "か"), glyph("ex:a1", "あ"), glyph("codh:g9", "け"),
                                              glyph("codh:gx", "こ")])
    run(publish, monkeypatch, path)
    flagged = remote.query("glyph-atlas-v3", "SELECT code FROM codes WHERE field='state' AND key='flagged'")[0]["code"]
    after = crops(remote)
    assert (after[g2]["state"], after[g2]["revision"]) == (flagged, 1) and (after[g3]["state"], after[g3]["revision"]) == (flagged, 1)
    assert remote.query("glyph-atlas-v3", "SELECT crop, seq FROM corpus_rows ORDER BY seq") == [{"crop": g3, "seq": 1}, {"crop": g2, "seq": 2}]
    assert remote.query("glyph-atlas-v3", "SELECT crop, finding, realigned, transcribed FROM corpus_flags ORDER BY crop") == [
        {"crop": g2, "finding": "box-relabel", "realigned": None, "transcribed": "シ"},
        {"crop": g3, "finding": "box-relabel", "realigned": "所", "transcribed": "ク"}]
    # Each flag is an event of kind 'flag' with the wrong-character issue, one revision on, under the publisher's submission.
    events = remote.query("glyph-atlas-v3", "SELECT e.crop, e.revision, k.key AS kind, e.state, e.issue, e.label = e.shown AS same, "
                          "s.key AS submission, a.key AS actor FROM events e JOIN codes k ON k.field='kind' AND k.code=e.kind "
                          "JOIN submissions s ON s.id=e.submission JOIN actors a ON a.id=e.actor ORDER BY e.id")
    assert [(e["crop"], e["revision"], e["kind"], e["state"], e["issue"], e["same"], e["actor"]) for e in events] == [
        (g3, 1, "flag", flagged, 0, 1, "publish"), (g2, 1, "flag", flagged, 0, 1, "publish")]
    assert events[0]["submission"].startswith("publish:") and events[0]["submission"].endswith(":0")
    request = json.loads(remote.query("glyph-atlas-v3", "SELECT request FROM submissions")[0]["request"])
    assert request["target"] == "@flags" and request["input"]["finding"] == "box-relabel"
    assert [g["id"] for g in request["input"]["glyphs"]] == ["codh:g3", "codh:g2"]
    assert [(r["crop"], r["state"]) for r in remote.query("glyph-atlas-v3", "SELECT crop, state FROM reviews ORDER BY crop")] == [
        (g2, flagged), (g3, flagged)]
    tally = {r["state"]: r["n"] for r in remote.query("glyph-atlas-v3", "SELECT state, sum(n) AS n FROM tally WHERE kind=1 GROUP BY state")}
    assert tally[flagged] == 2 and sum(tally.values()) == 0
    report = json.loads((max((root / "out" / "publish").glob("*-f")) / "flags.json").read_text())
    del report["at"]
    # codh:g1 is withheld from the quiz by its review overlay; ex:a1 is a collected crop.
    assert report == {"flagged": ["codh:g3", "codh:g2"], "unshowable": ["codh:g1"], "held": ["ex:a1"], "missing": ["codh:g9", "codh:gx"]}
    outcome = json.loads((max((root / "out" / "publish").glob("*-f")) / "flags-outcome.json").read_text())
    assert outcome == {"flagged": ["codh:g3", "codh:g2"], "refused": []}
    assert json.loads(remote.query("glyph-atlas-v3", "SELECT response FROM submissions")[0]["response"]) == {"flagged": ["codh:g3", "codh:g2"]}
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert local.execute("SELECT crop, seq FROM corpus_rows ORDER BY seq").fetchall() == [(g3, 1), (g2, 2)]
    sent = remote.query("glyph-atlas-v3", "SELECT count(*) AS n FROM imports")[0]["n"]
    run(publish, monkeypatch, write(tmp_path, monkeypatch, "again", [glyph("codh:g2", "き")]))
    assert remote.query("glyph-atlas-v3", "SELECT count(*) AS n FROM imports")[0]["n"] == sent
    assert crops(remote)[g2]["revision"] == 1


def test_a_catalogue_named_with_braces_flags_its_glyphs(glyphs, monkeypatch, tmp_path):
    root, publish, remote = glyphs
    run(publish, monkeypatch, write(tmp_path, monkeypatch, "f{n}{rev}", [glyph("codh:g2", "き")]))
    flagged = remote.query("glyph-atlas-v3", "SELECT code FROM codes WHERE field='state' AND key='flagged'")[0]["code"]
    assert crops(remote)[ids(root)["codh:g2"]]["state"] == flagged


def test_a_glyph_whose_character_moved_is_left_as_it_is(glyphs, monkeypatch, tmp_path):
    root, publish, remote = glyphs
    run(publish, monkeypatch, write(tmp_path, monkeypatch, "f", [glyph("codh:g2", "け")]))
    assert crops(remote)[ids(root)["codh:g2"]]["revision"] == 0
    assert remote.query("glyph-atlas-v3", "SELECT * FROM corpus_rows") == []


def test_a_glyph_a_round_named_since_keeps_the_sites_rows(glyphs, monkeypatch, tmp_path, capsys):
    root, publish, remote = glyphs
    before = recount(sqlite3.connect(root / "out" / "atlas.sqlite"))
    g2 = ids(root)["codh:g2"]
    # A round showed it on the site after the publish read D1 and before its part landed: its row and a
    # mark one revision on.
    site, send = remote.dbs["glyph-atlas-v3"], publish.send

    def round_first(database, file, stamp):
        if database == "glyph-atlas-v3" and not site.execute("SELECT 1 FROM corpus_rows").fetchone():
            site.execute("INSERT INTO corpus_rows(crop, seq) VALUES(?, 1)", (g2,))
            site.execute("UPDATE crops SET revision=1, state=3 WHERE id=?", (g2,))
            site.commit()
        return send(database, file, stamp)
    monkeypatch.setattr(publish, "send", round_first)
    run(publish, monkeypatch, write(tmp_path, monkeypatch, "f", [glyph("codh:g2", "き")]))
    assert (crops(remote)[g2]["state"], crops(remote)[g2]["revision"]) == (3, 1)
    assert remote.query("glyph-atlas-v3", "SELECT * FROM corpus_flags") == []
    out = capsys.readouterr().out
    assert "1 changes kept D1's newer review" in out and "corpus flags: 0 flagged, 1 refused by D1" in out
    outcome = json.loads((max((root / "out" / "publish").glob("*-f")) / "flags-outcome.json").read_text())
    assert outcome == {"flagged": [], "refused": ["codh:g2"]}
    # A submission that flagged nothing is not kept.
    assert remote.query("glyph-atlas-v3", "SELECT count(*) AS n FROM submissions")[0]["n"] == 0
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert local.execute("SELECT state, revision FROM crops WHERE id=?", (g2,)).fetchone() == (3, 1)
    assert local.execute("SELECT crop, seq FROM corpus_rows").fetchall() == [(g2, 1)]
    assert local.execute("SELECT count(*) FROM corpus_flags").fetchone() == (0,)
    assert remote.query("glyph-atlas-v3", "SELECT count(*) AS n FROM events")[0]["n"] == 0
    assert local.execute("SELECT count(*) FROM events").fetchone() == (0,)
    assert follows(before, recount(local))


def test_a_glyph_the_site_moved_to_another_character_is_judged_on_d1(glyphs, monkeypatch, tmp_path):
    root, publish, remote = glyphs
    g2 = ids(root)["codh:g2"]
    # A form decision's follow moved the glyph's character on the site, keeping its revision.
    site = remote.dbs["glyph-atlas-v3"]
    site.execute("UPDATE crops SET char=(SELECT char FROM crops WHERE id=?) WHERE id=?", (ids(root)["codh:g3"], g2))
    site.commit()
    run(publish, monkeypatch, write(tmp_path, monkeypatch, "f", [glyph("codh:g2", "き")]))
    assert crops(remote)[g2]["revision"] == 0 and remote.query("glyph-atlas-v3", "SELECT * FROM corpus_flags") == []
    report = json.loads((max((root / "out" / "publish").glob("*-f")) / "flags.json").read_text())
    assert report["changed"] == ["codh:g2"] and "flagged" not in report


def test_a_character_moved_after_the_glyph_was_read_stops_its_flag_in_d1(glyphs, monkeypatch, tmp_path, capsys):
    root, publish, remote = glyphs
    before = recount(sqlite3.connect(root / "out" / "atlas.sqlite"))
    g2 = ids(root)["codh:g2"]
    site, send = remote.dbs["glyph-atlas-v3"], publish.send

    def follow_first(database, file, stamp):
        if database == "glyph-atlas-v3":
            site.execute("UPDATE crops SET char=(SELECT char FROM crops WHERE id=?) WHERE id=?", (ids(root)["codh:g3"], g2))
            site.commit()
        return send(database, file, stamp)
    monkeypatch.setattr(publish, "send", follow_first)
    run(publish, monkeypatch, write(tmp_path, monkeypatch, "f", [glyph("codh:g2", "き")]))
    assert crops(remote)[g2]["revision"] == 0
    assert remote.query("glyph-atlas-v3", "SELECT count(*) AS n FROM corpus_flags")[0]["n"] == 0
    assert remote.query("glyph-atlas-v3", "SELECT count(*) AS n FROM events")[0]["n"] == 0
    assert "corpus flags: 0 flagged, 1 refused by D1" in capsys.readouterr().out
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert follows(before, recount(local))


def test_each_finding_has_its_own_submission(glyphs, monkeypatch, tmp_path):
    _root, publish, remote = glyphs
    path = write(tmp_path, monkeypatch, "f", [glyph("codh:g2", "き"), glyph("codh:g3", "く")])
    sqlite3.connect(path).execute("UPDATE corpus_flags SET finding='other' WHERE id='codh:g3'").connection.commit()
    run(publish, monkeypatch, path)
    requests = [json.loads(r["request"])["input"] for r in remote.query("glyph-atlas-v3", "SELECT request FROM submissions ORDER BY id")]
    assert [(r["finding"], [g["id"] for g in r["glyphs"]]) for r in requests] == [("box-relabel", ["codh:g2"]), ("other", ["codh:g3"])]


def recount(local):
    """The local tally's corpus cells beside a count of the local crops, each as {cell: n}."""
    tally = {tuple(r[:-1]): r[-1] for r in local.execute(
        "SELECT kind,char,family,book,production,style,state,quiz,decade,sum(n) FROM tally WHERE kind=1 GROUP BY 1,2,3,4,5,6,7,8,9")
        if r[-1]}
    crops = {tuple(r[:-1]): r[-1] for r in local.execute(
        "SELECT kind,coalesce(char,-1),coalesce(family,-1),book,coalesce(production,-1),coalesce(style,-1),state,quiz,"
        "coalesce((year-CASE WHEN year<0 THEN 9 ELSE 0 END)/10*10,-1),count(*) FROM crops WHERE kind=1 GROUP BY 1,2,3,4,5,6,7,8,9")}
    return tally, crops


def follows(before, after):
    """Whether the local tally moved exactly as the local crops' cells did."""
    def delta(a, b):
        return {k: b.get(k, 0) - a.get(k, 0) for k in a.keys() | b.keys() if b.get(k, 0) != a.get(k, 0)}
    return delta(before[0], after[0]) == delta(before[1], after[1])


def test_a_glyph_d1_flagged_that_the_local_copy_saw_under_another_character(glyphs, monkeypatch, tmp_path, capsys):
    root, publish, remote = glyphs
    g2, g3 = ids(root)["codh:g2"], ids(root)["codh:g3"]
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    before = recount(local)
    # The site's follow moved the glyph to く (g3's character) without a revision; the local copy still has き.
    site = remote.dbs["glyph-atlas-v3"]
    site.execute("UPDATE crops SET char=(SELECT char FROM crops WHERE id=?) WHERE id=?", (g3, g2))
    site.commit()
    run(publish, monkeypatch, write(tmp_path, monkeypatch, "f", [glyph("codh:g2", "く")]))
    flagged = remote.query("glyph-atlas-v3", "SELECT code FROM codes WHERE field='state' AND key='flagged'")[0]["code"]
    assert (crops(remote)[g2]["state"], crops(remote)[g2]["revision"]) == (flagged, 1)
    out = capsys.readouterr().out
    assert "corpus flags: 1 flagged, 0 refused by D1" in out and "kept D1's newer review" not in out
    assert local.execute("SELECT char, state FROM crops WHERE id=?", (g2,)).fetchone() == \
        tuple(site.execute("SELECT char, state FROM crops WHERE id=?", (g2,)).fetchone())
    # The local tally moved as the local crops did, the glyph's old cell under き included.
    assert follows(before, recount(local))
