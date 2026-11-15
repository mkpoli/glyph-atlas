#!/usr/bin/env python3
"""Collect Wikimedia Commons scans that have no Wikisource index, as untranscribed page images.

    uv run scripts/collect_commons_scans.py --out work/shinshaku-dili-beikao --language zh --scripts Hani \
        --production printed/woodblock --origin china --native-width 3013 \
        --native-width-evidence "pdfimages -list on the Commons original" \
        --file "NLC403-312001067176-19028 新釋地理備考全書 卷三.pdf"

    uv run scripts/collect_commons_scans.py --out work/shinshaku-dili-beikao \
        --category 新釋地理備考全書 --match '^File:NLC403-.* 新釋地理備考全書 '

Each file becomes one document of the dataset in `--out`, with its pages and no page texts. Images go
to the image cache (`$GLYPH_ATLAS_CACHE/images`, or `--image-cache`). `--category` takes the files of a
category whose title matches `--match`.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from glyph_atlas import origin, production
from glyph_atlas.importers.commons_scans import category_files, collect
from glyph_atlas.importers.wikisource_scans import PAUSE, Http


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", action="append", default=[], dest="files",
                        help="a Commons file title, with or without File: (repeatable)")
    parser.add_argument("--category", help="a Commons category whose files are collected")
    parser.add_argument("--match", help="regular expression a category file's title must match (required with --category)")
    parser.add_argument("--out", type=Path, required=True, help="dataset directory to write")
    parser.add_argument("--image-cache", type=Path, help="image cache directory (default: the project cache)")
    parser.add_argument("--native-width", type=int,
                        help="the scan's pixel width, when it is wider than the page size Commons states")
    parser.add_argument("--native-width-evidence", help="how --native-width was measured, kept with the dataset")
    parser.add_argument("--scripts", default="", help="ISO 15924 codes, comma separated, e.g. Hani")
    parser.add_argument("--language", help="language code")
    parser.add_argument("--production", choices=list(production.vocabulary()), default="unknown")
    parser.add_argument("--origin", choices=list(origin.TREE.nodes), default="unknown")
    parser.add_argument("--pause", type=float, default=PAUSE, help=f"seconds between requests (default: {PAUSE})")
    args = parser.parse_args()
    if bool(args.category) != bool(args.match):
        parser.error("--category and --match go together")
    if not args.files and not args.category:
        parser.error("give --file or --category with --match")
    http = Http(pause=args.pause)
    titles = list(args.files)
    if args.category:
        titles += category_files(http, args.category, args.match)
    if not titles:
        parser.error("no file to collect")
    result = collect(
        args.out, dict.fromkeys(titles), http=http, image_root=args.image_cache,
        command="scripts/collect_commons_scans.py",
        native_width=args.native_width, native_width_evidence=args.native_width_evidence,
        scripts=[s for s in args.scripts.split(",") if s], language=args.language,
        production=args.production, origin=args.origin,
    )
    print(json.dumps(result, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
