#!/usr/bin/env python3
"""Collect proofread Wikisource indexes as page images from Commons with their page texts.

    uv run scripts/collect_wikisource_scans.py --wiki ko --out work/hunminjeongeum \
        --scripts Hani,Hang "조선어학회 훈민정음.pdf" "훈민정음 석보상절 월인천강지곡.pdf"

Each index becomes one document of the dataset in `--out`. Images go to the image cache
(`$GLYPH_ATLAS_CACHE/images`, or `--image-cache`).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from glyph_atlas.importers.wikisource_scans import PAUSE, Http, collect
from glyph_atlas.schema import Production


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("indexes", nargs="+", help="index names, with or without the Index: prefix")
    parser.add_argument("--wiki", default="ko", help="Wikisource language code (default: ko)")
    parser.add_argument("--out", type=Path, required=True, help="dataset directory to write")
    parser.add_argument("--image-cache", type=Path, help="image cache directory (default: the project cache)")
    parser.add_argument("--native-width", type=int,
                        help="the scan's pixel width, when it is wider than the page size Commons states")
    parser.add_argument("--native-width-evidence", help="how --native-width was measured, kept with the dataset")
    parser.add_argument("--scripts", default="", help="ISO 15924 codes, comma separated, e.g. Hani,Hang")
    parser.add_argument("--language", help="language code (default: the index's language field)")
    parser.add_argument("--contributors", help="text attribution (default: '<Language> Wikisource contributors')")
    parser.add_argument("--production", choices=[p.value for p in Production], default=Production.UNKNOWN.value)
    parser.add_argument("--pause", type=float, default=PAUSE, help=f"seconds between requests (default: {PAUSE})")
    args = parser.parse_args()
    result = collect(
        args.out, args.wiki, args.indexes, http=Http(pause=args.pause), image_root=args.image_cache,
        command="scripts/collect_wikisource_scans.py --wiki " + args.wiki,
        native_width=args.native_width, native_width_evidence=args.native_width_evidence, scripts=[s for s in args.scripts.split(",") if s],
        language=args.language, contributors=args.contributors, production=Production(args.production),
    )
    print(json.dumps(result, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
