"""Write D1 SQL that replaces `unit_suspects` with computed Quick review suspect marks.

The inputs are `quiz-suspects.json` files: the one `atlas review suspects <dataset>` writes into the
dataset the published catalogue was exported from (its frozen `source/` copy), and the one
`atlas review corpus-suspects` writes for the corpus glyphs. The output replaces the whole table; run
it with `wrangler d1 execute glyph-atlas --remote --file <out>` after migration 0017 is applied.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def quoted(value: str | None) -> str:
    return "NULL" if value is None else "'{}'".format(value.replace("'", "''"))


def export(files: list[Path], out: Path) -> int:
    marks: dict[str, dict] = {}
    for path in files:
        for identity, mark in json.loads(path.read_text())["suspects"].items():
            p, reads = mark.get("p"), mark.get("reads_as")
            if not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1:
                raise ValueError(f"{identity}: a suspect's probability must be a number from 0 to 1")
            if reads is not None and not isinstance(reads, str):
                raise ValueError(f"{identity}: reads_as must be text or null")
            if identity in marks and marks[identity] != mark:
                raise ValueError(f"{identity}: marked differently by two files")
            marks[identity] = mark
    lines = ["DELETE FROM unit_suspects;"]
    for identity, mark in sorted(marks.items()):
        lines.append(f"INSERT INTO unit_suspects VALUES({quoted(identity)},{mark['p']!r},{quoted(mark.get('reads_as'))});")
    out.write_text("\n".join(lines) + "\n")
    return len(marks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("marks", type=Path, nargs="+", help="quiz-suspects.json files, e.g. <export>/source/quiz-suspects.json")
    args = parser.parse_args()
    print(json.dumps({"unit_suspects": export(args.marks, args.out)}))
