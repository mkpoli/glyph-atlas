"""Update units the site already holds from a newer catalogue, without undoing reviews made on it.

A publication inserts units with INSERT OR IGNORE, so a unit already in D1 keeps its old row.
This writes the UPDATE statements that bring such units up to date:

- a unit nobody has reviewed on the site takes the new row, with its revision bumped so an
  open page holding the old row is refused instead of overwriting it;
- a reviewed unit whose crop is unchanged keeps its reviewed state, revision and history, and
  takes only the new quiz flag;
- a reviewed unit whose crop changed takes the new row, since its reviews are about a crop that
  no longer exists; they stay in the journal as history.

Every statement is guarded by the revision the live row had when it was read, so a review saved
in the meantime is never overwritten; that unit is simply left for the next run.

    python scripts/refresh_published_units.py CATALOGUE.sqlite LIVE.jsonl OUTPUT.sql

LIVE.jsonl has one object per unit the site holds: id, revision, quiz, data, reviewed.
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


def with_revision(text: str, revision: int) -> str:
    data = json.loads(text)
    data["revision"] = revision
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def unrevised(text: str) -> dict:
    data = json.loads(text)
    data.pop("revision", None)
    return data


def plan(new: dict, live: dict) -> tuple[str, str | None]:
    """What to do with one unit: ("skip" | "quiz" | "replace", statement)."""
    guard = f" WHERE id={quote(new['id'])} AND revision={int(live['revision'])};"
    same_crop = json.loads(new["data"]).get("image_sha256") == json.loads(live["data"]).get("image_sha256")
    if not live.get("reviewed") and unrevised(new["data"]) == unrevised(live["data"]) and int(new["quiz"]) == int(live["quiz"]):
        return "skip", None
    if live.get("reviewed") and same_crop:
        if int(new["quiz"]) == int(live["quiz"]):
            return "skip", None
        return "quiz", f"UPDATE units SET quiz={int(new['quiz'])}" + guard
    revision = int(live["revision"]) + 1
    values = dict(new, revision=revision, data=with_revision(new["data"], revision))
    sets = ", ".join(f"{column}={quote(values[column])}" for column in (*COLUMNS, "revision"))
    return "replace", f"UPDATE units SET {sets}" + guard


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("catalogue", type=Path)
    parser.add_argument("live", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    live = {row["id"]: row for row in map(json.loads, args.live.read_text(encoding="utf-8").splitlines()) if row}
    db = sqlite3.connect(args.catalogue)
    db.row_factory = sqlite3.Row
    counts = {"skip": 0, "quiz": 0, "replace": 0, "new": 0}
    with args.output.open("w", encoding="utf-8") as out:
        for row in db.execute("SELECT * FROM units"):
            new = dict(row)
            current = live.get(new["id"])
            if current is None:
                counts["new"] += 1  # inserted by the publication itself
                continue
            action, statement = plan(new, current)
            counts[action] += 1
            if statement:
                out.write(statement + "\n")
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
