"""Collect the selected Kyujanggak land-register scans.

    uv run python scripts/collect_kyujanggak.py

The image list is pinned by SHA-256 in the source record, and each scan is
cached by content hash. Inserted slips stay separate images in the viewer's
order. One volume per output directory keeps a review with its volume; pass
--book to collect a subset. The output symlink selects a complete snapshot;
earlier snapshots remain in the sibling .<output-name>-versions directory.
"""
import argparse
import json
from pathlib import Path

from glyph_atlas.importers.kyujanggak import collect


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=Path("data/sources/kyujanggak.yaml"))
    parser.add_argument("--out", type=Path, default=Path("work/kyujanggak"))
    parser.add_argument("--cache", type=Path, default=Path("cache/kyujanggak/pinned"))
    parser.add_argument("--book", action="append", metavar="BOOK_CD:VOL_NO",
                        help="volume to collect, e.g. GK18324_00:0001; repeat for several, default all")
    args = parser.parse_args()
    print(json.dumps(collect(args.source, args.out, args.cache, books=args.book),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
