# T01 Table store and validation

Goal: one module that writes, reads, validates and merges the dataset tables, and that still works
at ten million units.

Read first: `docs/implementation/README.md`, `docs/schema.md`, `src/kuzushiji_atlas/schema.py`,
`src/kuzushiji_atlas/tables.py`.

Outputs
- `src/kuzushiji_atlas/tables.py`: `schema_for(model) -> pyarrow.Schema` built from the Pydantic
  model (enums as strings, nested models as structs, `dict[str, Any]` fields as JSON strings in a
  string column, dates as `date32`, datetimes as `timestamp[us, UTC]`); `write(path, records,
  model)` so that an empty table still has every column; `read(path, model) -> list[model]` and
  `scan(path, model, columns=None, batch_size=65_536)` yielding validated batches without loading
  the file; `Dataset(dir)` with `tables` = documents, pages, page_texts, lines, units, groups,
  each optional except documents; `Dataset.validate()`; `Dataset.merge(others, out)`.
- Sharding: `units` and `lines` may be a directory of shards `units/<bucket>.parquet`, bucket =
  first two hex digits of `sha1(document_id)`; `scan` walks shards; `write` shards when given
  `shard=True`. Rows inside a file are sorted by `document_id`, `page_id`, `seq`, `id`.
- A `MANIFEST.json` per dataset directory: schema version (`1`), row counts per table, sha256 per
  file, writer version, the command that produced it.
- CLI: `atlas tables validate <dir>`, `atlas tables merge <dir>... --out <dir>`,
  `atlas tables stats <dir>`.

Validation rules: every row parses; `units.document_id` in documents; `units.page_id` (when set)
in pages; `units.line_id` (when set) in lines; `lines.page_id` in pages; `pages.document_id` in
documents; `groups.unit_ids` in units; `page_texts.page_id` in pages; box `w`, `h` > 0; a box
inside its page when width and height are known; a unit with `page_id` null has `crop` set; ids
unique per table. Errors are collected with the offending id; exit 1 on any error.

Merge: concatenate; identical duplicate rows collapse to one; duplicate ids with different content
fail with both rows printed; output sorted as above.

Tests: round trip of every model including an empty table; a dangling `page_id`; a unit without
page and without crop; merge with one identical and one conflicting duplicate; `scan` over a
sharded directory; `MANIFEST.json` counts equal the rows written.

Acceptance: `uv run atlas import codh cache/codh/200006663.zip` (the importer already in the
repository) followed by `atlas tables validate work/codh` passes and `stats` reports 121 units.

Size: medium. Depends on: nothing.
