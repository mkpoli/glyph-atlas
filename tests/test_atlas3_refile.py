"""The runs a reference load's moved heads leave behind, filed again (scripts/atlas3/refile.py)."""

import gzip
import importlib
import json
import sqlite3

import pytest
from test_atlas3_publish import counts_match, layout  # noqa: F401 -- `layout` is the fixture
from test_atlas3_reference import both, card, load, sources

# Numbered past the fixture's graphemes: 萅 a head of its own that the load files under 春, 春, and 備 with 备,
# which is filed under 留 and goes to 備.
GRAPHEMES = {"萅": 100, "春": 101, "留": 102, "备": 103, "備": 104, "の": 105}
HEADS = {"萅": "萅", "春": "春", "留": "留", "备": "留", "備": "備", "の": "の"}


def cards(**moved):
    """The cards of the graphemes above, each under the head the load gives it."""
    heads = {**HEADS, "萅": "春", "备": "備", **moved}
    return [card(char, head=heads[char], members=[c for c in heads if heads[c] == heads[char]]) for char in heads]


def spread(root, remote):
    """Five crops of one page, written 春, 萅, 备, 留 and の, and the runs they stand in: 春の and 萅の, 备の under 留の,
    and 留の."""
    crops = dict(zip("abcde", range(9_000, 9_005), strict=True))
    chars = dict(zip("abcde", "春萅备留の", strict=True))
    rows = "".join(f"INSERT INTO graphemes(id, text, head) VALUES({i}, '{c}', {GRAPHEMES[HEADS[c]]});" for c, i in GRAPHEMES.items())
    rows += "".join(f"INSERT INTO crops(id, kind, book, char, state, quiz, shuffle) VALUES({crops[k]}, 0, 1, {GRAPHEMES[chars[k]]}, 0, 1, 0);" for k in crops)
    both(root, remote, rows)
    made = [("a", "春の"), ("b", "萅の"), ("c", "留の"), ("d", "留の")]
    sql = "".join(f"INSERT INTO runs VALUES(2, '{key}', 1, {i}, {crops[k]}, {crops['e']}, NULL, {1 + i % 2});" for i, (k, key) in enumerate(made))
    sql += "INSERT INTO run_stats VALUES(2, '春の', 1, 1), (2, '萅の', 1, 1), (2, '留の', 2, 2);"
    sql += f"INSERT INTO run_written VALUES({crops['c']}, '备');"
    for db in (sqlite3.connect(root / "out" / "atlas-runs.sqlite"), remote.dbs["glyph-atlas-v3-runs"]):
        db.executescript(sql)
        db.commit()
    sources(root, cards=cards())
    return crops


def runs_of(remote, crops):
    db = remote.dbs["glyph-atlas-v3-runs"]
    db.row_factory = None
    return {k: db.execute("SELECT graphemes FROM runs WHERE first=?", (crops[k],)).fetchone()[0] for k in "abcd"}


def local(root):
    return sqlite3.connect(root / "out" / "atlas-runs.sqlite")


def rows_of(db):
    db.row_factory = None
    return [db.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in ("runs", "run_stats", "run_written")]


FILED = {"a": "春の", "b": "春の", "c": "備の", "d": "留の"}


