"""Prepare bounded, provenance-preserving samples for visual-family analysis."""
import argparse
import json
from pathlib import Path

from glyph_atlas.visual_samples import PRIORITY, prepare

parser=argparse.ArgumentParser()
parser.add_argument("--root",type=Path,default=Path("work/visual-families"))
parser.add_argument("--families",default=PRIORITY,help="canonical characters, or all")
parser.add_argument("--per-source",type=int,default=128)
parser.add_argument("--maximum",type=int,default=3000)
parser.add_argument("--network-mib",type=int,default=250)
args=parser.parse_args()
if not 1<=args.per_source<=128 or not 1<=args.maximum<=3000 or not 0<=args.network_mib<=250:
    parser.error("bounded run requires per-source1..128, maximum1..3000, network0..250MiB")
print(json.dumps(prepare(args.root,request=args.families,per_source=args.per_source,
                         maximum=args.maximum,network_mib=args.network_mib),ensure_ascii=False,indent=2))
