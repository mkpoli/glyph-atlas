"""Write D1 SQL that records the adjacent crop pairs of review datasets whose crops the site already holds.

A publication records its own pairs (`export_cloudflare.py`, `seal_cloudflare.py`); this covers crops
published before migration 0028. Each dataset's `review.sqlite` is read, never written. A pair is
recorded only when both of its crops are on the site, and a pair already recorded is left as it is, so
the output may be applied to D1 any number of times:

    uv run scripts/export_unit_pairs.py DATASET [DATASET ...] OUTPUT.sql
    bunx wrangler d1 execute glyph-atlas --remote --file OUTPUT.sql
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from glyph_atlas.schema import Unit
from glyph_atlas.unit_pairs import adjacent_pairs, pair_inserts


def dataset_units(dataset: Path) -> list[Unit]:
    with sqlite3.connect(f"file:{dataset / 'review.sqlite'}?mode=ro", uri=True) as db:
        return [Unit.model_validate_json(data) for (data,) in db.execute("SELECT data FROM units")]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("datasets", type=Path, nargs="+")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    pairs = sorted({pair for dataset in args.datasets for pair in adjacent_pairs(dataset_units(dataset))})
    args.output.write_text("".join(statement + "\n" for statement in pair_inserts(pairs)))
    print({"pairs": len(pairs), "statements": -(-len(pairs) // 200)})


if __name__ == "__main__":
    main()
