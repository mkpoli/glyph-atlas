"""Write D1 SQL that replaces `unit_suspects` with computed Quick review suspect marks.

The inputs are marks files: `atlas review catalogue-suspects` over the sealed publications the hosted
site serves (their `atlas.sqlite`), and `atlas review corpus-suspects` for the corpus glyphs. A marks
file for one dataset (`atlas review suspects <dataset>`) also fits, when that dataset is what was
published. The output replaces the whole table; run
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
    """Files come in precedence order: a crop an earlier file scored keeps that file's answer, marked or not.

    A file lists what it scored in `scored_ids` (`catalogue-suspects` writes it); without the list only
    its marks count as scored. So the hosted catalogue's marks go first and the corpus glyphs' last:
    a glyph published as a local crop is judged as the crop the site serves.
    """
    marks: dict[str, dict] = {}
    scored: set[str] = set()
    for path in files:
        document = json.loads(path.read_text())
        for identity, mark in document["suspects"].items():
            if identity in scored:
                continue
            p, reads = mark.get("p"), mark.get("reads_as")
            if not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1:
                raise ValueError(f"{identity}: a suspect's probability must be a number from 0 to 1")
            if reads is not None and not isinstance(reads, str):
                raise ValueError(f"{identity}: reads_as must be text or null")
            label, box = mark.get("label"), mark.get("box")
            if not isinstance(label, str) or not label:
                raise ValueError(f"{identity}: a suspect names the label it was judged under")
            if box is not None and not (isinstance(box, dict) and sorted(box) == ["h", "w", "x", "y"]
                                        and all(isinstance(v, (int, float)) for v in box.values())):
                raise ValueError(f"{identity}: box must be x, y, w and h, or null")
            marks[identity] = mark
        scored |= set(document.get("scored_ids") or document["suspects"])
    lines = ["DELETE FROM unit_suspects;"]
    for identity, mark in sorted(marks.items()):
        box = json.dumps(mark["box"], sort_keys=True) if mark["box"] is not None else None
        lines.append(f"INSERT INTO unit_suspects VALUES({quoted(identity)},{mark['p']!r},{quoted(mark.get('reads_as'))},"
                     f"{quoted(mark['label'])},{quoted(box)});")
    out.write_text("\n".join(lines) + "\n")
    return len(marks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("marks", type=Path, nargs="+", help="quiz-suspects.json files, e.g. <export>/source/quiz-suspects.json")
    args = parser.parse_args()
    print(json.dumps({"unit_suspects": export(args.marks, args.out)}))