def test_the_runs_of_a_moved_head_and_of_a_member_that_left_one_stand_under_the_new(layout, monkeypatch, capsys):  # noqa: F811
    root, _publish, remote = layout
    crops = spread(root, remote)
    load(remote, monkeypatch, "--send", "--heads")
    d1 = remote.dbs["glyph-atlas-v3-runs"]
    assert runs_of(remote, crops) == FILED
    # Two old keys meet in one new key: their runs add up, each once, and the crops stay as they were written.
    rows, stats = counts_match(d1)
    assert rows == stats and (2, "春の", 2) in rows and (2, "留の", 1) in rows and (2, "備の", 1) in rows
    assert d1.execute("SELECT count(*), count(DISTINCT first) FROM runs WHERE first BETWEEN 9000 AND 9009").fetchone() == (4, 4)
    assert dict(d1.execute("SELECT crop, char FROM run_written WHERE crop BETWEEN 9000 AND 9009")) == {crops["b"]: "萅", crops["c"]: "备"}
    # The local copy holds what D1 does, and the answers of the books and shards are written from it.
    assert rows_of(local(root)) == rows_of(d1)
    grid = json.loads(gzip.decompress((root / "out" / "runs-answers" / "grid-2.json.gz").read_bytes()))
    assert [row[:2] for row in grid if row[0] in ("春の", "萅の", "留の", "備の")] == [["春の", 2], ["備の", 1], ["留の", 1]]
    assert "v3/runs/" in remote.prefixes and local(root).execute("SELECT count(*) FROM answers_owed").fetchone() == (0,)
    said = capsys.readouterr().out
    assert "run keys on 2 moved heads: 留の, 萅の\n" in said
    assert "2 runs moved to another key\n2 run_stats counted up\n2 run_stats counted down\n1 run_stats let go\n" \
           "1 run_written written\n0 run_written dropped\n" in said
    assert said.count("run keys on 2 moved heads: none") == 1
    # What the cached answers are kept under moved after the answers went up.
    stamps = [s for (s,) in remote.dbs["glyph-atlas-v3"].execute("SELECT stamp FROM imports WHERE stamp LIKE 'reference-%' ORDER BY rowid")]
    assert stamps[-1].endswith(".runs")


def test_a_dry_run_counts_what_the_filing_changes_and_changes_nothing(layout, monkeypatch, capsys):  # noqa: F811
    root, _publish, remote = layout
    spread(root, remote)
    before = rows_of(remote.dbs["glyph-atlas-v3-runs"]), rows_of(local(root))
    load(remote, monkeypatch, "--heads")
    assert "2 runs moved to another key" in capsys.readouterr().out
    assert (rows_of(remote.dbs["glyph-atlas-v3-runs"]), rows_of(local(root))) == before


