"""Write D1 SQL that records the pairs of corpus glyphs that follow each other on a line.

The corpus index holds no runs of its own: a glyph's line and position come from its corpus, read here,
and the pair is recorded only where both glyphs are published (`corpus_units`). A corpus whose units name
their line (`line_id`, `seq`) is read as it is. CODH names none, but its ids carry the transcribers'
reading order, `…:B0001:C0042`: the block is the line and the character number the position, so a
column ending inside a block breaks the pair there, as the next glyph is not below it (`glyph_pairs`).
A pair's text is what its corpus transcribes (`text_source`), composed as the Worker composes a query, so
a CODH kana the site labels with its hentaigana form is still found by the kana typed.

    uv run scripts/export_corpus_ngrams.py CORPUS_ROOT OUTPUT [--corpora codh-full ...]
    cd apps/cloudflare && for part in ../../OUTPUT/sql/part-*.sql; do ../../scripts/d1_import.sh "$part" || { echo "stopped at $part" >&2; break; }; done

Every glyph of the corpora read loses the pair recorded for it before, so the parts may be applied any
number of times, in order; the last one stamps `units_refreshed_at`, which
the Worker's cached answers are keyed by.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

import pyarrow.dataset as ds
from prepare_publication import write_parts
from refresh_published_units import VERSION_BUMP

from glyph_atlas.corpus import sources
from glyph_atlas.ngrams import Glyph, corpus_pair_statements, glyph_pairs

#: The corpora whose glyphs stand on lines of a page. `hilab` and `hng` are single characters cut apart
#: and have no line to read. A corpus is read from its tables (`units.parquet`), as its glyphs' published
#: records are built, though `honkoku-lines` keeps a review store beside them.
CORPORA = ("codh-full", "kokatsuji", "honkoku-lines", "hng-kiridashi", "hdic-krm", "hdic-ktb", "hdic-tsj",
           "ainu-records", "glossary-headwords")
CODH_ORDER = re.compile(r":(B\d+):C(\d+)$")
COMPATIBILITY = re.compile(r"[豈-﫿\U0002F800-\U0002FA1F]")
COLUMNS = ["id", "page_id", "line_id", "seq", "box", "kind", "granularity", "text_source", "active"]


def compose(value: str) -> str:
    """NFC, leaving CJK compatibility ideographs as they are: the Worker's `compose`."""
    out, run = [], ""
    for c in value:
        if COMPATIBILITY.match(c):
            out += [unicodedata.normalize("NFC", run), c]
            run = ""
        else:
            run += c
    return "".join(out) + unicodedata.normalize("NFC", run)


def position(row: dict) -> tuple[str, int, bool] | None:
    """A glyph's line, its place on it, and whether that order wraps from column to column: as its
    corpus records them, or from a CODH id, whose block holds several columns."""
    if row["line_id"] and row["seq"] is not None:
        return row["line_id"], row["seq"], False
    order = CODH_ORDER.search(row["id"])
    return (f"{row['page_id']}:{order.group(1)}", int(order.group(2)), True) if order and row["page_id"] else None


def corpus_glyphs(corpus: sources.Corpus) -> tuple[list[Glyph], list[str]]:
    """The glyphs of a corpus that can be part of a run (active character glyphs with a box, a line and
    a transcription), and the ids of all its glyphs, whose earlier pairs a refresh replaces."""
    table = ds.dataset(str(corpus.table("units")), format="parquet").to_table(columns=COLUMNS)
    glyphs, every = [], []
    for row in table.to_pylist():
        every.append(row["id"])
        place = position(row)
        if (row["active"] is False or row["kind"] != "char" or row["granularity"] != "char" or not row["box"]
                or not row["text_source"] or place is None):
            continue
        box = row["box"]
        glyphs.append(Glyph(row["id"], place[0], place[1], (box["x"], box["y"], box["w"], box["h"]), compose(row["text_source"]), place[2]))
    return glyphs, every


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--corpora", nargs="+", default=list(CORPORA))
    args = parser.parse_args()
    found = {corpus.name: corpus for corpus in sources.discover(args.root)}
    ids, pairs, report = [], [], {}
    unknown = [name for name in args.corpora if name not in found]
    if unknown:
        raise SystemExit(f"no such corpus under {args.root}: {', '.join(unknown)}")
    for name in args.corpora:
        corpus = found[name]
        if corpus.table("units") is None:
            report[name] = "no units"
            continue
        glyphs, every = corpus_glyphs(corpus)
        # A glyph twice over would hold its position twice and break the line there unnoticed.
        repeated = len(every) - len(set(every))
        if repeated:
            raise SystemExit(f"{name}: {repeated} glyph ids appear more than once")
        found_pairs = glyph_pairs(glyphs)
        ids += every
        pairs += found_pairs
        report[name] = {"glyphs": len(every), "placed": len(glyphs), "pairs": len(found_pairs),
                        "across": sum(not p.vertical for p in found_pairs)}
    statements = [s + "\n" for s in corpus_pair_statements(ids, sorted(pairs))]
    args.output.mkdir(parents=True, exist_ok=True)
    parts = write_parts(args.output, [statements, [VERSION_BUMP]])
    texts = Counter(p.text for p in pairs)
    print(json.dumps({"corpora": report, "pairs": len(pairs), "texts": len(texts), "statements": len(statements) + 1,
                      "parts": parts, "most": texts.most_common(5)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
