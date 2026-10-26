"""Collect Kyoto's kanbun text corpora and scan references from pinned snapshots.

    uv run python scripts/collect_kyodokenkyu.py

Page images are referenced remotely. UD text segments have no inferred scan
alignment. CoNLL-U annotations retain unknown rights pending upstream evidence.
The output symlink selects a complete snapshot; earlier snapshots remain in the
sibling .<output-name>-versions directory.
"""
import argparse
import json
from pathlib import Path

from glyph_atlas.importers.kyodokenkyu import collect


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=Path("data/sources/kyodokenkyu.yaml"))
    parser.add_argument("--out", type=Path, default=Path("work/kyodokenkyu"))
    parser.add_argument("--cache", type=Path, default=Path("cache/kyodokenkyu/pinned"))
    args = parser.parse_args()
    print(json.dumps(collect(args.source, args.out, args.cache), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
