"""Write `reviewed.jsonl`: crops whose character a person settled on the review site.

A crop counts when its latest review that was not undone checked it (`new` is `reviewed`) by
confirming its label or by correcting its character, one crop at a time, in a round or as a batch.
Its truth is the label the review left the crop with, from the event's `correction`. A corrected
reading, a bad crop, a merged crop or a blank names no character, so it is left out, and a batch
that named a character for a crop already reported for its box leaves it reported. The label and
the image are the ones the reviewer saw, from the event's snapshot; the unit may have changed since.
The input is the site's review events, exported from D1:

    bunx wrangler d1 execute glyph-atlas --remote --json --command "SELECT e.target id,
      json_extract(e.event,'$.new') new, json_extract(e.event,'$.evidence') ev FROM events e
      JOIN submissions s ON s.id=e.submission AND s.undone=0
      WHERE e.kind='review' ORDER BY e.at" | jq -c '.[0].results[]' > events.jsonl
    python models/benchmark/reviewed.py events.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from glyph_atlas import refs


def truths(lines):
    latest = {row["id"]: row for row in map(json.loads, lines)}
    for identity, row in latest.items():
        evidence = json.loads(row["ev"])
        if row["new"] != "reviewed" or not (evidence["verdict"] == "match" or evidence["issue"] == "character"):
            continue
        seen = evidence["snapshot"]["character"]
        truth = refs.from_code_points(evidence["correction"]["unicode"].split())
        if len(truth) == 1:
            yield {"id": identity, "image": seen["image"], "truth": truth, "label": seen["label"],
                   "script": str(refs.script_of(truth)), "corrected": truth != seen["label"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("events", type=Path)
    args = parser.parse_args()
    rows = list(truths(args.events.read_text().splitlines()))
    (Path(__file__).parent / "reviewed.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(json.dumps({"crops": len(rows), "corrected": sum(r["corrected"] for r in rows)}))


if __name__ == "__main__":
    main()
