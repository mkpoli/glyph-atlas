import importlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "atlas3"))


def test_cards_are_read_by_bytes_and_a_row_past_the_bound_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS3_DIR", str(tmp_path))
    sys.modules.pop("pull", None)
    pull = importlib.import_module("pull")
    live = sqlite3.connect(":memory:")
    live.row_factory = sqlite3.Row
    live.execute("CREATE TABLE characters(code_point PRIMARY KEY, character, name, data, detail)")
    sizes = [100, 300, 2500, 100, 100, 100, 900, 50]
    live.executemany("INSERT INTO characters VALUES(?,?,?,?,?)",
                     [(f"U+{0x3042 + i:04X}", chr(0x3042 + i), "", json.dumps({"grapheme": {"code_point": "U+3042"}}), "x" * n)
                      for i, n in enumerate(sizes)])
    reads = []

    def query(sql):
        found = [dict(r) for r in live.execute(sql)]
        if "_bytes" not in sql:
            reads.append([len(r["data"]) + len(r["detail"]) for r in found])
        return found

    monkeypatch.setattr(pull, "query", query)
    monkeypatch.setattr(pull, "READ_BYTES", 1000)
    pull.pull("characters", fresh=True)
    copied = sqlite3.connect(tmp_path / "source-characters.sqlite")
    assert [len(r[0]) for r in copied.execute("SELECT detail FROM characters ORDER BY code_point")] == sizes
    assert copied.execute("SELECT DISTINCT head FROM characters").fetchall() == [("U+3042",)]
    # Each read stays within the bound, but for the row larger than it, which is read alone.
    assert [len(r) for r in reads] == [2, 1, 3, 1, 1]
    assert all(sum(r) <= 1000 for r in reads if len(r) > 1)
