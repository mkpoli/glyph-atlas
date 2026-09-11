# T10 Import the whole CODH くずし字データセット

Goal: all 44 books of 日本古典籍くずし字データセット in the tables, with page images cached.

Read first: `src/kuzushiji_atlas/importers/codh.py` (one-book importer), `data/sources/codh-char-shape.yaml`,
http://codh.rois.ac.jp/char-shape/book/ (book list), the NIJL specification PDF named in the source file.

Inputs: `http://codh.rois.ac.jp/char-shape/dataset/v2/{bid}.zip` per book (8 MB to 434 MB), or the
whole set without crops at `http://codh.rois.ac.jp/char-shape/dataset/v2/all.zip` (5.14 GB).

Outputs
- `data/sources/codh-books.tsv`: bid, title, code points, characters, release date, as listed on the
  book page, plus `holder` where the NIJL 書誌ID resolves on 国書データベース (leave blank otherwise).
- `work/codh/` tables for all books; page images copied into the image cache (T02) with the IIIF
  service URL as their key, so later steps address them like any other page.
- `_report.csv` rows become units with `kind=unreadable`, `review=rejected`, `text_source=None` and
  the report text in `upstream["report"]`.
- CLI: `atlas import codh --all [--from-zip cache/codh/all.zip]`, `atlas import codh <zip>` as now.

Steps: parse the book list once; download with the T02 fetcher; import each book; fill `title` and
`source_refs`; run `atlas tables validate`; print counts per book and totals.

Edge cases: the three CODH-local ids (brsk00000, hnsd00000, umgy00000) have no NIJL 書誌ID; some
image files in a zip have no CSV rows (blank pages) and still become pages.

Tests: the existing one-book test; a two-book fixture merged into one directory; a report row.

Acceptance: totals 1,086,326 character units, 6,151 pages, 44 documents, 4,328 distinct `unicode`
values among `kind=char` units; `atlas tables validate work/codh` passes.

Size: medium. Depends on: T01, T02.
