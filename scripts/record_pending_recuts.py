"""Record the recut a box redrawn on the site waits for, on crops imported before the import recorded it.

    uv run scripts/record_pending_recuts.py DATASET [--live LIVE.jsonl] [--apply]

A crop qualifies when the site still shows its old cut under a redrawn box (`box_pending` on the live
row), that box is the one the store holds, and the store's last import of the crop brought the box
back without a recut. Each gets the event the import now records (`store.RECUT`): it changes no
record and moves the crop's revision past the live one, so the next publication cuts the crop at a
revision no open page holds, and the review made on it after imports on top of it.

The live rows are read from D1 with one read-only query, or from LIVE.jsonl (rows with id, revision
and data, such as `prepare_publication.py` writes). Without `--apply` it reports what it would record
and writes nothing. A crop already recorded is left alone, so running it twice adds nothing.
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from prepare_publication import d1

from glyph_atlas.evidence import crop_version
from glyph_atlas.review import cloudflare_import as bridge
from glyph_atlas.review.characters import _source_digest
from glyph_atlas.review.store import RECUT, Store

#: The live crops waiting on a redrawn box: only these can be pending recuts.
PENDING = ("SELECT id, revision, data FROM units WHERE origin='local' "
           "AND json_extract(data,'$.box_pending') IS NOT NULL AND json_extract(data,'$.box_pending')")


def plan(conn, store, row) -> dict:
    """What to record for one live row: the report item, with the recut as `recut` when it is due."""
    item = {"unit_id": row["id"], "live_revision": int(row["revision"])}
    box = json.loads(row["data"]).get("box")
    last = conn.execute("SELECT remote_id FROM cloudflare_imports WHERE target_id=? ORDER BY local_revision DESC LIMIT 1",
                        (row["id"],)).fetchone()
    unit = store._unit_row(conn, row["id"])
    if last is None or unit is None or not unit.active:
        return {**item, "status": "not-imported"}
    if not conn.execute("SELECT 1 FROM events WHERE id=?", (last["remote_id"] + ":box",)).fetchone():
        return {**item, "status": "no-box"}
    if conn.execute("SELECT 1 FROM events WHERE id=?", (last["remote_id"] + ":" + RECUT,)).fetchone():
        return {**item, "status": "recorded"}
    if unit.box is None or unit.box.model_dump() != box:
        return {**item, "status": "other-box"}
    # The import read these pixels to bring the box back; without them the crop version is unknown.
    if (version := crop_version(unit.id, _source_digest(store, unit), box)) is None:
        return {**item, "status": "no-pixels"}
    recut = {"box": box, "crop_version": version, "published_revision": int(row["revision"])}
    return {**item, "status": "due", "source_event_id": last["remote_id"], "recut": recut}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--live", type=Path, help="live units as JSON lines, instead of reading D1")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    rows = ([json.loads(line) for line in args.live.read_text(encoding="utf-8").splitlines() if line.strip()]
            if args.live else d1(PENDING))
    rows = [row for row in rows if json.loads(row["data"]).get("box_pending")]
    store = Store(args.dataset)
    at = datetime.now(UTC).isoformat()
    items = []
    with store._lock, store._connection() as conn, store._transaction(conn):
        for row in rows:
            item = plan(conn, store, row)
            items.append(item)
            if item["status"] != "due" or not args.apply:
                continue
            remote = {"target_id": row["id"], "actor": bridge.POLICY, "at": at}
            bridge._append(store, conn, remote, RECUT, item["recut"],
                           bridge._json({"policy": bridge.POLICY, "source_event_id": item["source_event_id"]}),
                           item["source_event_id"] + ":" + RECUT, role="model", actor=bridge.POLICY)
            item.update(status="recorded", revision=store._revision(conn, row["id"]))
    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    print(json.dumps({"counts": counts, "apply": args.apply, "items": items}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
