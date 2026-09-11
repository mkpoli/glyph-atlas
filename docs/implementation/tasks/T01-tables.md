# T01 Table store and validation

Goal: one place that writes, reads, validates and merges the dataset tables.

Read first: `docs/schema.md`, `src/kuzushiji_atlas/schema.py`, `src/kuzushiji_atlas/tables.py`.

Outputs
- `src/kuzushiji_atlas/tables.py`: `write(path, records)`, `read(path)`, plus `Dataset` class over a
  directory holding `documents.parquet`, `pages.parquet`, `lines.parquet`, `units.parquet`,
  `groups.parquet`, `reviews.jsonl` (optional). `Dataset.load(dir)`, `Dataset.validate()`,
  `Dataset.merge(others, out)`.
- CLI: `atlas tables validate <dir>`, `atlas tables merge <dir>... --out <dir>`,
  `atlas tables stats <dir>` (counts per table, units by `script`, `kind`, `review`, `method`).

Steps
1. Write tables with an explicit Arrow schema derived from the Pydantic models so that an empty
   table has the right columns; nested models become struct columns; enums become strings.
2. `validate` re-parses every row through the model and checks references: `units.page_id` in
   `pages.id` when not null, `units.line_id` in `lines.id`, `lines.page_id` in `pages.id`,
   `pages.document_id` in `documents.id`, `groups.unit_ids` in `units.id`, boxes inside the page
   image when width and height are known. Errors are collected and printed with the offending id;
   the command exits 1 on any error.
3. `merge` concatenates tables, fails on duplicate ids with different content, keeps one row for
   identical duplicates, and writes rows sorted by id so output is deterministic.
4. `stats` prints a Markdown table.

Edge cases: a directory missing optional tables (lines, groups); a units table with all-null
`page_id` (standalone crops); a page with width 0 (unknown), which skips the box check.

Tests: round trip of every model; a validation failure for a dangling `page_id`; merge of two
directories with one overlapping identical row and one conflicting row.

Acceptance: `uv run atlas tables validate work/codh` passes on the output of the CODH importer;
`stats` shows 121 units for the sample book.

Size: small. Depends on: nothing.
