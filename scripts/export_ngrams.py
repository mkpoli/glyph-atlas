"""Write D1 SQL that records the pairs and trigrams of crops of datasets whose crops the site already holds.

A publication records its own runs (`export_cloudflare.py`, `seal_cloudflare.py`); this covers crops
published before their runs were counted. A dataset is read, never written: its `review.sqlite` where it
has one, else its `units.parquet` (and `lines.parquet`, for which lines are written across the page).
Each dataset's runs are found within it, as its publication finds them; where two datasets hold the same
crop, the later one's runs from it replace the earlier's. A run is recorded only when all of its crops
are on the site, and each unit loses the runs recorded for it before, so the parts may be applied to D1
any number of times, in order:

    uv run scripts/export_ngrams.py DATASET [DATASET ...] OUTPUT
    for part in OUTPUT/sql/part-*.sql; do bunx wrangler d1 execute glyph-atlas --remote --file "$part"; done

The parts stay under D1's import size, every statement under its statement limit; the last one stamps
`units_refreshed_at`, which the Worker's cached counts are keyed by.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from prepare_publication import write_parts
from refresh_published_units import VERSION_BUMP

from glyph_atlas import tables
from glyph_atlas.ngrams import adjacent_ngrams, ngram_statements
from glyph_atlas.schema import Line, Unit


def dataset_units(dataset: Path) -> tuple[list[Unit], set[str]]:
    """A dataset's units, and the ids of its lines written across the page."""
    if (dataset / "review.sqlite").exists():
        with sqlite3.connect(f"file:{dataset / 'review.sqlite'}?mode=ro", uri=True) as db:
            units = [Unit.model_validate_json(data) for (data,) in db.execute("SELECT data FROM units")]
            lines = db.execute("SELECT 1 FROM sqlite_master WHERE name='lines'").fetchone()
            return units, {i for (i,) in db.execute("SELECT id FROM lines WHERE json_extract(data, '$.vertical') = 0")} if lines else set()
    lines = dataset / "lines.parquet"
    return tables.read(dataset / "units.parquet", Unit), \
        {line.id for line in tables.read(lines, Line) if not line.vertical} if lines.exists() else set()


def statements(datasets: list[Path]) -> tuple[list[str], Counter]:
    """Every statement that records the datasets' runs, and how many they name by length and direction."""
    # Pooled, a line two datasets cut differently would hold two crops at a position, and such a
    # position starts and ends no run.
    runs, placed = {}, set()
    for dataset in datasets:
        units, horizontal = dataset_units(dataset)
        held = {unit.id for unit in units}
        runs = {key: run for key, run in runs.items() if key[0] not in held}
        runs |= {(run.units[0], len(run.units)): run for run in adjacent_ngrams(units, horizontal)}
        placed |= {unit.id for unit in units if unit.active and unit.line_id}
    found = [s + "\n" for s in ngram_statements(sorted(placed), [runs[key] for key in sorted(runs)])]
    return found, Counter((len(run.units), run.vertical) for run in runs.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("datasets", type=Path, nargs="+")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    found, sizes = statements(args.datasets)
    args.output.mkdir(parents=True, exist_ok=True)
    parts = write_parts(args.output, [found, [VERSION_BUMP]])
    print(json.dumps({"pairs": sizes[2, True] + sizes[2, False], "trigrams": sizes[3, True] + sizes[3, False],
                      "horizontal": {"pairs": sizes[2, False], "trigrams": sizes[3, False]},
                      "statements": len(found) + 1, "parts": parts}))


if __name__ == "__main__":
    main()
