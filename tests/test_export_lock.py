import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

from glyph_atlas import export_lock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_a_folder_is_held_by_one_process_at_a_time(tmp_path):
    held = export_lock.hold(tmp_path)
    with pytest.raises(SystemExit, match="another process"):
        export_lock.hold(tmp_path)
    held.close()
    export_lock.hold(tmp_path).close()


def test_addressing_waits_for_no_exporter(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS / "atlas3"))
    address_packs = importlib.import_module("address_packs")
    db = sqlite3.connect(tmp_path / "corpus.sqlite")
    db.execute("CREATE TABLE media(key TEXT PRIMARY KEY, object TEXT, offset INTEGER, size INTEGER, content_type TEXT)")
    db.execute("INSERT INTO media VALUES('k','pack-10001.bin',0,4,'image/webp')")
    db.commit()
    (tmp_path / "pack-10001.bin").write_bytes(b"aaaa")
    held = export_lock.hold(tmp_path)
    with pytest.raises(SystemExit, match="another process"):
        address_packs.address(tmp_path / "corpus.sqlite")
    assert (tmp_path / "pack-10001.bin").exists()
    held.close()


@pytest.mark.parametrize("name", ["export_cloudflare", "export_cloudflare_corpus"])
def test_an_export_into_a_held_folder_stops_before_writing(tmp_path, monkeypatch, name):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    monkeypatch.chdir(tmp_path)
    sys.modules.pop(name, None)
    module = importlib.import_module(name)
    output = tmp_path / "out"
    output.mkdir()
    held = export_lock.hold(output)
    with pytest.raises(SystemExit, match="another process"):
        if name == "export_cloudflare":
            module.export(tmp_path / "dataset", output, resume=True)
        else:
            module.export(output, resume=True, corpora=[])
    assert sorted(p.name for p in output.iterdir()) == [export_lock.NAME]
    held.close()
