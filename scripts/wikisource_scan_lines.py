#!/usr/bin/env python3
"""Write the lines of a collected Wikisource scan dataset from its page texts.

    uv run scripts/wikisource_scan_lines.py work/wikisource-zh-scans

Only page texts of a wiki `glyph_atlas.wikitext` reads (zh.wikisource) become lines.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from glyph_atlas.importers.wikisource_scans import write_lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", type=Path, help="a dataset written by collect_wikisource_scans.py")
    args = parser.parse_args()
    print(f"{write_lines(args.directory)} lines -> {args.directory / 'lines.parquet'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
