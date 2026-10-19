import importlib
import sqlite3
import sys

import pytest
from test_atlas3_publish import layout  # noqa: F401 -- the fixture


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
