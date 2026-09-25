"""Write `reviewed.jsonl`: crops whose character a person settled on the review site.

A crop counts when its latest review that was not undone confirms the label, or corrects the
character (`issue` `character` with a written character). A corrected reading, a bad crop, a
merged crop or a blank names no character, so it is left out. The label and the image are the ones
the reviewer saw, from the event's snapshot; the unit may have changed since. The input is the
site's review events, exported from D1:

    bunx wrangler d1 execute glyph-atlas --remote --json --command "SELECT e.target id,
      json_extract(e.event,'$.evidence') ev FROM events e
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
    latest = {}
    for line in lines:
        row = json.loads(line)
        evidence = json.loads(row["ev"])
        request = evidence["request"]
        answer = request if evidence["kind"] == "character-review" else next(
            (a for a in request.get("answers", []) if a["id"] == row["id"]), None)
        if answer is not None:
            latest[row["id"]] = (evidence["snapshot"]["character"], answer)
    for identity, (seen, answer) in latest.items():
        if answer.get("verdict") == "match":
            truth = seen["label"]
        elif answer.get("verdict") == "wrong" and answer.get("issue") == "character" and answer.get("character"):
            truth = answer["character"]
        else:
            continue
        if truth and len(truth) == 1:
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
