"""Write the 異体字 graph, its derived tier, the words' spellings and their citations into the reference
input (`glyph_atlas.reference_input`), which `scripts/atlas3/reference.py` loads onto the site.

    python scripts/export_character_variants.py            # every table below
    python scripts/export_character_variants.py --words    # words and word_spellings alone
    python scripts/export_character_variants.py --writers  # grapheme_writers alone

`character_variants` is the 異体字 graph itself. The derived tier is `component_variants` (each
substitution with its count and pairs) and `character_derived`: one row per character, its derived
list as `refs.derived_variants` ranks it, each form with its routes (`refs.derived_row`). Up to two
substitutions per form make that about half an hour of work on one processor, spread over the
processors and computed once per run. `han_ids` holds each character's descriptions, which the form
picker's IDS editor starts from, and its regional shapes. `words` and `word_spellings` are the word
layer of decision 0004 (`refs.words`, `refs.word_spellings`, which joins 振り仮名 counts and observed
crop claims); `--words` writes them alone, so a new word reaches the site without deriving the graph
again. `grapheme_writers` holds the reduced forms that write several characters and the forms with no
character of their own, one row per form and grapheme it writes besides its own (`refs.also_writes`).
`sources` holds the citations a row may name: `variant_sources` for the graph and the derived tier,
`word_sources` for the words.

Each run replaces the tables it writes in one transaction. The full export (`export_cloudflare.py`)
fills the same tables in its catalogue through `fill`.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sqlite3
from functools import cache
from pathlib import Path

from glyph_atlas import han_components, reference_input, refs

COLUMNS = ("a", "b", "relation", "source", "detail", "written", "widens")
DERIVED_COLUMNS = ("a", "forms")
SUBSTITUTION_COLUMNS = ("a", "b", "count", "pairs")
WORD_COLUMNS = ("id", "language", "reading", "class")
WRITER_COLUMNS = ("writer", "grapheme", "own")
SPELLING_COLUMNS = ("word", "spelling", "source", "locator", "tier", "word_by", "basis", "related", "statement",
                    "documents", "occurrences")


def _derived_part(chars: list[str]) -> list[tuple[str, str]]:
    return [row for char in chars if (row := refs.derived_row(char))]


@cache
def derived_rows() -> tuple[tuple[str, str], ...]:
    """Every row of the derived tier (refs.derived_rows), computed once per run on forked processes
    that share the descriptions read before the pool starts. Each chunk runs in a fresh process, so
    what a derivation keeps of each part never grows past one chunk's characters."""
    chars = sorted(refs._descriptions().trees)
    refs._maker()
    refs._stated_pairs()
    chunks = [chars[i::256] for i in range(256)]
    with multiprocessing.get_context("fork").Pool(max(1, (os.cpu_count() or 2) - 2), maxtasksperchild=1) as pool:
        found = [row for part in pool.imap_unordered(_derived_part, chunks) for row in part]
    return tuple(sorted(found))


def edge_rows() -> list[tuple]:
    """`character_variants`: each edge of the graph, its flags as integers."""
    return [tuple(int(edge[c]) if c in ("written", "widens") else edge[c] for c in COLUMNS) for edge in refs.variant_edges()]


def substitution_rows() -> list[tuple[str, str, int, str]]:
    """`component_variants`: each substitution with its count and the pairs behind it."""
    return [(left, right, item["count"], json.dumps(item["pairs"], ensure_ascii=False, separators=(",", ":")))
            for (left, right), item in sorted(refs.component_variants().items())]


def citations() -> str:
    """Every source a row of any table may cite, the derived tier's own `derived-ids` included."""
    return json.dumps({**refs.variant_sources(), **refs.component_variant_sources()},
                      ensure_ascii=False, separators=(",", ":"))


def word_citations() -> str:
    """Every source a word or a spelling cites."""
    return json.dumps({**refs.word_sources(), **refs.word_spelling_sources()}, ensure_ascii=False, separators=(",", ":"))


@cache
def ids_rows() -> tuple[tuple[str, str, str | None], ...]:
    """`han_ids`: each character's descriptions the IDS editor starts from (`refs.ids_sequences`), and
    its regional shapes when it has several (`refs.regional_shapes`, else NULL)."""
    dump = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return tuple((char, dump(found), dump(shapes) if (shapes := refs.regional_shapes(char)) else None)
                 for char in sorted(han_components._sequences()) if (found := refs.ids_sequences(char)))


