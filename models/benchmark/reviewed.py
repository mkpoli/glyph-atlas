"""Write `reviewed.jsonl`: crops whose character a person settled on the review site.

A crop counts when its latest review confirms the label, or marks it a wrong reading and names one
character. Reports of a bad crop, a merged crop, a blank, or a wrong reading with no character name
no truth, so they are left out. The input is the site's review events, exported from D1:

    bunx wrangler d1 execute glyph-atlas --remote --json --command "SELECT e.target id,
      u.character c, json_extract(u.data,'$.image') image, json_extract(e.event,'$.evidence') ev
      FROM events e JOIN units u ON u.id=e.target WHERE e.kind='review' ORDER BY e.at" \\
      | jq -c '.[0].results[]' > events.jsonl
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
            latest[row["id"]] = (row, answer)
    for identity, (row, answer) in latest.items():
        named = answer.get("character") or answer.get("correction")
        if answer.get("verdict") == "match":
            truth = row["c"]
        elif answer.get("verdict") == "wrong" and answer.get("issue") in ("reading", "character") and named:
            truth = named
        else:
            continue
        if truth and len(truth) == 1:
            yield {"id": identity, "image": row["image"], "truth": truth, "label": row["c"],
                   "script": str(refs.script_of(truth)), "corrected": truth != row["c"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("events", type=Path)
    args = parser.parse_args()
    rows = list(truths(args.events.read_text().splitlines()))
    (Path(__file__).parent / "reviewed.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(json.dumps({"crops": len(rows), "corrected": sum(r["corrected"] for r in rows)}))


if __name__ == "__main__":
    main()
