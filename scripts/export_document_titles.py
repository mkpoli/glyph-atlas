"""Write the D1 SQL that names the corpus documents the site already holds (migration 0075).

    uv run scripts/export_document_titles.py > titles.sql

A corpus publication writes each document's title with its count from then on; the documents published
before 0075 have a row and no title. Each statement names up to `CHUNK` documents of every unit corpus
the site publishes (`export_cloudflare_corpus.unit_corpora`), and writes only a row the site holds whose
title differs, so a document the site holds no glyph of writes nothing and every statement repeats safely.
"""
from __future__ import annotations

from export_cloudflare_corpus import corpus_titles, unit_corpora

from glyph_atlas.review.ledger import sql_literal as q

#: Documents one statement names.
CHUNK = 200


def statements(titles: dict[str, str]) -> list[str]:
    rows = [f"({q(document)},{q(title)})" for document, title in sorted(titles.items())]
    return [f"WITH t(document,title) AS (VALUES{','.join(rows[i:i + CHUNK])}) UPDATE corpus_documents SET title=t.title FROM t "
            "WHERE corpus_documents.document=t.document AND corpus_documents.title IS NOT t.title;"
            for i in range(0, len(rows), CHUNK)]


def main() -> None:
    print("\n".join(statements(corpus_titles(unit_corpora()))))


if __name__ == "__main__":
    main()
