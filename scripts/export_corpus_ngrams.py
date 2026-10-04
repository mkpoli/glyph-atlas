"""Write D1 SQL that records the pairs and trigrams of the corpus glyphs the site publishes.

A corpus glyph's runs are found the way a crop's are (`glyph_atlas.ngrams.adjacent_ngrams`): by its
line and position on that line, in the unit corpora the corpus publication exports
(`export_cloudflare_corpus.unit_corpora`). A corpus whose units carry no line, such as a set of
pre-cut crops, has none; a CODH unit's line and place are read from its id (`codh_all.reading_place`).
An aligned unit's place on its line is read from its box (`in_reading_order`).
The glyphs of withdrawn documents are left out, and a run is recorded only while all of its glyphs
are on the site, so a glyph the publication left out breaks the runs it would be part of. Each corpus's runs replace those recorded for its glyphs before, so the parts
may be applied any number of times, in order, once the corpus glyphs are published:

    uv run scripts/export_corpus_ngrams.py OUTPUT [--corpus NAME ...]
    cd apps/cloudflare && for part in ../../OUTPUT/sql/part-*.sql; do ../../scripts/d1_import.sh "$part" || { echo "stopped at $part" >&2; break; }; done

The corpora are read from `work/` under the working directory. Nothing is sent to D1 here. The last
part stamps `units_refreshed_at`, which the Worker's cached counts are keyed by.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pyarrow.dataset as ds
from export_cloudflare_corpus import line_orientation, unit_corpora
from prepare_publication import write_parts
from refresh_published_units import VERSION_BUMP

from glyph_atlas import withdrawn
from glyph_atlas.importers.codh_all import reading_place
from glyph_atlas.ngrams import (
    Glyph,
    Run,
    adjacent_ngrams,
    corpus_ngram_statements,
    id_ranges,
    in_reading_order,
)

COLUMNS = ("id", "document_id", "page_id", "line_id", "seq", "box", "kind", "granularity", "active", "method")
#: Units whose line numbers come from the aligner, which numbered a line's boxes in a scrambled order
#: until 2026-09-24 (`glyph_atlas.box_relabel`): their places are read from their boxes instead.
ALIGNED = "detect-align"
#: Ids whose runs one statement removes: two runs at most start at each, so a statement deletes at
#: most twice this many rows, well inside what D1 changes in one go.
SLICE = 5000


def placed(row: dict, columns: set[str]) -> dict:
    """A unit row with its line and position: its own, or those its dataset's ids carry
    (`codh_all.reading_place`). A line read from the ids is a block of several columns, and is added
    to `columns`."""
    if row.get("line_id") is None and (place := reading_place(row["id"], row.get("page_id"))):
        row["line_id"], row["seq"] = place
        columns.add(place[0])
    return row


def corpus_runs(corpus) -> tuple[list[Run], list[str]]:
    """A corpus's runs, and the ids of every glyph on a line, whose earlier runs the new ones replace."""
    paths = corpus.parquet_files("units")
    if not paths:
        return [], []
    dataset = ds.dataset([str(p) for p in paths], format="parquet")
    if not {"line_id", "seq"} <= set(dataset.schema.names):
        return [], []
    vertical = line_orientation(corpus)
    gone = withdrawn.documents()
    columns: set[str] = set()
    rows = [placed(row, columns) for row in dataset.to_table(columns=[c for c in COLUMNS if c in dataset.schema.names]).to_pylist()
            if row.get("document_id") not in gone]
    rows = [row for row in rows if row.get("line_id")]
    horizontal = {line for line, down in vertical.items() if down is False}
    glyphs = [Glyph.of(row) for row in rows if row.get("method") != ALIGNED]
    glyphs += in_reading_order((Glyph.of(row) for row in rows if row.get("method") == ALIGNED), horizontal)
    return adjacent_ngrams(glyphs, horizontal, columns), [row["id"] for row in rows]


def statements(corpora) -> tuple[list[str], dict[str, Counter]]:
    """Every statement that records the corpora's runs, and how many each corpus has by length and direction."""
    found, sizes = [], {}
    for corpus in corpora:
        runs, placed = corpus_runs(corpus)
        sizes[corpus.name] = Counter((len(run.units), run.vertical) for run in runs)
        found += [s + "\n" for s in corpus_ngram_statements(id_ranges(placed, SLICE), sorted(runs))]
    return found, sizes


def summary(sizes: Counter) -> dict[str, int]:
    return {"pairs": sizes[2, True] + sizes[2, False], "trigrams": sizes[3, True] + sizes[3, False],
            "horizontal_pairs": sizes[2, False], "horizontal_trigrams": sizes[3, False]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", type=Path)
    parser.add_argument("--corpus", action="append", dest="corpora", metavar="NAME",
                        help="only this unit corpus; repeat for several (default: every one the site publishes)")
    args = parser.parse_args()
    found, sizes = statements(unit_corpora(args.corpora))
    args.output.mkdir(parents=True, exist_ok=True)
    parts = write_parts(args.output, [found, [VERSION_BUMP]])
    print(json.dumps({"corpora": {name: summary(counted) for name, counted in sizes.items()},
                      "statements": len(found) + 1, "parts": parts}))


if __name__ == "__main__":
    main()
