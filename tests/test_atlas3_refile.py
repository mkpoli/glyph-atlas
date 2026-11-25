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


def run_of(root, remote, key, first, second, written=(), shuffle=50):
    """One more run of two crops, filed under `key`, in D1 and the local copy."""
    sql = f"INSERT INTO runs VALUES(2, '{key}', 1, {shuffle}, {first}, {second}, NULL, 1);"
    sql += f"INSERT INTO run_stats VALUES(2, '{key}', 1, 1) ON CONFLICT(size, graphemes) DO UPDATE SET n=n+1, down=down+1;"
    sql += "".join(f"INSERT INTO run_written VALUES({crop}, '{char}');" for crop, char in written)
    for db in (sqlite3.connect(root / "out" / "atlas-runs.sqlite"), remote.dbs["glyph-atlas-v3-runs"]):
        db.executescript(sql)
        db.commit()


def crop_of(root, remote, crop, char, kind=0):
    """One more crop of the page; `char` None for a corpus glyph known by its label alone."""
    both(root, remote, f"INSERT INTO crops(id, kind, book, char, state, quiz, shuffle) VALUES({crop}, {kind}, 1, {GRAPHEMES[char] if char else 'NULL'}, 0, 1, 0);")


def claim(remote, crop, text):
    remote.dbs["glyph-atlas-v3"].execute(
        "INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at) "
        "VALUES(?,'has_grapheme','','',?,0,'asserted',?,'[]','[]','[]','r','2026-11-25')", (f"ex:{crop}", crop, f"U+{ord(text):04X}"))
    remote.dbs["glyph-atlas-v3"].commit()


def worker_moves(remote, crop, old, new, written=None):
    """A run the Worker files under another key, its counts and the crop's row with it."""
    remote.dbs["glyph-atlas-v3-runs"].executescript(f"""UPDATE runs SET graphemes='{new}' WHERE first={crop} AND graphemes='{old}';
      INSERT INTO run_stats VALUES(2, '{new}', 1, 1) ON CONFLICT(size, graphemes) DO UPDATE SET n=n+1, down=down+1;
      UPDATE run_stats SET n=n-1, down=down-1 WHERE graphemes='{old}'; DELETE FROM run_stats WHERE n<=0;"""
                                                         + (f"INSERT OR REPLACE INTO run_written VALUES({crop}, '{written}');" if written else ""))


def before_the_send(remote, monkeypatch, publish, once):
    """Runs `once` after the runs are read, before they are sent the first time."""
    real, done = remote.send, []

    def send(database, file, stamp):
        if ".refile." in stamp and not done:
            done.append(once())
        return real(database, file, stamp)
    monkeypatch.setattr(publish, "send", send)


def held_by(remote):
    return dict(remote.dbs["glyph-atlas-v3-runs"].execute("SELECT crop, char FROM run_written"))


def consistent(root, remote):
    rows, stats = counts_match(remote.dbs["glyph-atlas-v3-runs"])
    assert rows == stats and rows_of(local(root)) == rows_of(remote.dbs["glyph-atlas-v3-runs"])


#: The limits D1 sets its databases.
D1_LIMITS = {sqlite3.SQLITE_LIMIT_EXPR_DEPTH: 100, sqlite3.SQLITE_LIMIT_COMPOUND_SELECT: 5, sqlite3.SQLITE_LIMIT_SQL_LENGTH: 100_000}


