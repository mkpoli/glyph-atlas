import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import publish_collection_status as module

SCHEMA = Path(__file__).resolve().parents[1] / "apps/cloudflare/migrations-atlas"


def test_push_writes_the_status_to_the_new_site_table(monkeypatch):
    calls = []
    monkeypatch.setattr(module, "status", lambda: {"crops": 3, "note": "it's"})
    monkeypatch.setattr(module.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    assert module.push()
    assert calls[0][4] == "glyph-atlas-v3"
    db = sqlite3.connect(":memory:")
    for path in sorted(SCHEMA.glob("*.sql")):
        db.executescript(path.read_text())
    for _ in range(2):  # a second push replaces the row
        db.execute(calls[0][-1])
    assert db.execute("SELECT key, value FROM site").fetchall() == [("collection", '{"crops":3,"note":"it\'s"}')]


def test_push_failure_is_logged_not_raised(monkeypatch, capsys):
    monkeypatch.setattr(module, "status", dict)
    monkeypatch.setattr(module.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "secret"))
    assert not module.push()
    err = capsys.readouterr().err
    assert "not written" in err and "secret" not in err


def test_push_survives_a_missing_wrangler(monkeypatch):
    def boom(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    monkeypatch.setattr(module, "status", dict)
    monkeypatch.setattr(module.subprocess, "run", boom)
    assert not module.push()
