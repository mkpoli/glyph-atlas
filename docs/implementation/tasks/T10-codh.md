# T10 Import the whole CODH くずし字データセット

Goal: all 44 books of 日本古典籍くずし字データセット in the tables, page images in the cache.

Read first: `src/kuzushiji_atlas/importers/codh.py` (one-book importer), `data/sources/codh-char-shape.yaml`,
http://codh.rois.ac.jp/char-shape/book/, the NIJL specification PDF named in the source file.

Inputs: per-book zips `http://codh.rois.ac.jp/char-shape/dataset/v2/{bid}.zip` (8 MB to 434 MB)
or the whole set without crops `http://codh.rois.ac.jp/char-shape/dataset/v2/all.zip` (5.14 GB;
`full.zip` includes crops and is not needed).

Outputs
- `data/sources/codh-books.tsv`: bid, title, code points, characters, release date as on the book
  page, and `holder` where the NIJL 書誌ID resolves on 国書データベース (blank otherwise), with the
  date of the listing in the header.
- `work/codh/`: documents (`title` from the TSV, `production` woodblock unless the book page says
  写本), pages for every image in a zip including blank ones (`codh:{bid}:{image}`), units as the
  one-book importer makes them, with `document_id` set. Page images go into the image cache through
  `images.register(path, url)` with the CODH IIIF service URL
  `https://codh.rois.ac.jp/char-shape/iiif/{bid}/{image}.tif` as their key.
- `_report.csv` rows become units `codh:{bid}:{image}:report:{n}` with `kind=unreadable`,
  `review=rejected`, `text_source` null, the report text in `upstream["report"]`; they are counted
  apart from character units.
- Labels stay what CODH gives: modern kana code points with `classification=identified` for kanji
  and marks and `classification=unassessed` for kana (the hentaigana form is unknown).
- CLI: `atlas import codh --all [--from-zip cache/codh/all.zip] [--books bid,bid]`; the one-book
  form stays.

Steps: parse the book list once; download with T02; import each book; validate; print per-book and
total counts.

Edge cases: brsk00000, hnsd00000 and umgy00000 carry no NIJL 書誌ID; a zip whose CSV names an image
absent from `images/` (page created with size 0, warning printed).

Tests: the existing one-book test; a two-book fixture merged into one directory; a report row; a
kana unit gets `classification=unassessed`.

Acceptance: 1,086,326 character units, 6,151 pages, 44 documents, 4,328 distinct `unicode` among
character units; report units counted separately in the pull request; `atlas tables validate`
passes.

Size: medium. Depends on: T01, T02.
