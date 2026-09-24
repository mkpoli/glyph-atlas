"""Update units the site already holds from a newer catalogue, without undoing reviews made on it.

A publication inserts units with INSERT OR IGNORE, so a unit already in D1 keeps its old row.
This writes the UPDATE statements that bring such units up to date:

- a unit nobody has reviewed on the site takes the new row, including the catalogue's own
  revision and snapshot, so later site reviews still chain onto the local store's revision;
- a reviewed unit whose crop is unchanged keeps its reviewed state, revision and history, and
  may only leave the quiz: a reviewed crop is never put back into Quick review;
- a reviewed unit whose crop changed is held back and reported. Its events carry revisions from
  the old lineage; a lower catalogue revision would make the site call the old review current and
  the importer reject every new one. It needs a local revision above every event it has first.

A crop is the same when its box, crop box and image (the crop's media key) are unchanged; the
image hash names the page the crop was cut from, so it cannot tell two crops apart.

A replaced unit's new revision must differ from the live one: an open page holding the old row
sends the old revision and is refused. Every statement is guarded by the revision the live row
had when it was read, so a review saved in the meantime is never overwritten; that unit is left
for the next run. JSON is written back in its stored key order, because the Worker compares
`json_extract(data, '$.box')` text with the box a crop was seen with.

    python scripts/refresh_published_units.py CATALOGUE.sqlite LIVE.jsonl OUTPUT.sql

LIVE.jsonl has one object per unit the site holds, all keys required: id, revision, quiz, data,
reviewed. `reviewed` means the unit has any row in the Worker's `events` table:

    SELECT u.id, u.revision, u.quiz, u.data,
           EXISTS(SELECT 1 FROM events e WHERE e.target = u.id) AS reviewed
    FROM units u WHERE u.origin = 'local'
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

COLUMNS = ("origin", "character", "reading", "family", "visual_group", "production", "category",
           "state", "quiz", "priority", "shuffle", "data", "snapshot", "context", "visual")


def quote(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else repr(value)
    return "'" + str(value).replace("'", "''") + "'"


CROP_KEYS = ("box", "crop_box", "image")


def same_crop(new_data: str, live_data: str) -> bool:
    new, live = json.loads(new_data), json.loads(live_data)
    return all(new.get(key) == live.get(key) for key in CROP_KEYS)


class Collision(ValueError):
    """The catalogue's revision equals the live one, so a stale page could not be told apart."""


def plan(new: dict, live: dict) -> tuple[str, str | None]:
    """What to do with one unit: ("skip" | "quiz" | "replace" | "hold", statement)."""
    for key in ("revision", "quiz", "data", "reviewed"):
        if key not in live:
            raise KeyError(f"{new['id']}: the live export lacks {key!r}")
    guard = f" WHERE id={quote(new['id'])} AND revision={int(live['revision'])};"
    if live["reviewed"]:
        if not same_crop(new["data"], live["data"]):
            return "hold", None
        # Only ever out of the quiz: a reviewed (or flagged) crop is not dealt again.
        if int(new["quiz"]) == 0 and int(live["quiz"]) == 1:
            return "quiz", "UPDATE units SET quiz=0" + guard
        return "skip", None
    # The revision is part of the comparison: a unit left at the old lineage's revision could not
    # have its later site reviews imported.
    if new["data"] == live["data"] and int(new["revision"]) == int(live["revision"]) and int(new["quiz"]) == int(live["quiz"]):
        return "skip", None
    if int(new["revision"]) == int(live["revision"]):
        raise Collision(f"{new['id']}: catalogue revision {new['revision']} equals the live one")
    sets = ", ".join(f"{column}={quote(new[column])}" for column in (*COLUMNS, "revision"))
    return "replace", f"UPDATE units SET {sets}" + guard


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("catalogue", type=Path)
    parser.add_argument("live", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    live = {row["id"]: row for row in (json.loads(line) for line in args.live.read_text(encoding="utf-8").splitlines() if line.strip())}
    db = sqlite3.connect(args.catalogue)
    db.row_factory = sqlite3.Row
    counts = {"skip": 0, "quiz": 0, "replace": 0, "hold": 0, "new": 0, "collision": 0}
    held = []
    with args.output.open("w", encoding="utf-8") as out:
        for row in db.execute("SELECT * FROM units"):
            new = dict(row)
            current = live.get(new["id"])
            if current is None:
                counts["new"] += 1  # inserted by the publication itself
                continue
            try:
                action, statement = plan(new, current)
            except Collision as error:
                print(error)
                counts["collision"] += 1
                continue
            counts[action] += 1
            if action == "hold":
                held.append(new["id"])
            if statement:
                out.write(statement + "\n")
    print(json.dumps({**counts, "held": held}, ensure_ascii=False))


if __name__ == "__main__":
    main()
