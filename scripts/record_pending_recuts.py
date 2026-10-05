"""Record the recut a box redrawn on the site waits for, on crops imported before the import recorded it.

    uv run scripts/record_pending_recuts.py DATASET [--live LIVE.jsonl] [--apply]

A crop qualifies when the site still shows its old cut under a redrawn box (`box_pending` on the live
row), that box is the one the store holds and an import of the crop brought it back, the store's last
import of the crop reached the live revision, and no recut of that cut moves past it yet. A crop with
reviews saved on the site since its last import is reported as `import-first`: import them, and the
import records the recut. Each gets the event the import now records (`store.RECUT`): it changes no
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
    last = conn.execute("SELECT remote_id, remote_revision FROM cloudflare_imports WHERE target_id=? "
                        "ORDER BY local_revision DESC LIMIT 1", (row["id"],)).fetchone()
    unit = store._unit_row(conn, row["id"])
    if last is None or unit is None or not unit.active:
        return {**item, "status": "not-imported"}
    # A review saved on the site since the last import is not in the store: the cut would drop it.
    if last["remote_revision"] != int(row["revision"]):
        return {**item, "status": "import-first"}
    if unit.box is None or unit.box.model_dump() != box:
        return {**item, "status": "other-box"}
    # Any import of the crop may have brought the box back; later imports on the old cut carry no box.
    brought = [json.loads(new) for new, in conn.execute(
        "SELECT e.new FROM events e JOIN cloudflare_imports c ON e.id = c.remote_id || ':box' WHERE c.target_id=?",
        (row["id"],))]
    if box not in brought:
        return {**item, "status": "no-box"}
    # The import read these pixels to bring the box back; without them the crop version is unknown.
    if (version := crop_version(unit.id, _source_digest(store, unit), box)) is None:
        return {**item, "status": "no-pixels"}
    asked = conn.execute("SELECT new FROM events WHERE target_id=? AND field=? ORDER BY seq DESC LIMIT 1",
                         (row["id"], RECUT)).fetchone()
    if asked and (recorded := json.loads(asked["new"]))["crop_version"] == version \
            and recorded["published_revision"] >= int(row["revision"]):
        return {**item, "status": "recorded"}
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
