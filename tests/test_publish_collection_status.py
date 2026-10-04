import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_publication
import publish_collection_status as module


def test_push_writes_the_status_row(monkeypatch):
    calls = []
    monkeypatch.setattr(prepare_publication, "status_row", lambda: "ROW")
    monkeypatch.setattr(module.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    assert module.push()
    assert calls[0][-2:] == ["--command", "ROW"]


def test_push_failure_is_logged_not_raised(monkeypatch, capsys):
    monkeypatch.setattr(prepare_publication, "status_row", lambda: "ROW")
    monkeypatch.setattr(module.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "secret"))
    assert not module.push()
    err = capsys.readouterr().err
    assert "not written" in err and "secret" not in err


def test_push_survives_a_missing_wrangler(monkeypatch):
    def boom(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    monkeypatch.setattr(prepare_publication, "status_row", lambda: "ROW")
    monkeypatch.setattr(module.subprocess, "run", boom)
    assert not module.push()
