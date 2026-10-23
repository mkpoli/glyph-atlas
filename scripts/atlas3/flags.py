"""Write the catalogue that sends the corpus glyphs `atlas repair relabel` left for review to the site's
Flagged view.

    python3 scripts/atlas3/flags.py REVIEW-IDS.json <flags.sqlite>
    python3 scripts/atlas3/publish.py <flags.sqlite> --dry-run
    python3 scripts/atlas3/publish.py <flags.sqlite>

A relabel keeps the label of a box whose realigned character disagrees with a form a person decided
(`box_relabel.relabel`, status `review`); a republication lists those glyphs in a `review-ids.json`
(id, label, realigned, form). Each becomes a `corpus_flags` row with the finding `box-relabel`: the
character the site shows for it (the decided form), the realigned character and the transcribed one.
The publish flags each glyph that nothing was written about yet, so a list published again writes
nothing.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

FINDING = "box-relabel"
COLUMNS = ("id", "character", "finding", "realigned", "transcribed")


def glyph(entry: dict) -> tuple:
    """One listed glyph's row. The form a person decided is the character the site shows for it, so the
    finding holds while the site still shows that character."""
    form = entry.get("form")
    if not form or form.startswith("U+"):
        raise ValueError(f"{entry['id']}: the decided form is not one character ({form!r}), so the shown character is unknown")
    return entry["id"], form, FINDING, entry.get("realigned"), entry.get("label")


def rows(entries: list[dict]) -> list[tuple]:
    found = [glyph(entry) for entry in entries]
    if len({r[0] for r in found}) != len(found):
        raise ValueError("a glyph is listed twice")
    return found


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("review_ids", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    if args.output.exists():
        sys.exit(f"{args.output} exists; the flags are written into a new file")
    found = rows(json.loads(args.review_ids.read_text()))
    db = sqlite3.connect(args.output)
    db.execute("CREATE TABLE corpus_flags(id TEXT PRIMARY KEY, character TEXT NOT NULL, finding TEXT NOT NULL, realigned TEXT, transcribed TEXT)")
    db.executemany(f"INSERT INTO corpus_flags({','.join(COLUMNS)}) VALUES({','.join('?' * len(COLUMNS))})", found)
    db.commit()
    print(f"{len(found)} glyphs -> {args.output}", flush=True)


if __name__ == "__main__":
    main()
