"""Reconcile an exported review set and refine joined detections in the local overlay."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from glyph_atlas.feedback import normalize_export
from glyph_atlas.review.cloudflare_import import bind_remote_outcomes, ingest_cloudflare
from glyph_atlas.review.receipts import FeedbackReceipts, complete_batch, fingerprint
from glyph_atlas.review.refine import refine_feedback, repair_adjacent_labels, scan_joined
from glyph_atlas.review.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("reviews", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scan-limit", type=int, default=0)
    parser.add_argument("--adjacent-limit", type=int, default=0)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--include-processed", action="store_true",
                        help="explicitly reassess feedback already handled by an earlier offline batch")
    args = parser.parse_args()
    store = Store(args.dataset)
    payload = json.loads(args.reviews.read_text(encoding="utf-8"))
    if not args.include_processed:
        pending, _ = FeedbackReceipts(args.dataset).filter(payload.get("reviews", []))
        payload = {**payload, "reviews": pending}
    if args.apply:
        folder = args.dataset / "feedback-backups"
        folder.mkdir(exist_ok=True)
        name = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".sqlite"
        with sqlite3.connect(store.path) as source, sqlite3.connect(folder / name) as backup:
            source.backup(backup)
    bound, imported = ingest_cloudflare(store, payload, apply=args.apply)
    feedback = refine_feedback(store, bound, apply=args.apply)
    bind_remote_outcomes(feedback, imported)
    result = {"feedback": feedback, "cloudflare_import": imported,
              "input": {"source": args.reviews.name,
                        "fingerprints": [fingerprint(record) for record in payload.get("reviews", [])]}}
    records = normalize_export(bound, active_only=True)
    result["supervision"] = {
        "single_characters": [r.as_dict() for r in records if r.train_parent_as_single_char],
        "sequences": [r.as_dict() for r in records if r.decision == "joined" and r.trusted_human],
    }
    if args.scan_limit:
        result["scan"] = scan_joined(store, limit=args.scan_limit, apply=args.apply)
    if args.adjacent_limit:
        result["adjacent"] = repair_adjacent_labels(store, limit=args.adjacent_limit, apply=args.apply)
    # The durable report binds remote records to their canonical local events.
    # Acknowledge both identities in one transaction, so an interrupted batch
    # cannot clear the remote export while leaving its local alias pending.
    aliases = {item["local_event_fingerprint"] for item in feedback["items"] if item.get("local_event_fingerprint")}
    acknowledgement = {**payload, "reviews": [*payload.get("reviews", []),
                        *(record for record in bound["reviews"] if fingerprint(record) in aliases)]}
    result["receipts"] = complete_batch(args.dataset, acknowledgement, result, args.output, apply=args.apply)
    print(json.dumps({**{key: value["counts"] for key, value in result.items() if "counts" in value},
                      "receipts": result["receipts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
