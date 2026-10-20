import gzip
import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_publish import (
    UNITS,
    layout,  # noqa: F401 -- the fixture
    retired_catalogue,
    retired_unit,
    run,
    write_source,
)


def check(monkeypatch):
    sys.modules.pop("check", None)
    module = importlib.import_module("check")
    monkeypatch.setattr(sys, "argv", ["check.py"])
    with pytest.raises(SystemExit) as stopped:
        module.main()
    return stopped.value.code


def test_a_fresh_build_passes_every_check(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    (root / "out" / "media").mkdir(exist_ok=True)
    assert check(monkeypatch) == 0


@pytest.mark.parametrize("change", ["UPDATE crops SET state = 1 WHERE kind = 0 AND state = 0",
                                    "UPDATE crops SET year = 1999 WHERE kind = 0",
                                    "UPDATE crops SET flags = 1 WHERE kind = 0",
                                    "DELETE FROM crops WHERE kind = 1"])
def test_a_crop_that_differs_from_its_live_record_fails(layout, monkeypatch, change):  # noqa: F811
    root, _publish, _remote = layout
    (root / "out" / "media").mkdir(exist_ok=True)
    db = sqlite3.connect(root / "out" / "atlas.sqlite")
    db.execute(change)
    db.commit()
    assert check(monkeypatch) == 1


@pytest.mark.parametrize("named", [None, "ex:a1"])
def test_a_retired_crop_must_name_its_replacement_by_number(layout, monkeypatch, named):  # noqa: F811
    root, publish, _remote = layout
    (root / "out" / "media").mkdir(exist_ok=True)
    gone = retired_unit("ex:b1", "hl:p2", {"x": 0, "y": 0, "w": 10, "h": 10})
    run(publish, monkeypatch, retired_catalogue(root / "c.sqlite", [gone], [("ex:b1", "ex:gone"), ("ex:gone", "ex:a1")]))
    db = sqlite3.connect(root / "source-units.sqlite")
    db.execute(f"INSERT INTO units VALUES(100,{','.join('?' * len(UNITS))})", gone)
    db.commit()
    write_source(root, "unit_redirects", ("id", "target"), [("ex:b1", "ex:gone"), ("ex:gone", "ex:a1")])
    assert check(monkeypatch) == 0
    if named:
        rev = sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT max(rev) FROM pages").fetchone()[0]
        for path in (root / "out" / "pages").glob(f"*-{rev}.json.gz"):
            body = json.loads(gzip.decompress(path.read_bytes()))
            for e in body["crops"]:
                if e["id"] == "ex:b1":
                    e["replaced_by"] = named
            path.write_bytes(gzip.compress(json.dumps(body).encode()))
        assert check(monkeypatch) == 1