def word_rows() -> list[tuple]:
    return [tuple(row[column] for column in WORD_COLUMNS) for row in refs.words().values()]


def spelling_rows() -> list[tuple]:
    return [tuple(row.get(column) for column in SPELLING_COLUMNS) for row in refs.word_spellings()]


def writer_rows() -> list[tuple[str, str, int]]:
    """Each form and a grapheme it writes besides its own, as text, by form then grapheme, with whether
    the form is a character of its own (`refs.own_grapheme`)."""
    return sorted((refs.to_char(point), refs.to_char(head), int(refs.own_grapheme(point)))
                  for head, family in refs._grapheme_families().items() for point in family["writers"])


def write(db: sqlite3.Connection, *, words: bool = True, writers: bool = True, graph: bool = True) -> dict[str, int]:
    """The chosen tables of the reference input replaced, in the caller's transaction; the rows each holds."""
    counts = {}
    if graph:
        counts["character_variants"] = reference_input.replace(db, "character_variants", edge_rows())
        counts["component_variants"] = reference_input.replace(db, "component_variants", substitution_rows())
        counts["character_derived"] = reference_input.replace(db, "character_derived", derived_rows())
        counts["han_ids"] = reference_input.replace(db, "han_ids", ids_rows())
        reference_input.put_source(db, "variant_sources", citations())
    if writers:
        counts["grapheme_writers"] = reference_input.replace(db, "grapheme_writers", writer_rows())
    if words:
        counts["words"] = reference_input.replace(db, "words", word_rows())
        counts["word_spellings"] = reference_input.replace(db, "word_spellings", spelling_rows())
        reference_input.put_source(db, "word_sources", word_citations())
    return counts


def fill(db: sqlite3.Connection, *, derived: bool = True) -> None:
    """The same rows in a local catalogue built from the migrations.

    `derived=False` leaves out the derived forms and the descriptions: deriving them runs over every
    character and takes more memory than a collection export has, and a collection publication never
    reads them, since this script writes them for the site itself."""
    db.execute("DELETE FROM character_variants")
    db.executemany(f"INSERT OR REPLACE INTO character_variants({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})",
                   edge_rows())
    db.execute("DELETE FROM component_variants")
    db.executemany(f"INSERT OR REPLACE INTO component_variants({','.join(SUBSTITUTION_COLUMNS)}) VALUES (?,?,?,?)",
                   substitution_rows())
    if derived:
        db.execute("DELETE FROM character_derived")
        db.executemany(f"INSERT OR REPLACE INTO character_derived({','.join(DERIVED_COLUMNS)}) VALUES (?,?)",
                       derived_rows())
        db.execute("DELETE FROM han_ids")
        db.executemany("INSERT INTO han_ids(char,sequences,shapes) VALUES (?,?,?)", ids_rows())
    db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('variant_sources',?)", (citations(),))
    db.execute("DELETE FROM grapheme_writers")
    db.executemany("INSERT INTO grapheme_writers(writer,grapheme,own) VALUES (?,?,?)", writer_rows())
    db.execute("DELETE FROM words")
    db.executemany(f"INSERT INTO words({','.join(WORD_COLUMNS)}) VALUES ({','.join('?' * len(WORD_COLUMNS))})", word_rows())
    db.execute("DELETE FROM word_spellings")
    db.executemany(f"INSERT INTO word_spellings({','.join(SPELLING_COLUMNS)}) VALUES ({','.join('?' * len(SPELLING_COLUMNS))})",
                   spelling_rows())
    db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('word_sources',?)", (word_citations(),))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, help="the reference input to write (default: reference-input.sqlite in ATLAS3_DIR)")
    alone = parser.add_mutually_exclusive_group()
    alone.add_argument("--words", action="store_true", help="write words and word_spellings alone")
    alone.add_argument("--writers", action="store_true", help="write grapheme_writers alone")
    arguments = parser.parse_args()
    every = not (arguments.words or arguments.writers)
    with reference_input.writing(arguments.input) as db:
        counts = write(db, words=every or arguments.words, writers=every or arguments.writers, graph=every)
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
