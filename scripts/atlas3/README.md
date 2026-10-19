# New storage layout: first build

The tools that fill the D1 databases of [decision record 0005](../../docs/decisions/0005-storage-rebuild.md)
from the live database: `glyph-atlas-v3` (`0b475d45-dfe4-49a6-8c44-996289e82995`: crops, pages,
books, graphemes, names, review tables) and `glyph-atlas-v3-runs`
(`61a9e757-1343-4e4e-801b-f9c23f66b531`: runs of two and three crops). Their schemas are
`apps/cloudflare/migrations-atlas/` and `apps/cloudflare/migrations-atlas-runs/`.

Data lives in `work/atlas-v3/` (or `$ATLAS3_DIR`), outside the repository:

- `source-<table>.sqlite`: copies of the live tables;
- `out/atlas.sqlite`, `out/atlas-runs.sqlite`: local copies of the new databases;
- `out/mirror.sqlite`: the id allocation (text id → integer crop id, page and book keys, codes),
  the only record of which number each crop has. Back it up after every change;
- `out/pages/<page>-<rev>.json.gz`: page bundles.

## Steps

1. `pull.py` copies the live tables in primary-key ranges of a few thousand rows, so no read scans,
   and it only reads. An interrupted copy resumes; a finished one is copied again only with `--fresh`.
2. `build.py` writes the new layout and stops unless every source crop has exactly one crop, whose id
   names its record's page, with its page and book, and every run has its crops. It refuses to run
   over an existing `out/mirror.sqlite`, since a rebuild renumbers every crop.
3. `sample.py` compares random crops with their source records field by field.
4. `load.sh` creates the tables, loads the rows in parts, then builds the indexes. A rerun skips the
   parts of the same build that are already loaded.

## Not in the new databases yet

These stay in the live database until cutover, when they move with integer crop ids or become R2
answers:

- the claim ledger (`assertions`, `assertion_*`, `current_claims`, `ledger_submissions`,
  `rejections`), including `writes_word` claims;
- reviews (`events`, `submissions`, `skips`, `seen`, `actors`), accounts (`user`, `session`,
  `account`, `account_profiles`, `passkey`, `verification`, `rateLimit`) and `favourites`;
- forms (`forms`, `form_*`), `words`, `word_spellings`;
- character data (`characters` cards, `character_variants`, `character_derived`,
  `component_variants`, `han_ids`, `han_components`, `han_component_names`, `grapheme_writers`,
  `document_characters`, `corpus_documents`, `representations`);
- the model votes in `units.visual`, and the text-id alias files.

Anything that changes in the live database after the copy reaches the new databases at cutover, from
a fresh copy (`pull.py --fresh`).
