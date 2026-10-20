import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from test_atlas3_publish import crops, ids, layout, run  # noqa: F401 -- the fixture

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
    db.executemany("INSERT INTO corpus_units VALUES(?,?,NULL,NULL,7,'corpus-0001.bin',?,10,'printed',0,'unassessed','codh:b',NULL)",
                   [(g, c, i * 10) for i, (g, c) in enumerate(glyphs)])
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
    report = json.loads((max((root / "out" / "publish").glob("*-f")) / "flags.json").read_text())
    del report["at"]
    # codh:g1 is withheld from the quiz by its review overlay; ex:a1 is a collected crop.
    assert report == {"flagged": ["codh:g3", "codh:g2"], "unshowable": ["codh:g1"], "held": ["ex:a1"], "missing": ["codh:g9", "codh:gx"]}
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert local.execute("SELECT crop, seq FROM corpus_rows ORDER BY seq").fetchall() == [(g3, 1), (g2, 2)]
    sent = remote.query("glyph-atlas-v3", "SELECT count(*) AS n FROM imports")[0]["n"]
    run(publish, monkeypatch, write(tmp_path, monkeypatch, "again", [glyph("codh:g2", "き")]))
    assert remote.query("glyph-atlas-v3", "SELECT count(*) AS n FROM imports")[0]["n"] == sent
    assert crops(remote)[g2]["revision"] == 1


def test_a_glyph_whose_character_moved_is_left_as_it_is(glyphs, monkeypatch, tmp_path):
    root, publish, remote = glyphs
    run(publish, monkeypatch, write(tmp_path, monkeypatch, "f", [glyph("codh:g2", "け")]))
    assert crops(remote)[ids(root)["codh:g2"]]["revision"] == 0
    assert remote.query("glyph-atlas-v3", "SELECT * FROM corpus_rows") == []


def test_a_glyph_a_round_named_since_keeps_the_sites_rows(glyphs, monkeypatch, tmp_path, capsys):
    root, publish, remote = glyphs
    g2 = ids(root)["codh:g2"]
    # A round showed it on the site after the local copy was read: its row and a mark one revision on.
    site = remote.dbs["glyph-atlas-v3"]
    site.execute("INSERT INTO corpus_rows(crop, seq) VALUES(?, 1)", (g2,))
    site.execute("UPDATE crops SET revision=1, state=3 WHERE id=?", (g2,))
    site.commit()
    run(publish, monkeypatch, write(tmp_path, monkeypatch, "f", [glyph("codh:g2", "き")]))
    assert (crops(remote)[g2]["state"], crops(remote)[g2]["revision"]) == (3, 1)
    assert remote.query("glyph-atlas-v3", "SELECT * FROM corpus_flags") == []
    assert "1 changes kept D1's newer review" in capsys.readouterr().out
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert local.execute("SELECT state, revision FROM crops WHERE id=?", (g2,)).fetchone() == (3, 1)
    assert local.execute("SELECT crop, seq FROM corpus_rows").fetchall() == [(g2, 1)]
    assert local.execute("SELECT count(*) FROM corpus_flags").fetchone() == (0,)
