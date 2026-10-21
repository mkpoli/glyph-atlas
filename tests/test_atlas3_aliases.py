import gzip
import hashlib
import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "atlas3"))


@pytest.fixture
def data(tmp_path, monkeypatch):
    """A mirror and a redirect table under $ATLAS3_DIR; returns a function that runs the script."""
    monkeypatch.setenv("ATLAS3_DIR", str(tmp_path))
    (tmp_path / "out").mkdir()
    mirror = sqlite3.connect(tmp_path / "out" / "mirror.sqlite")
    mirror.execute("CREATE TABLE ids(text TEXT PRIMARY KEY, id INTEGER NOT NULL UNIQUE) WITHOUT ROWID")
    mirror.executemany("INSERT INTO ids VALUES(?, ?)", [("ex:a1", 101), ("ex:a2", 102), ("ex:old", 103)])
    mirror.commit()
    mirror.close()

    def run(redirects, *argv):
        path = tmp_path / "source-unit_redirects.sqlite"
        path.unlink(missing_ok=True)
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE unit_redirects(id, target)")
        db.executemany("INSERT INTO unit_redirects VALUES(?, ?)", redirects)
        db.commit()
        db.close()
        for name in ("build", "publish", "media_map", "aliases"):
            sys.modules.pop(name, None)
        aliases = importlib.import_module("aliases")
        sent = []
        publish = importlib.import_module("publish")
        monkeypatch.setattr(publish, "upload", lambda files, prefix: sent.append(([f.name for f in files], prefix)))
        monkeypatch.setattr(sys, "argv", ["aliases.py", *argv])
        aliases.main()
        return aliases, sent

    return tmp_path, run


def shards(root):
    found = {}
    for path in (root / "out" / "aliases").glob("*.json.gz"):
        found.update(json.loads(gzip.decompress(path.read_bytes())))
    return found


def test_a_shard_is_named_by_the_first_three_hex_digits_of_the_text_ids_sha256(data):
    root, run = data
    aliases, sent = run([], "--send")
    for text in ("ex:a1", "ex:a2", "ex:old"):
        name = hashlib.sha256(text.encode()).hexdigest()[:3]
        assert aliases.shard_name(text) == name
        assert json.loads(gzip.decompress((root / "out" / "aliases" / f"{name}.json.gz").read_bytes()))[text] > 100
    assert sent == [(sorted({f"{aliases.shard_name(t)}.json.gz" for t in ("ex:a1", "ex:a2", "ex:old")}), "v3/aliases/")]
    assert not list((root / "out" / "aliases").glob("*.part"))


def test_a_rerun_uploads_only_the_shards_that_changed(data):
    root, run = data
    run([], "--send")
    _aliases, sent = run([], "--send")
    assert sent == [([], "v3/aliases/")]
    aliases, sent = run([("hk:d", "ex:a1")], "--send")
    assert sent == [([f"{aliases.shard_name('hk:d')}.json.gz"], "v3/aliases/")]
    assert not (root / "out" / "aliases" / "unsent").exists()


def test_a_chain_resolves_to_the_first_text_id_the_mirror_has(data, capsys):
    root, run = data
    # hk:c -> hk:b -> ex:old, which the mirror has; ex:old's own redirect is not followed.
    run([("hk:c", "hk:b"), ("hk:b", "ex:old"), ("ex:old", "ex:a2"), ("hk:d", "ex:a1")], "--send")
    assert shards(root) == {"ex:a1": 101, "ex:a2": 102, "ex:old": 103, "hk:b": 103, "hk:c": 103, "hk:d": 101}
    out = capsys.readouterr().out
    assert "1 redirects already in the mirror" in out
    assert "3 redirected\n" in out
    assert "1 redirected through more than one step" in out


def test_a_chain_ending_outside_the_mirror_is_gone_and_left_out(data, capsys):
    root, run = data
    run([("hk:x", "hk:y"), ("hk:y", "hk:missing"), ("hk:d", "ex:a1")], "--send")
    found = shards(root)
    assert "hk:x" not in found and "hk:y" not in found and found["hk:d"] == 101
    out = capsys.readouterr().out
    assert "2 gone" in out and "hk:x, hk:y" in out


def test_a_cycle_exits_1_with_examples_and_writes_nothing(data):
    root, run = data
    with pytest.raises(SystemExit) as stop:
        run([("hk:p", "hk:q"), ("hk:q", "hk:r"), ("hk:r", "hk:q"), ("hk:d", "ex:a1")], "--send")
    assert stop.value.code != 0 and "hk:q -> hk:r -> hk:q" in str(stop.value.code)
    assert not (root / "out" / "aliases").exists()


def test_without_send_nothing_is_written_or_uploaded(data, capsys):
    root, run = data
    _aliases, sent = run([("hk:d", "ex:a1")])
    assert sent == [] and not (root / "out" / "aliases").exists()
    out = capsys.readouterr().out
    assert "4 aliases in" in out and "largest" in out and "dry run" in out
