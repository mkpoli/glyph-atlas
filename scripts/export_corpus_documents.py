"""Write the D1 SQL that fills `corpus_units.document` (migration 0053) for the glyphs published before it.

    uv run scripts/export_corpus_documents.py OUTPUT
    OUTPUT/apply.sh

The glyphs of every unit corpus the site publishes (`export_cloudflare_corpus.unit_corpora`) are sorted
by id, and each run of ids from one document becomes one statement over that id range, at most `SLICE`
ids long, so no statement touches more rows than D1 updates in one go. A range is bounded by ids the
corpus holds, so a glyph of another document is never inside it unless the site holds a glyph no corpus
here has; `apply.sh` counts what is left unfilled. Every statement repeats safely.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pyarrow.dataset as ds
from export_cloudflare_corpus import unit_corpora

from glyph_atlas import withdrawn
from glyph_atlas.review.ledger import sql_literal as q

#: Ids one statement covers at most; one UPDATE over 200k rows failed on the live database (0024).
SLICE = 20_000
#: Statements one part holds: each part is one D1 transaction, here of at most 200k rows.
PER_PART = 10


def pairs(corpora) -> list[tuple[str, str]]:
    """Every (glyph id, document id) of `corpora`, by id."""
    found: dict[str, str] = {}
    gone = withdrawn.documents()
    for corpus in corpora:
        path = corpus.table("units")
        table = ds.dataset(path, format="parquet").to_table(columns=["id", "document_id"])
        for identity, document in zip(table.column("id").to_pylist(), table.column("document_id").to_pylist(), strict=True):
            if document and document not in gone:
                found[identity] = document
    return sorted(found.items())


def ranges(found: list[tuple[str, str]], size: int = SLICE) -> list[tuple[str, str, str]]:
    """Runs of one document, each at most `size` ids: (first id, last id, document)."""
    out: list[tuple[str, str, str]] = []
    start = 0
    for i in range(1, len(found) + 1):
        if i == len(found) or found[i][1] != found[start][1] or i - start == size:
            out.append((found[start][0], found[i - 1][0], found[start][1]))
            start = i
    return out


def statements(runs: list[tuple[str, str, str]]) -> list[str]:
    return [f"UPDATE corpus_units SET document={q(document)} WHERE id>={q(first)} AND id<={q(last)} "
            f"AND document IS NOT {q(document)};\n" for first, last, document in runs]


def counts(found: list[tuple[str, str]]) -> list[str]:
    """The statements that write each document's glyph count, replacing the earlier ones."""
    per = Counter(document for _, document in found)
    rows = [f"({q(document)},{n})" for document, n in sorted(per.items())]
    return ["DELETE FROM corpus_document_counts;\n"] + [
        f"INSERT INTO corpus_document_counts(document,n) VALUES{','.join(rows[i:i + 200])};\n" for i in range(0, len(rows), 200)]


APPLY = """#!/usr/bin/env bash
# Fill corpus_units.document for {glyphs} glyphs of {documents} documents ({runs} ranges). Each part repeats safely.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd "$(git -C "$here" rev-parse --show-toplevel)/apps/cloudflare"
q() {{ bunx wrangler d1 execute glyph-atlas --remote --json --command "$1" 2>/dev/null | jq -c '.[0].results[0]'; }}
[ "$(q "SELECT count(*) AS n FROM pragma_table_info('corpus_units') WHERE name='document'")" = '{{"n":1}}' ] \\
  || {{ echo "apply migration 0053 first" >&2; exit 1; }}
for part in "$here"/sql/part-*.sql; do
  done=0
  for try in 1 2 3 4; do
    if bunx wrangler d1 execute glyph-atlas --remote --yes --file "$part" 2>&1 | tee /dev/stderr | grep -q "Executed"; then done=1; break; fi
    sleep 30
  done
  [ "$done" -eq 1 ] || {{ echo "$(basename "$part") failed four times; rerun the apply." >&2; exit 1; }}
done
echo "glyphs with no document: $(q "SELECT count(*) AS n FROM corpus_units WHERE document IS NULL")"
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    found = pairs(unit_corpora())
    runs = ranges(found)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "sql").mkdir(exist_ok=True)
    for old in (args.output / "sql").glob("*.sql"):
        old.unlink()
    # The last part writes how many glyphs each document has (0053) and stamps `corpus_documents_at`,
    # which the Worker keeps its date counts by.
    found_statements = statements(runs) + counts(found) + [
        "INSERT OR REPLACE INTO metadata(key,value) VALUES('corpus_documents_at',json_quote(strftime('%Y-%m-%dT%H:%M:%fZ','now')));\n"]
    parts = []
    for i in range(0, len(found_statements), PER_PART):
        name = f"sql/part-{len(parts) + 1:03d}.sql"
        (args.output / name).write_text("".join(found_statements[i:i + PER_PART]), encoding="utf-8")
        parts.append(name)
    documents = len({document for _, document in found})
    (args.output / "apply.sh").write_text(APPLY.format(glyphs=len(found), documents=documents, runs=len(runs)), encoding="utf-8")
    (args.output / "apply.sh").chmod(0o755)
    print(json.dumps({"glyphs": len(found), "documents": documents, "ranges": len(runs), "parts": parts}))


if __name__ == "__main__":
    main()
