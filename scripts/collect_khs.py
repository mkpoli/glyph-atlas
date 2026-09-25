"""Collect 국가유산청 heritage items with their 공공누리 제1유형 photographs."""
import argparse
import json
import sys
from pathlib import Path

from glyph_atlas.importers.khs import collect, item_id

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("ids", nargs="+", help="item ids, ccbaKdcd-ccbaAsno-ccbaCtcd (e.g. 12-0008750000400-24)")
parser.add_argument("--out", type=Path, default=Path("work/khs"))
args = parser.parse_args()
try:
    ids = [item_id(value) for value in args.ids]
except ValueError as error:
    parser.error(str(error))
summary = collect(ids, args.out, command=" ".join(["collect_khs.py", *ids]))
print(json.dumps(summary, ensure_ascii=False, indent=2))
sys.exit(1 if summary["unavailable"] else 0)
