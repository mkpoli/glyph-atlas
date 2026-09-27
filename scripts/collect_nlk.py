"""Collect 국립중앙도서관 old-book records into a dataset directory, from their PDF viewer."""
import argparse
import json
import sys
from pathlib import Path

from glyph_atlas.importers.nlk import collect, content_id

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("ids", nargs="+", help="content ids (CNTS-…)")
parser.add_argument("--out", type=Path, default=Path("work/nlk"))
args = parser.parse_args()
try:
    ids = [content_id(value) for value in args.ids]
except ValueError as error:
    parser.error(str(error))
summary = collect(ids, args.out, command=" ".join(["collect_nlk.py", *ids]))
print(json.dumps(summary, ensure_ascii=False, indent=2))
sys.exit(1 if summary["unavailable"] else 0)
