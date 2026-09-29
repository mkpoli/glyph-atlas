"""Write D1 SQL that records the adjacent crop pairs of datasets whose crops the site already holds.

A publication records its own pairs (`export_cloudflare.py`, `seal_cloudflare.py`); this covers crops
published before migration 0028. A dataset is read, never written: its `review.sqlite` where it has one,
else its `units.parquet`. Each dataset's pairs are found within it, as its publication finds them; where
two datasets pair the same crop, the later one wins. A pair is recorded only when both of its crops are on the site, and each unit loses the pair recorded for it
before, so the parts may be applied to D1 any number of times, in order:

    uv run scripts/export_unit_pairs.py DATASET [DATASET ...] OUTPUT
    for part in OUTPUT/sql/part-*.sql; do bunx wrangler d1 execute glyph-atlas --remote --file "$part"; done

The parts stay under D1's import size, every statement under its statement limit; the last one stamps
`units_refreshed_at`, which the Worker's cached counts are keyed by.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from prepare_publication import write_parts
from refresh_published_units import VERSION_BUMP

from glyph_atlas import tables
from glyph_atlas.schema import Unit
from glyph_atlas.unit_pairs import adjacent_pairs, pair_statements


def dataset_units(dataset: Path) -> list[Unit]:
    if (dataset / "review.sqlite").exists():
        with sqlite3.connect(f"file:{dataset / 'review.sqlite'}?mode=ro", uri=True) as db:
            return [Unit.model_validate_json(data) for (data,) in db.execute("SELECT data FROM units")]
    return tables.read(dataset / "units.parquet", Unit)


def statements(datasets: list[Path]) -> tuple[list[str], int]:
    """Every statement that records the datasets' pairs, and how many pairs they name."""
    # Pooled, a line two datasets cut differently would hold two crops at a position, and such a
    # position pairs with nothing.
    pairs, placed = {}, set()
    for dataset in datasets:
        units = dataset_units(dataset)
        pairs |= dict(adjacent_pairs(units))
        placed |= {unit.id for unit in units if unit.active and unit.line_id}
    return [s + "\n" for s in pair_statements(sorted(placed), sorted(pairs.items()))], len(pairs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("datasets", type=Path, nargs="+")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    found, pairs = statements(args.datasets)
    args.output.mkdir(parents=True, exist_ok=True)
    parts = write_parts(args.output, [found, [VERSION_BUMP]])
    print(json.dumps({"pairs": pairs, "statements": len(found) + 1, "parts": parts}))


if __name__ == "__main__":
    main()
