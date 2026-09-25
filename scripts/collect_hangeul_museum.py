"""Collect 국립한글박물관 아카이브 records under 공공누리 제1유형, with their images and 판독."""
import argparse
import json
import sys
from pathlib import Path

from glyph_atlas.importers.hangeul_museum import collect, record_id

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("ids", nargs="+", help="record ids (rcrdId)")
parser.add_argument("--out", type=Path, default=Path("work/hangeul-museum"))
args = parser.parse_args()
try:
    ids = [record_id(value) for value in args.ids]
except ValueError as error:
    parser.error(str(error))
summary = collect(ids, args.out, command=" ".join(["collect_hangeul_museum.py", *ids]))
print(json.dumps(summary, ensure_ascii=False, indent=2))
sys.exit(1 if summary["unavailable"] else 0)