def test_a_load_filed_again_writes_nothing(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    spread(root, remote)
    load(remote, monkeypatch, "--send", "--heads")
    sent, send = [], remote.send
    monkeypatch.setattr(publish, "send", lambda database, file, stamp: sent.append(stamp) or send(database, file, stamp))
    monkeypatch.setattr(remote, "send", lambda database, file, stamp: sent.append(stamp) or send(database, file, stamp))
    remote.uploaded.clear()
    load(remote, monkeypatch, "--send", "--heads")
    assert sent == [] and remote.uploaded == []
    # Nor does filing one already filed, were its mark lost.
    with local(root) as runs:
        runs.execute("DELETE FROM imports WHERE stamp LIKE '%.refiled'")
    importlib.import_module("refile").refile_moved(sqlite3.connect(root / "out" / "atlas.sqlite"))
    assert sent == [] and remote.uploaded == []


@pytest.mark.parametrize("stop", ["before the runs", "between two parts of the runs", "before the answers", "before the stamp"])
def test_a_load_stopped_anywhere_is_finished_by_the_next(layout, monkeypatch, stop):  # noqa: F811
    root, publish, remote = layout
    crops = spread(root, remote)
    real, upload, seen = remote.send, remote.upload, []

    def send(database, file, stamp):
        kind = "refile" if ".refile." in stamp else "stamp" if stamp.endswith(".runs") else "rows"
        seen.append(kind)
        if (stop == "before the runs" and kind == "refile") or (stop == "between two parts of the runs" and seen.count("refile") == 2) \
                or (stop == "before the stamp" and kind == "stamp"):
            raise SystemExit("stopped")
        return real(database, file, stamp)

    def uploads(files, prefix="v3/pages/"):
        if stop == "before the answers" and prefix.startswith("v3/runs/"):
            raise SystemExit("stopped")
        return upload(files, prefix)
    monkeypatch.setattr(publish, "send", send)
    monkeypatch.setattr(publish, "upload", uploads)
    with pytest.raises(SystemExit, match="stopped"):
        load(remote, monkeypatch, "--send", "--heads", batch=1)
    assert remote.dbs["glyph-atlas-v3"].execute("SELECT head FROM graphemes WHERE id=100").fetchone() == (101,), "the heads landed"
    rows, stats = counts_match(remote.dbs["glyph-atlas-v3-runs"])
    assert rows == stats, "a stop leaves every count true"
    monkeypatch.setattr(publish, "send", real)
    monkeypatch.setattr(publish, "upload", upload)
    load(remote, monkeypatch, "--send", "--heads", batch=1)
    assert runs_of(remote, crops) == FILED and rows_of(local(root)) == rows_of(remote.dbs["glyph-atlas-v3-runs"])
    assert counts_match(remote.dbs["glyph-atlas-v3-runs"])[0] == counts_match(remote.dbs["glyph-atlas-v3-runs"])[1]
    assert local(root).execute("SELECT count(*) FROM imports WHERE stamp LIKE '%.refiled'").fetchone() == (1,)


def test_a_run_a_claim_moved_stands_under_the_head_its_claim_reads(layout, monkeypatch):  # noqa: F811
    """The site reads the 留 of d as 萅 since the last publish; its claim names 萅, which is now 春's."""
    root, _publish, remote = layout
    crops = spread(root, remote)
    atlas, runs = remote.dbs["glyph-atlas-v3"], remote.dbs["glyph-atlas-v3-runs"]
    atlas.execute("INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at) "
                  "VALUES('ex:d','has_grapheme','','',?,0,'asserted','U+8405','[]','[]','[]','r','2026-11-25')", (crops["d"],))
    atlas.commit()
    runs.executescript(f"""UPDATE runs SET graphemes='萅の' WHERE first={crops['d']}; UPDATE run_stats SET n=1, down=1 WHERE graphemes='留の';
      UPDATE run_stats SET n=2, down=2 WHERE graphemes='萅の'; INSERT INTO run_written VALUES({crops['d']}, '留');
      INSERT INTO run_moves VALUES({crops['d']}, 2, {crops['d']});""")
    load(remote, monkeypatch, "--send", "--heads")
    assert runs_of(remote, crops) == {**FILED, "d": "春の"}
    rows, stats = counts_match(runs)
    assert rows == stats and (2, "春の", 3) in rows
    assert dict(runs.execute("SELECT crop, char FROM run_written WHERE crop BETWEEN 9000 AND 9009"))[crops["d"]] == "留"
    assert runs.execute("SELECT count(*) FROM run_moves").fetchone() == (1,), "the claim reads it still"
    assert rows_of(local(root)) == rows_of(runs)


def test_a_run_a_claim_moves_while_it_is_filed_is_left_as_the_worker_left_it(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    crops = spread(root, remote)
    runs, real = remote.dbs["glyph-atlas-v3-runs"], remote.send

    def send(database, file, stamp):
        if ".refile." in stamp:
            runs.executescript(f"""UPDATE runs SET graphemes='留の' WHERE first={crops['b']}; DELETE FROM run_stats WHERE graphemes='萅の';
              UPDATE run_stats SET n=3, down=3 WHERE graphemes='留の';""")
        return real(database, file, stamp)
    monkeypatch.setattr(publish, "send", send)
    load(remote, monkeypatch, "--send", "--heads")
    assert runs_of(remote, crops) == {**FILED, "b": "留の"}
    rows, stats = counts_match(runs)
    assert rows == stats and rows_of(local(root)) == rows_of(runs)


def test_the_two_databases_are_written_apart(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    spread(root, remote)
    sent, real = [], remote.send
    monkeypatch.setattr(publish, "send", lambda database, file, stamp: sent.append((database, stamp.split(".")[1])) or real(database, file, stamp))
    load(remote, monkeypatch, "--send", "--heads")
    assert sent == [("glyph-atlas-v3-runs", "refile"), ("glyph-atlas-v3", "runs")]