def test_a_load_that_moves_many_keys_stays_within_the_limits_of_d1(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    crops = spread(root, remote)
    others = [chr(0x4F00 + i) for i in range(150)]
    both(root, remote, "".join(f"INSERT INTO graphemes(id, text, head) VALUES({200 + i}, '{c}', {200 + i});" for i, c in enumerate(others)))
    for i, char in enumerate(others):
        crop_of(root, remote, 9_100 + i, "萅")
        run_of(root, remote, f"萅{char}", 9_100 + i, crops["e"], shuffle=100 + i)
    sources(root, cards=[*cards(), *(card(c) for c in others)])
    for db in remote.dbs.values():
        for limit, value in D1_LIMITS.items():
            db.setlimit(limit, value)
    load(remote, monkeypatch, "--send", "--heads")
    consistent(root, remote)
    d1 = remote.dbs["glyph-atlas-v3-runs"]
    assert d1.execute("SELECT count(*) FROM runs WHERE graphemes LIKE '春%' AND first >= 9100").fetchone() == (150,)
    assert d1.execute("SELECT count(*) FROM runs WHERE graphemes LIKE '萅%'").fetchone() == (0,)


def test_a_claim_filed_before_the_send_keeps_what_it_wrote_when_the_load_would_drop_it(layout, monkeypatch):  # noqa: F811
    """春 stands under 萅 and goes to 春, where its row in `run_written` is dropped; a claim files it under 留 first."""
    root, publish, remote = layout
    spread(root, remote)
    both(root, remote, "UPDATE graphemes SET head=100 WHERE id=101;")
    run_of(root, remote, "萅の", 9_020, 9_004, written=[(9_020, "春")])
    crop_of(root, remote, 9_020, "春")
    before_the_send(remote, monkeypatch, publish, lambda: (worker_moves(remote, 9_020, "萅の", "留の", "春"), claim(remote, 9_020, "留")))
    load(remote, monkeypatch, "--send", "--heads")
    assert remote.dbs["glyph-atlas-v3-runs"].execute("SELECT graphemes FROM runs WHERE first=9020").fetchone() == ("留の",)
    assert held_by(remote)[9_020] == "春"
    consistent(root, remote)


def test_a_claim_filed_before_the_send_is_not_overwritten(layout, monkeypatch):  # noqa: F811
    """The load would write 萅 for b, whose run the Worker has filed under 留 with the row it wrote."""
    root, publish, remote = layout
    crops = spread(root, remote)
    before_the_send(remote, monkeypatch, publish, lambda: (worker_moves(remote, crops["b"], "萅の", "留の", "の"), claim(remote, crops["b"], "留")))
    load(remote, monkeypatch, "--send", "--heads")
    assert runs_of(remote, crops)["b"] == "留の" and held_by(remote)[crops["b"]] == "の"
    consistent(root, remote)


def test_a_run_whose_other_crop_a_claim_moved_meanwhile_is_filed_again(layout, monkeypatch):  # noqa: F811
    """The Worker reads the の of 萅の as 留 and leaves the 萅 beside it, which the load moves to 春."""
    root, publish, remote = layout
    crops = spread(root, remote)
    before_the_send(remote, monkeypatch, publish, lambda: (worker_moves(remote, crops["b"], "萅の", "萅留"), claim(remote, crops["e"], "留")))
    load(remote, monkeypatch, "--send", "--heads")
    assert runs_of(remote, crops)["b"] == "春留" and held_by(remote)[crops["b"]] == "萅"
    consistent(root, remote)
    assert local(root).execute("SELECT count(*) FROM imports WHERE stamp LIKE '%.refiled'").fetchone() == (1,)


def test_a_load_whose_runs_never_stand_still_is_left_for_the_next(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    crops = spread(root, remote)
    real, keys = remote.send, iter(["萅留", "萅の", "萅留", "萅の", "萅留", "萅の"])

    def send(database, file, stamp):
        if ".refile." in stamp:
            (old,) = remote.dbs["glyph-atlas-v3-runs"].execute("SELECT graphemes FROM runs WHERE first=?", (crops["b"],)).fetchone()
            worker_moves(remote, crops["b"], old, next(keys))
        return real(database, file, stamp)
    monkeypatch.setattr(publish, "send", send)
    with pytest.raises(SystemExit, match="1 runs were moved by other writes"):
        load(remote, monkeypatch, "--send", "--heads")
    assert local(root).execute("SELECT count(*) FROM sqlite_master WHERE name='imports'").fetchone() == (1,)
    assert local(root).execute("SELECT count(*) FROM imports WHERE stamp LIKE '%.refiled'").fetchone() == (0,)
    monkeypatch.setattr(publish, "send", real)
    load(remote, monkeypatch, "--send", "--heads")
    assert runs_of(remote, crops)["b"].startswith("春")
    consistent(root, remote)


def test_a_corpus_glyph_known_by_its_label_alone_is_filed_and_written_by_it(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    spread(root, remote)
    # 萅, the head its run names, and 备, which its row says, under 留.
    for crop in (9_030, 9_031):
        crop_of(root, remote, crop, None, kind=1)
    run_of(root, remote, "萅の", 9_030, 9_004, shuffle=60)
    run_of(root, remote, "留の", 9_031, 9_004, written=[(9_031, "备")], shuffle=61)
    load(remote, monkeypatch, "--send", "--heads")
    d1 = remote.dbs["glyph-atlas-v3-runs"]
    assert [d1.execute("SELECT graphemes FROM runs WHERE first=?", (c,)).fetchone()[0] for c in (9_030, 9_031)] == ["春の", "備の"]
    assert {c: held_by(remote).get(c) for c in (9_030, 9_031)} == {9_030: "萅", 9_031: "备"}
    consistent(root, remote)


LABELLED = 9_040


def labelled(root, remote, others):
    """A corpus glyph with no character its row calls 春, under 萅, and a run with each of `others`, before it or after:
    the load gives 春 its own head and leaves 萅."""
    both(root, remote, "UPDATE graphemes SET head=100 WHERE id=101;")
    sources(root, cards=[c for c in cards(萅="萅", 春="春")])
    crop_of(root, remote, LABELLED, None, kind=1)
    for i, (crop, before) in enumerate(others):
        crop_of(root, remote, crop, "の")
        run_of(root, remote, *(("の萅", crop, LABELLED) if before else ("萅の", LABELLED, crop)), shuffle=70 + i, written=[(LABELLED, "春")] if not i else ())


@pytest.mark.parametrize("batch", [1, 100])
@pytest.mark.parametrize("others", [[(8_990, True), (9_050, False)], [(9_050, False), (9_060, True)]])
def test_a_glyph_known_by_its_label_is_filed_in_every_batch_its_runs_are_in(layout, monkeypatch, others, batch):  # noqa: F811
    root, _publish, remote = layout
    spread(root, remote)
    labelled(root, remote, others)
    load(remote, monkeypatch, "--heads")
    load(remote, monkeypatch, "--send", "--heads", batch=batch)
    keys = {r[0] for r in remote.dbs["glyph-atlas-v3-runs"].execute("SELECT graphemes FROM runs WHERE first=? OR second=?", (LABELLED, LABELLED))}
    assert keys == {"の春", "春の"} and held_by(remote)[LABELLED] == "春"
    consistent(root, remote)


def test_a_glyph_known_by_its_label_is_filed_though_the_worker_moves_one_of_its_runs_meanwhile(layout, monkeypatch):  # noqa: F811
    """The Worker reads the の after the glyph as 留 between the read and the send; the glyph's part of that run is filed next."""
    root, publish, remote = layout
    spread(root, remote)
    labelled(root, remote, [(8_990, True), (9_050, False)])
    before_the_send(remote, monkeypatch, publish, lambda: (worker_moves(remote, LABELLED, "萅の", "萅留"), claim(remote, 9_050, "留")))
    load(remote, monkeypatch, "--send", "--heads")
    keys = {r[0] for r in remote.dbs["glyph-atlas-v3-runs"].execute("SELECT graphemes FROM runs WHERE first=? OR second=?", (LABELLED, LABELLED))}
    assert keys == {"の春", "春留"} and held_by(remote)[LABELLED] == "春"
    consistent(root, remote)


def test_a_crop_a_claim_reads_as_a_member_that_moves_goes_with_it(layout, monkeypatch, capsys):  # noqa: F811
    """d is a 留 a claim reads as 备, which stands under 留 and goes to 備: neither its run's key nor its character moves."""
    root, _publish, remote = layout
    crops = spread(root, remote)
    claim(remote, crops["d"], "备")
    load(remote, monkeypatch, "--heads")
    assert "3 runs moved to another key" in capsys.readouterr().out, "a dry run counts it, the local heads unmoved"
    load(remote, monkeypatch, "--send", "--heads")
    assert runs_of(remote, crops) == {**FILED, "d": "備の"} and held_by(remote)[crops["d"]] == "留"
    consistent(root, remote)


def test_a_load_stopped_before_its_new_heads_are_replayed_is_finished(layout, monkeypatch):  # noqa: F811
    """D1 has numbered 旾, which 萅 goes to, and the local copy has not."""
    root, publish, remote = layout
    crops = spread(root, remote)
    sources(root, cards=[*(c for c in cards(萅="旾") if c[0] != "U+65FE"), card("旾", members=["旾", "萅"])])
    real = remote.send

    def send(database, file, stamp):
        done = real(database, file, stamp)
        if database == "glyph-atlas-v3" and stamp.startswith("reference-"):
            raise SystemExit("stopped")
        return done
    monkeypatch.setattr(remote, "send", send)
    with pytest.raises(SystemExit, match="stopped"):
        load(remote, monkeypatch, "--send", "--heads", group=1, batch=1)
    local_atlas = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert local_atlas.execute("SELECT count(*) FROM graphemes WHERE text='旾'").fetchone() == (0,)
    monkeypatch.setattr(remote, "send", real)
    load(remote, monkeypatch, "--send", "--heads", group=1, batch=1)
    assert runs_of(remote, crops)["b"] == "旾の"
    consistent(root, remote)


def test_loads_left_to_file_are_filed_in_the_order_of_their_generations(layout, monkeypatch):  # noqa: F811
    """The ninth moves 萅 to 春 and the tenth back to 萅: by name the tenth comes first."""
    root, _publish, remote = layout
    crops = spread(root, remote)
    load(remote, monkeypatch, "--send", "--heads")
    with local(root) as runs:
        runs.execute("DELETE FROM imports WHERE stamp LIKE '%.refiled'")
    worker_moves(remote, crops["b"], "春の", "萅の")
    atlas, folder = sqlite3.connect(root / "out" / "atlas.sqlite"), root / "out" / "reference-load"
    for old in folder.glob("reference-*"):
        old.rename(folder / f"kept-{old.name}")
    for name, heads in (("reference-9-aa", {"萅": ["萅", "春"]}), ("reference-10-bb", {"萅": ["春", "萅"]})):
        (folder / name).mkdir()
        (folder / name / "0000.sql").write_text("")
        (folder / name / "heads.json").write_text(json.dumps(heads))
        for db in (atlas, remote.dbs["glyph-atlas-v3"]):
            db.execute("INSERT INTO imports VALUES(?, 0)", (f"{name}.0000",))
            db.commit()
    refile = importlib.import_module("refile")
    assert [run.name for run in refile.loads()] == ["reference-9-aa", "reference-10-bb"]
    refile.refile_moved(atlas)
    assert runs_of(remote, crops)["b"] == "萅の"
    consistent(root, remote)


def test_the_two_databases_are_written_apart(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    spread(root, remote)
    sent, real = [], remote.send
    monkeypatch.setattr(publish, "send", lambda database, file, stamp: sent.append((database, stamp.split(".")[1])) or real(database, file, stamp))
    load(remote, monkeypatch, "--send", "--heads")
    assert sent == [("glyph-atlas-v3-runs", "refile"), ("glyph-atlas-v3", "runs")]
