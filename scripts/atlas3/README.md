# New storage layout: build and publish

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
- `out/pages/<page>-<rev>.json.gz`: page bundles, the latest revision of each page. A crop's entry is
  its whole record, less its page key, the fields its row in `crops` holds, those a review or a form
  decision sets (`issue`, the written character and its identity) and those the Worker computes
  (`crop_editable`, `crop_version`). A new record replaces the old one; an image keeps its pack place
  when the catalogue has no `media` table to look it up;
- `out/publish/<stamp>/`: each publish's parts, bundles and journal. A publish listed in the
  mirror's `pending` table is finished by the next run, so its folder must stay until then.

Publishing needs an rclone remote named `r2` (or `$ATLAS3_R2_REMOTE`) that reaches the site's R2
account. A schema change is applied by hand to databases that are already loaded; `load.sh` only
creates them.

## Steps

1. `pull.py` copies the live tables in primary-key ranges of a few thousand rows, so no read scans,
   and it only reads. An interrupted copy resumes; a finished one is copied again only with `--fresh`.
2. `build.py` writes the new layout and stops unless every source crop has exactly one crop, whose id
   names its record's page, with its page and book, and every run has its crops. It refuses to run
   over an existing `out/mirror.sqlite`, since a rebuild renumbers every crop.
3. `sample.py` compares random crops with their source records field by field.
4. `load.sh` creates the tables, loads the rows in parts, then builds the indexes, and records the
   build id in `imports`; `publish.py` refuses a mirror whose build the database does not name. A
   rerun skips the parts already loaded, and refuses once anything has been published.

## Publishing into the new layout

    python3 scripts/atlas3/publish.py <catalogue.sqlite> --dry-run
    python3 scripts/atlas3/publish.py <catalogue.sqlite>

The catalogue is what `export_cloudflare.py` and `export_cloudflare_corpus.py` already write
(`units`, and when present `corpus_units`, `media`, `unit_ngrams`, `pages`, `document_dating`).

- A new text id gets a page and slot once, from `out/mirror.sqlite`. The allocation and the record
  that the publish is pending are one transaction, saved before anything is sent.
- Only what differs from the local copy is sent; pages whose crops and facts did not change keep
  their revision. A field the catalogue does not carry (form, cluster, cut, other flag bits, a corpus
  label) keeps its published value. Witness years come from the catalogue's dating, else from the
  dating already published.
- Review-owned columns (`char`, `state`, `style`, `quiz`, `kind`, `flags`) change only when the
  catalogue's revision is at least the published one, and in D1 only while the crop's revision is
  unchanged; the result is read back. A corpus glyph's come only from its review overlay
  (`units` rows with `origin='corpus'`), which a plain corpus export does not have.
- A crop leaves the galleries and runs only when the catalogue carries it with `origin='retired'`,
  at a revision no older than the published one. The exporters drop withdrawn crops instead, so a
  withdrawal needs a catalogue that names it. A retired crop the new layout never had (the first
  build left out the 56 live ones, each kept for its history) gets a number and a retired bundle
  entry with its record, and with `replaced_by` where the catalogue's `unit_redirects` names its
  replacement, so its reviews, claims and links resolve.
- A crop's runs are replaced when the catalogue lists runs for it; one with no rows in `unit_ngrams`
  keeps its runs.
- A known crop with another box or image on its own page is a re-cut: it keeps its id, its `cut`
  moves on by one, and its bundle entry takes the new record. Reviews and claims name the cut they
  were made on, so they stay with the old one.
- A known crop on another page, or a new or re-cut box overlapping another crop on its page
  (IoU ≥ 0.5), stops the publish with a report: those need the slot matcher, which is not built yet.
- Order: page bundles to R2 (`v3/pages/`, through rclone's `r2` remote), then the rows in parts of at
  most 40 MiB that never split a page (its crops and its revision go together), then the runs. Each
  part is confirmed by its stamp in `imports`, then replayed on the local copy. A publish stopped
  after allocating is finished by the next run before anything else.
- A dry run writes the parts and bundles under `out/publish/<stamp>/` and publishes nothing.

One publish runs at a time. The first build's bundles went to R2 with
`rclone copy out/pages r2:glyph-atlas/v3/pages --header-upload "Content-Type: application/json" --header-upload "Content-Encoding: gzip"`.

## Publication answers

`answers.py` (dry run with `--dry-run`) publishes what the galleries count. It bumps `generation`
in `glyph-atlas-v3` from G to G + 1, writes per-character counts from the local copy to
`v3/pub/<pub>/counts/<char % 256>.json.gz` and the unread crops' counts per script to
`v3/pub/<pub>/unread.json.gz`, then moves `v3/pointer.json` to `{"pub", "through_gen": G}`. A cell
counts the crops of one kind, style, state, withheld flag, witness decade and visual group, in the
codes of the `codes` table. It runs after a publish, under the same lock, and refuses while a publish
is unfinished or while the database holds review events, which it cannot absorb yet. Each run costs
one D1 row and 258 R2 objects.

## Image key map

`media_map.py <media.sqlite>` (dry run with `--dry-run`) merges a `media` table into the image key
map: `v3/media/<first three hex digits>.json.gz` in R2, each image key's pack, offset and size, with
local copies under `out/media/`. A key mapped to another place stops the run before anything is
written. Changed shards are listed in `out/media/unsent` until they are uploaded, so an interrupted
run's shards go with the next. A publish whose catalogue has a `media` table adds its images the same
way and uploads them before its bundles. The first map comes from the live `media` table:
5,271,887 images in 4,096 shards, 354 MB.

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
- the model votes in `units.visual`, and the text-id alias files;
- family heads and search names for characters that first appear after the first build;
- `has_grapheme` claims, which file a crop under a grapheme other than its character's family: the
  new `crops` has no per-crop grapheme column yet, so the read path decides how such a crop joins a
  family gallery before cutover.

The Worker read path builds on the catalogue stamps of #763 (`catalogueStamps`, `catalogueKey`), so a
reviewer's own save still shows at once.

Anything that changes in the live database after the copy reaches the new databases at cutover, from
a fresh copy (`pull.py --fresh`).
