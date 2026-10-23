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
   over an existing `out/mirror.sqlite`, since a rebuild renumbers every crop. A rebuild
   (`--replace`) also renumbers the graphemes, which the reference objects name: `reference.py
   --send` runs again after it.
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
  label, model votes) keeps its published value. Witness years come from the catalogue's dating, else from the
  dating already published.
- Review-owned columns (`char`, `state`, `style`, `quiz`, `kind`, `flags`) change only when the
  catalogue's revision is at least the published one, and in D1 only while the crop's revision is
  unchanged; the result is read back. A corpus glyph's come only from its review overlay
  (`units` rows with `origin='corpus'`), which a plain corpus export does not have.
- A crop leaves the galleries and runs only when the catalogue carries it with `origin='retired'`,
  at a revision no older than the published one. The exporters drop withdrawn crops instead, so a
  withdrawal needs a catalogue that names it: `withdraw.py <file>` writes one for the documents of
  `data/vocab/withdrawn.yaml`, every crop on their pages with `origin='withdrawn'` at the local
  copy's revision. The publish deletes the live ones in D1 first, under their revision guard, and
  reads them back; only a crop D1 no longer holds goes on. It is retired, its bundle entry keeps only
  its id and cut, and the images its entry named (its earlier cuts' too) leave the image key map,
  except those another entry names; its reviews and claims stay with its number. A retired entry
  that named it as its replacement names the next live crop on its redirect chain. A crop reviewed
  on the site since keeps its row, record and images, and the publish ends listing it
  (`refused-withdrawals.txt`); a fresh `withdraw.py` catalogue takes it at its new revision. A retired crop the new layout never had (the first
  build left out the 56 live ones, each kept for its history) gets a number and a retired bundle
  entry with its record, so its reviews, claims and links resolve.
- A retired crop's entry names its replacement in `replaced_by`, as an integer crop id: the first text
  id on its `unit_redirects` chain that is a live crop after the publish, past retired crops and text
  ids with no crop, so a reader reaches a live crop in one step. A chain that loops, or ends with no
  live crop, leaves `replaced_by` unset; the publish counts those crops, prints examples and lists
  them in `unreplaced.txt` in its run. An entry that holds its replacement as a text id is rewritten
  by the next publish whose catalogue carries the crop as retired, or by `publish.py --replacements`,
  which checks every retired entry against `source-unit_redirects.sqlite`.
- A catalogue's `corpus_flags`, which `flags.py` writes from a relabel's `review-ids.json`, sends
  each named corpus glyph to the Flagged view with the rows the Worker's admin flag writes: its
  finding in `corpus_flags`, its first `corpus_rows` row, and an event of kind `flag` with the
  wrong-character issue one revision on, under a submission of the actor `publish` keyed
  `publish:<run>:<n>` (one finding and at most 96 glyphs each, the finding in its request as the
  Worker reads it), which moves the crop to `flagged`, its review overlay and its `tally` cells. Each
  glyph is judged on D1's row, since the site moves a corpus glyph's character by itself: a collected
  crop, a glyph with a `corpus_rows` row (anything was written about it), one whose character is not
  the finding's or one withheld from the quiz is left out. In D1 each flag waits on the crop's
  revision, character, kind and quiz and on no `corpus_rows` row, and the local copy takes D1's rows
  back, its tally moved from the cell it counted each glyph in to the cell of D1's row. A submission
  ends naming only the glyphs D1 flagged under it, and goes when it flagged none. The run's
  `flags.json` lists each id by how it was judged, `flags-outcome.json` what D1 flagged and refused.
- A catalogue's `book_styles` (document, style), which `styles.py` writes from
  `data/vocab/document-styles.yaml`, gives every collected crop and corpus glyph of each book that
  style, guarded and read back like the other review-owned columns, and moves each changed crop's
  `tally` cell in the same statements; the style is no bundle field, so no page moves. `styles.py`
  refuses while a glyph or page of a confirmed document, a review event in the layout's local copy
  (`events.style`, or a review whose style is not the one it was shown with), or a review store named
  with `--review` gives a crop a style of its own, and in D1 a crop with such an event is passed
  over. The first styles go the same way: the dry run writes the counts to the run's `styles.json`
  (crop rows, index entries holding `style` per index, tally cells),
  and the send runs only with `--approve-styles N` naming that crop count, after the owner has
  approved the figures.
- A catalogue whose `units` carry `visual` and `context`, as `export_cloudflare.py` writes them, sets
  each published crop's model votes for its cut in its page's object
  `v3/votes/<build>/<page>.json.gz` (`votes.py`), and a reading that is not ready removes them. Only
  the objects that changed are uploaded, once D1 holds the model codes they name; `out/votes/` takes
  each object as it goes up. A retired crop's votes go with it, and a reader takes an entry only while
  its cut is the crop's. A rebuilt layout has its own build id, so its objects start a new folder.
- A crop facts load left unfinished (`crop_facts.py --send`) stops the publish until it is finished.
- A statement over 90 KB stops the publish before anything is allocated.
- A crop's runs are replaced when the catalogue lists runs for it; one with no rows in `unit_ngrams`
  keeps its runs; a run whose crops change in place is updated, not replaced. Every run is written down
  the page: a catalogue run written across it stops the publish, since the layout has no column for its
  direction yet. A run with a crop a `has_grapheme` claim reads as another grapheme is filed under
  that grapheme's head, as the Worker files it when the claim is made; a crop the publish cuts again
  leaves its claims on the old cut. The local copy first takes from D1, and keeps, the runs the Worker
  moved (`run_moves`), their counts and their crops' `run_written` rows; nothing of them is sent back.
  A run is taken out under whatever key it holds when the part lands, its count with it, and a run of
  a claimed or moved crop is put in counted only if it went in. Once the runs have landed, the publish
  files again the runs of every crop whose claim changed since the last reconcile and of every crop in
  `run_moves`, from the claims as they then stand (`reconcile`): a claim disputed meanwhile, a claim a
  redrawn box or a new cut stopped, and a filing the Worker could not finish are put right. Each batch's
  runs are read before its claims and sent at once, `run_written` is written where it differs, and
  `run_moves` keeps the runs a claim still reads. Every move is guarded as the Worker's is, so neither
  undoes nor counts twice what the other did. The publish keeps `run_written` for the
  catalogue's crops and, once the runs have landed, writes again the R2 answers of the books and forms
  shards it and the moves changed and Explore's grids (`run_answers.py`), uploading those whose bytes
  R2 lacks. It refuses a local copy without `run_written` (`runs.py`). `runs.py --migrate` applies the runs
  migrations D1 and the local copy lack, each stamped `migration:<number>`, and nothing else; migration
  0003 (`run_moves`, an empty table) goes before the Worker that writes it.
- A crop joins the family gallery of its character's family (`graphemes.head`). Every crop also keeps
  the family its source names (`crops.family`, migration 0012), and a crop with no character is listed
  with that family: a corpus glyph transcribed も, which may be も or one of its hentaigana, is listed
  with the も family, and so is a crop whose character a review clears. On the 19 October copies this
  lists 6,252 `codh-omt` glyphs and 1,333 collected crops whose source named no family with their
  character's family, which the old galleries left out, and the 24 crops whose record named a family
  other than their character's (21 汙, 2 匘, 1 撿) with their character's family. A crop that a
  `has_grapheme` claim reads as another grapheme is listed with that grapheme's family instead, which
  the Worker reads from the ledger; the publication leaves it alone. `fill.py` fills the column once at
  the cutover; a publish refuses a local copy without it.
- A known crop with another box or image on its own page is a re-cut: it keeps its id, its `cut`
  moves on by one, and its bundle entry takes the new record. Reviews and claims name the cut they
  were made on, so they stay with the old one. A re-cut crop that rounds had marked seen or hard is pending
  again at its new cut, one revision on, with an event of kind `cut` (actor and submission 0), since the
  rounds showed its old box. The version it replaced joins the entry's `cuts` (its
  cut number, box, image, pack place and `at`, when it was recorded), which later records carry along
  unless they name their own. The entry's `cut_at` is when its own cut was recorded: the publish time of
  a re-cut, the old site's first record of the version for a cut the history load moved, carried along
  by later records, as are `encodings`, other encodings of the current cut that a version id names. A
  catalogue's `cut_facts(id, cut_at, encodings)` sets both on crops it does not otherwise publish.
- A known crop on another page, or a new or re-cut box overlapping another crop on its page
  (IoU ≥ 0.5), stops the publish with a report: those need the slot matcher, which is not built yet.
- Order: page bundles to R2 (`v3/pages/`, through rclone's `r2` remote), then the rows in parts of at
  most 40 MiB that never split a page (its crops and its revision go together), then the runs. Each
  part is confirmed by its stamp in `imports`, then replayed on the local copy. A publish stopped
  after allocating is finished by the next run before anything else.
- A dry run writes the parts and bundles under `out/publish/<stamp>/` and publishes nothing.

The exporters number their packs (`pack-10001.bin`, `corpus-0001.bin`), names the bucket's root
already holds for earlier publications. Before a catalogue's packs go up, `address_packs.py
<catalogue.sqlite>` (count only with `--dry-run`) moves each pack its `media` and `corpus_units` rows
name to `packs/<SHA-256>.bin` beside it and points the rows there, in one transaction recorded in
`addressed` before any file moves, so a stopped run finishes on the next. Then
`rclone copy <export>/packs r2:glyph-atlas/packs --immutable` uploads them, and `publish.py` names them.
An export resumed later reads its packs, so `packs/` stays beside it.

One publish runs at a time. The first build's bundles went to R2 with
`rclone copy out/pages r2:glyph-atlas/v3/pages --header-upload "Content-Type: application/json" --header-upload "Content-Encoding: gzip"`.

## Review exports

`/atlas/reviews.json` lists every review in the order it committed, 200 a page; `scripts/fetch_reviews.py`
collects the pages into one file for the offline import. A review saved on the site freezes its record in
R2 before its submission commits (`v3/review-evidence/<sha256>.json.gz`, named in `submissions.evidence`):
what the reviewer saw, and the record its crop is imported from. That baseline is the crop's page bundle
record at the store's revision once a publication has spoken for the crop; before that, the old site's
publication record when the old site reviewed the crop, else the bundle record.

Revisions there count as the old site and the offline store count them. A publication that speaks for a
crop records the catalogue's revision and the crop's own in `review_revisions` (migration 0016), and moves
the crop's own revision on by one when it changes what a reviewer sees; every event since adds one, but a
mark or a re-cut clearing a mark. A crop no publication has spoken for counts its revision less those.
When the publication changed what a reviewer sees (a review-owned column or the cut), or put the crop back,
the row is also a `boundary`: no view or review from before it stands, and an undo of one is refused; a
publication that changes only the revision keeps the last boundary and leaves open views alone.
Migrations 0015 and 0016 are applied to glyph-atlas-v3 before the Worker that reads them deploys;
`publish.py` applies each where what it makes is missing, on D1 and on the local copy.

The reviews moved from the old site are served from `v3/review-legacy/<page>.json.gz`, as the old export
gave them. `review_archive.py --snapshots FILE` builds those archives from the frozen copies of `events`
and `units` and the old site's publication record (`units.snapshot`) of each collected or retired crop
they name, which the copies lack; `--send` uploads them. It writes nothing to D1. They are uploaded before
the Worker deploys: until then the export stops at the first moved review, and a review of a crop the old
site reviewed takes the bundle record as its baseline.

## Publication answers

`answers.py` (dry run with `--dry-run`) publishes what the galleries, the browse page and the homepage
count. It bumps `generation` in `glyph-atlas-v3` from G to G + 1, writes from the local copy, under
`v3/pub/<pub>/`:

- `counts/<char % 256>.json.gz`, per character, and `unread.json.gz`, the unread crops per script: one
  cell per kind, style, state, withheld flag, witness decade and visual group, in the codes of the
  `codes` table. Each count shard also groups crops with no character under their source family;
- `facets.json.gz`: the collected crops per character, book, production, category, review priority,
  state and withheld flag, with each character's text and grapheme key (the browse page's and the
  rounds' counts);
- `corpus.json.gz`: the corpus glyphs per character, production, state and withheld flag;
- `books.json.gz`: each book's collected crops and corpus glyphs (the statistics of the dates);
- `gallery/`: the homepage sample, the corpus glyphs below 1 in 64 of the shuffle range and every
  glyph of Hangul, 구결 and Tangut, records copied in, in shards by shuffle;
- the `site` values `collection` (the collection's status, as `publish_collection_status.py` also
  writes between publications) and `corpus_index` (work/corpus-index/index.json).

then moves `v3/pointer.json` to `{"pub", "through_gen": G}`. A sample glyph's record is read once from
its pack through R2's S3 endpoint (the rclone remote's keys) and kept in `out/records.sqlite`; the
reads come before the bump, so a failed read changes nothing. `publish.py` runs it under its lock
after every publish that sent something, so the counts follow each publish. It refuses while a publish
is unfinished or while the database holds review events of a generation above 0 (the history load's are
absorbed), which it cannot absorb yet, and checks before the bump and again after it. On the local copy of 20 October a run costs one D1 row and 399 R2 objects, and
the sample holds 24,868 glyphs and the Hangul pool 1,750.

## Image key map

`media_map.py <media.sqlite>` (dry run with `--dry-run`) merges a `media` table into the image key
map: `v3/media/<first three hex digits>.json.gz` in R2, each image key's pack, offset and size, with
local copies under `out/media/`. A key mapped to another place stops the run before anything is
written. So does a new key in a pack not named by its content (`pack_names.py`): the bucket's
numbered packs belong to earlier publications. A publish also stops when a corpus glyph it has never published
has its record in such a pack. Changed shards are listed in `out/media/unsent` until they are uploaded, so an interrupted
run's shards go with the next. A publish whose catalogue has a `media` table adds its images the same
way and uploads them before its bundles. The first map comes from the live `media` table:
5,271,887 images in 4,096 shards, 354 MB.

## Cutover

The site moves to the new layout in one window; the old database stays untouched, so going back is
a redeploy of the previous Worker. Steps marked *not built* must exist before the window.

1. **Rehearse** the whole sequence on fresh copies and fix whatever `check.py` reports.
2. **Freeze.** Every session stops publishing to the old database, and the site pauses its writes:
   `echo 1 | wrangler secret put WRITES_PAUSED` from `apps/cloudflare`, which takes effect without a
   deploy. Every request that writes (reviews, rounds, corrections, claims, favourites, form decisions,
   sign-in, passkeys, account and admin changes) then answers 503 `paused` with a Retry-After, while
   reads and signed-in sessions go on. Signing out then only expires the browser's cookies: the session
   row stays valid until it expires, or until it is revoked from another device after writes resume.
   `wrangler secret put` and `wrangler secret delete` deploy a new
   version at once, and refuse while a newer version is uploaded and not deployed, so the freeze is set
   before step 9 uploads anything.
3. **Copy** the live tables again, `pull.py --fresh`, from a Codespace with the Cloudflare
   credentials: the copy is about 3 GB and the uploads after it are large.
4. **Publish** the copy (`catalogue.py --parts 6` writes it from the copies as `catalogue-<k>.sqlite`,
   whole books in each, then `publish.py work/atlas-v3/catalogue-<k>.sqlite` for each k: one publish of
   the whole copy outgrows 16 GB; it re-cuts crops whose box changed and numbers retired ones), merge its images into the map (`media_map.py`) and publish the answers (`answers.py`).
   Before publishing, load the history and crop facts described below, using
   `crop_facts.py --send --defer-crops`. Then `fill.py --send` adds `priority` (0011) and `family`
   (0012, which also gives the empty `tally` a family and witness decade). It fills family, priority
   and cluster in one UPDATE per affected crop, then builds every missing index in
   `migrations-atlas/indexes-cutover.sql`. A retry verifies and resumes the same saved plan, including
   its final release of form decisions. Keep the global write freeze in place throughout.
   The deferred facts load blocks publications and further facts loads until fill finishes. For `family`, on the 19–20 October
   copies: about 2.11M crop rows written (526,318 collected crops and 1,582,510 corpus glyphs whose
   record names a family). The character routes' indexes: about 3.40M entries, as many rows written,
   123 MB: `crop_char_book` 1,815,749 entries (66.2 MB), `crop_undated_corpus` 816,602 (27.7 MB),
   `crop_family` 299,331 (11.5 MB), `crop_family_book` 299,331 (11.4 MB), `crop_family_year` 162,857
   (5.9 MB), `crop_visual` 1,715 and `book_date_axis` 1,078. The publish that follows finds the values in
   place and writes none.
5. **Move the history:** `history.py` translates reviews, submissions, seen marks, skips, favourites,
   claims, the representations forms name, form decisions, suspects and shapes from the copies into
   `out/history.sqlite` (`migrations-atlas/0003_history.sql`, `0008_representations.sql`), with every text id resolved through `out/mirror.sqlite` and
   never through a redirect, and writes a per-table report; it exits 1 if any row stops the move. It
   also writes the earlier cuts, the tombstones and the marks the publisher applies. An event on a crop
   the mirror lacks stops the run until `publish.py out/history-tombstones.sqlite` has numbered its
   tombstone. `load_history.py` then applies `0003_history.sql` (which numbers the mark states `seen`
   and `hard`; the mirror takes the same numbers), `0008_representations.sql` (form anchors),
   `0009_undo.sql` (what an undo restores), and `0010_people.sql` (the labels each event showed and
   named, the corpus rows and flags), and `0013_forms.sql` (`form_bases`, the character before a
   forms decision moved a glyph). It loads, in stamped parts, the rows that differ from the local
   copy: everything on the first load, and after a later copy only the new rows, the changed ones
   (a DELETE and the INSERT) and the DELETE of rows gone, since `history.py` keeps every id an
   earlier load gave (actors, submissions, events by their old id, corpus rows) and numbers only
   what is new after them. It sets
   each pending marked crop's state to its mark one revision on with an event, and writes
   `out/history-cuts.sqlite` for `publish.py` to give 496 crops their earlier cut, 36 the other encodings
   of their current cut (35 corpus glyphs whose source revision changed under the same box, one collected
   crop) and every crop the old site recorded a version of its `cut_at` (`cut_facts`); that publish
   rewrites the bundle of every page of collected crops once. `answers.py`
   follows, since marks change the counts. Suspects and shape orders computed later go in with
   `quiz_marks.py --suspects <files> --shapes <quiz-shapes.json>`, which replaces each table it is given
   by crop through the mirror: without `--send` it only reports the difference from the local copy, and
   `--send` delivers it. `aliases.py` (counts only without `--send`) maps every text
   id in the mirror, and every source of `unit_redirects` followed to the first text id the mirror has,
   to its integer crop id, and uploads the map as `v3/aliases/<first three hex digits of the text id's
   SHA-256>.json.gz`, read by `aliasCrop` in the Worker. A redirect cycle stops it with examples; a
   chain that ends outside the mirror is counted as gone and left out.
   `crop_facts.py --votes` (a dry run without `--send`) moves the crop facts (decision record 0008).
   Each collected crop's model votes in the copy `unit_facts` join its page object in
   `v3/votes/<build>/` when they were read on the image of the crop's current cut; `--votes` is for
   this load only, as publications carry the votes afterwards. The forms tool's clustering comes from
   `work/forms/current`: each cluster becomes an R2 list named by its content,
   `v3/forms/<n>-<digest>.json.gz` (glyphs by rank with tile, split groups, tone and size), each family
   an index object under `v3/forms/families/`, clusters numbered by the mirror's `codes`; tiles the
   image map lacks are rendered, packed under `packs/` and mapped first. D1 takes the tables of
   migrations 0006 and 0014, which it applies: families, clusters with their latest cluster decision by
   key in the loaded `form_decisions`, the glyphs with their own latest glyph decision or carrying the
   latest cluster decision made on them while their cluster has had none since (named in `carried`),
   and `crops.cluster`. Text ids resolve through the mirror and never through a redirect; glyphs naming
   a crop the mirror lacks or a retired crop, and readings of another image, are dropped and counted in
   `out/crop-facts-report.txt`, and the counts are worked out again from what is kept. D1 takes the
   difference from the local copy, so a rerun after a new clustering sends only what changed, and
   readings an earlier load wrote are written again from the fresh copy. `--send` refuses a database
   holding form decisions the local copy lacks, holds decisions (`form_state.loading`) while its rows
   go, and deletes an object under `v3/forms/` that nothing names once a load two hours or more after
   the one that first found it unnamed runs. At cutover use `--send --defer-crops`: the metadata and
   lists load first, while the shared fill handles the approximately 1.33M cluster assignments together
   with family and priority. Review the combined dry-run figures before sending the fill. A new
   clustering after the cutover is loaded the same way, without `--votes` or `--defer-crops`.
6. **Move the book facts:** `books.py` translates each book's dating (both axes), the titles, the
   characters another site lists per book and the site-wide values (`collection`, `corpus_index`,
   `review_epoch`; the citations are `reference.py`'s) from the copies into `out/books.sqlite`
   (`migrations-atlas/0005_books.sql`), books and occurrences by integer id through
   `out/mirror.sqlite`, and writes the parts and their figures to `out/books-report.txt`; facts about
   a document that is not a book are left out with a count. Whether the site shows an occurrence with
   its review state is not stored, since every round changes it: a route derives it when it reads,
   from the crop's kind (a collected crop always) and, for a corpus glyph, from `seen`, `skips` and
   `events` (a round showed, skipped or reviewed it). The query, bounded by the `book_characters` key
   and the `seen_crop`, `skips` and `event_crop` indexes, is in `0005_books.sql`. `books.py --send` applies `0005_books.sql`
   and loads only what differs from the local copy, in stamped parts. A book's title comes from its
   first page bundle, else from the source the first of that bundle's crops names, else from its
   corpus publication. The corpus glyphs per book and the homepage
   sample are derived from the crops by `answers.py`, so neither is copied. Keeping the
   facts current after the cutover is the generators' work: they write the book facts input,
   `book-facts-input.sqlite` in `ATLAS3_DIR` (`src/glyph_atlas/book_facts_input.py`), and `books.py`
   reads it in place of copies:

       .venv/bin/python scripts/export_dates_cloudflare.py work/dates
       .venv/bin/python scripts/export_document_characters.py <dataset atlas ainu merge wrote>

   The dates export holds its claims and every document's dating as `glyph_atlas.dates` resolved it,
   books or not. `books.py` asserts the claims that stand nowhere in the ledger of the local copy
   (loaded by `load_history.py`, which must come first), retracts the dates a source no longer states,
   and resolves each slot it changes again in `glyph-atlas-v3` with the ledger's resolver as the Worker
   runs it, so the verdicts readers gave on the site count. The date claims on documents that are not books stay in
   `assertions` (11,998 of the 14,095 at the cutover), and their dating stays in the input, so a
   document that gains crops later takes its `book_dates` on the next run. The characters export
   replaces the documents it lists (all ainu-records covers, or those `--documents` names) and keeps
   the rest; `books.py` replaces `book_characters` only for the books of documents the input lists,
   so an input of some documents leaves the other books' characters as they are. The corpus publication's
   titles and the catalogue's site values are still read from the copies.
   `runs.py` (a dry run without `--send`) adds how the runs are written
   (`migrations-atlas-runs/0002_forms.sql`, which `--send` applies to `glyph-atlas-v3-runs`):
   `run_written` holds each crop written apart from its grapheme family's head, with its character, so a
   run's written form is its crops' rows, else their heads; a review that changes a crop's written
   character writes its row. The counts go to R2 under `v3/runs/` (`run_answers.py`): Explore's grid of
   each length with the forms each run gathers, each book's grid, and every run's forms sharded by length
   and first code point. `--send` loads the rows in stamped parts, uploads the objects whose bytes R2
   lacks, then inserts one stamp into `glyph-atlas-v3`'s `imports`, which moves the publication key the
   runs' cached answers are kept under. Figures go to `out/runs-report.txt`; `check.py` compares each run's
   written form and every answer with the live runs. **The first `--send` writes 297,905 rows of
   `glyph-atlas-v3-runs` and 1 of `glyph-atlas-v3`, and uploads 12,153 objects (6.3 MB) on the copies of 19 October 2026; it needs the owner's
   approval with the dry run's figures.**
7. **Move the reference data:** the generators write the reference input,
   `reference-input.sqlite` in `ATLAS3_DIR` (`src/glyph_atlas/reference_input.py`):

       .venv/bin/python scripts/export_graphemes.py --catalogue work/atlas-v3/source-characters.sqlite \
           --aliases work/atlas-v3/source-aliases.sqlite
       .venv/bin/python scripts/export_character_variants.py     # about half an hour on every processor
       .venv/bin/python scripts/export_components_cloudflare.py

   At the cutover the cards and their search aliases came from the copies of the live ones (`pull.py
   characters aliases`), so the site keeps the counts it shows; after it, from each full export's
   catalogue (`--catalogue` alone, which holds both), and `export_graphemes.py` without it rewrites the
   cards whose family the character table changed. The graph, its derived tier, the writers, words and
   spellings, their citations, the IDS and the component index are generated from the repository's
   data each time. `reference.py` (dry run by default) writes the cards with their edges, derived forms
   and writers as one R2 object per character under `v3/reference/`, beside sharded card summaries, the
   IDS, the name list, the card lists, one list per component and the kana readings of
   `data/vocab/kanji-readings.json` with their kanji's families (run it again after
   `scripts/build_kanji_readings.py`); the rows a query filters, joins or sorts on go to the tables of
   `migrations-atlas/0004_reference.sql` and to `names`, the cards' search aliases, and the citations
   to `site` (`variant_sources`, `word_sources`; `books.py --send` makes the table, migration 0005). A
   card's character or family head the layout lacks gets the next grapheme id, and every grapheme takes
   its card's family head. A visual group's samples are crop ids found in the mirror and in `crops`.
   `--send` applies 0004 once, loads only the rows that differ from the local copy in stamped parts,
   then uploads the objects whose hash differs from `v3/reference/manifest.json.gz` and deletes the
   paths that manifest lists and the translation lacks; figures go to `out/reference-report.txt`. A
   load that stopped partway is finished on the next run, also one whose part D1 applied and the
   local copy missed: the run checks D1 for the stamps of every load the local copy has not finished.

   The run reads about 1 GB of input and writes as much again to scratch files and the stage
   folder; it holds one object at a time (190 MB resident at most on the copies of 19–20 October
   2026). Run it in the batch slice:

       systemd-run --user --unit atlas3-reference --slice=batch.slice -p MemorySwapMax=0 --same-dir \
         --setenv=ATLAS3_DIR=$PWD/work/atlas-v3 -- .venv/bin/python scripts/atlas3/reference.py --send
       journalctl --user -u atlas3-reference -f
8. **Copy the accounts** into their own database, `glyph-atlas-accounts`, last, with sign-in and
   session changes still frozen. It is created once, before the window:
   `bunx wrangler d1 create glyph-atlas-accounts` (from `apps/cloudflare`). Then:

       python3 scripts/atlas3/accounts.py pull --fresh
       python3 scripts/atlas3/accounts.py load
       python3 scripts/atlas3/accounts.py load --send
       python3 scripts/atlas3/accounts.py check

   `pull` finds the authentication tables in the old database's `sqlite_master`: `user`,
   `verification`, `rateLimit` and every table whose foreign keys lead to `user` (`session`,
   `account`, `passkey`, `account_profiles`), less those `pull.py` copies for the history
   (`favourites`). A table that is neither and is not named, with its reason, in `LEFT` in
   `accounts.py` stops it. It copies their CREATE statements (with their indexes) and their rows in
   primary-key ranges into `accounts.sqlite`, then compares the copy with the old database as `check`
   does; it only reads. `load` writes the tables with those exact statements, the rows (parents
   first) and then the indexes as parts under `accounts-load/<copy>-<base>/`, named by the copy's
   checksums and the parts already loaded, and replays them on what is loaded to confirm they rebuild
   the copy. Only `--send` writes to D1: each part is confirmed by its stamp in an `imports` table, and
   a database holding a stamp no part sent from here wrote, or tables of its own, is refused. Every part sent is replayed on `accounts-loaded.sqlite`, and
   a `load` after `pull --fresh` writes only the difference from it: an INSERT per new row, an UPDATE
   per changed one and a DELETE per row gone, children first. If `--send` stops partway, run
   `load --send` again: it sends only the missing parts. `check` compares the table set, the schema, row counts and a checksum
   of every row per table, first between the old database and `accounts.sqlite`, then between
   `accounts.sqlite` and `glyph-atlas-accounts`, and exits 1 on any difference. Run it before the
   site checks of step 9: their sign-ins and session
   renewals write to `glyph-atlas-accounts`, after which it no longer matches.

   `accounts.sqlite` and `accounts-load/` hold sessions and account tokens; the script creates them
   readable by the owner only, as is `accounts-loaded.sqlite`. Once the switch's last load and `check` pass,
   delete all three, in the Codespace too:

       rm -rf work/atlas-v3/accounts.sqlite* work/atlas-v3/accounts-loaded.sqlite work/atlas-v3/accounts-load

9. **Check:** `check.py` must pass every check, and a set of site URLs must answer the same from a
   local Worker on each layout. The site checks that write (an existing session, renewal, logout, a
   history page, favourites, a claim, an old URL) run on this machine: `wrangler dev` runs the new
   Worker locally, with its bindings reaching the real new databases and bucket, and nothing answers
   on a public address. The deployed Worker stays paused throughout, so every row written in the
   window is one of these checks'. Version URLs stay off (`preview_urls: false`): turned on, they
   would also open older versions, which are bound to the old database and are not paused.

   Before the window, make a test account on glyphatlas.org (a code by mail to an address you hold)
   and copy its `__Secure-better-auth.session_token` cookie from the browser. Then, from
   `apps/cloudflare`, with the new Worker built:

   - copy `wrangler.jsonc` to `wrangler.check.jsonc` (ignored by git) and add `"remote": true` to
     the `ATLAS`, `ATLAS_RUNS` and `ACCOUNTS` entries of `d1_databases` and to `MEDIA` in
     `r2_buckets`; wrangler 4.135.0's config schema takes `remote` on each D1 and R2 binding, and a
     D1 binding reaches `database_id` (set no `preview_database_id`);
   - put the deployed `BETTER_AUTH_SECRET` in `.dev.vars.cutover` (ignored by git), so the test
     account's cookie is accepted. `wrangler secret` cannot print a secret, so it comes from where
     it is kept; without it, the existing session is checked on the site right after the switch.
     The file holds no `WRITES_PAUSED`.

   ```sh
   bunx wrangler dev -c wrangler.check.jsonc --env-file .dev.vars.cutover --ip 127.0.0.1 --local-protocol https
   ```

   Every request below goes to `https://127.0.0.1:8787` (`curl -k`) with `cf-connecting-ip:
   192.0.2.77`, the test account's cookie, and, on a POST, `origin: https://127.0.0.1:8787`. Over
   https the Worker names its cookies with the `__Secure-` prefix, as the site does.

   1. `GET /api/auth/get-session?disableCookieCache=true` answers the test account.
   2. Renewal: on `glyph-atlas-accounts`, move the test session's `expiresAt` back to 88 days from
      now (`UPDATE session SET expiresAt=… WHERE userId=:test`, an ISO time as stored); the same read
      then answers an `expiresAt` 90 days out.
   3. A favourite, set and read back; the history page; an old text-id URL, which answers the crop
      by its integer id.
   4. A claim: a form claim of an IDS outside the crop's grapheme whose form already exists
      (`SELECT count(*) FROM forms f JOIN representations r ON r.id=f.anchor WHERE r.scheme='ids'
      AND r.value=:ids` is 1), on a crop with no `current_claims` row (`SELECT count(*) FROM
      current_claims WHERE crop=:crop` is 0). It writes the ledger and the crop's claim, and no
      review, representation or form.
   5. `POST /api/auth/sign-out` last; the read in 1 then answers null.

   The checks write these rows, and nothing else writes in the window. `:test` is the test
   account's user id, which is also its actor key; `:crop` is the claimed crop. Count each set with
   `SELECT` first; then, with `bunx wrangler d1 execute <database> --remote --command "…"`:

   ```sql
   -- glyph-atlas-v3
   DELETE FROM favourites WHERE user = :test;
   DELETE FROM current_claims WHERE crop = :crop AND predicate = 'has_form'
     AND subject IN (SELECT subject FROM assertions WHERE asserted_by = :test AND predicate = 'has_form');
   DELETE FROM ledger_submissions WHERE id IN (SELECT submission FROM assertions WHERE asserted_by = :test);
   DELETE FROM ledger_submissions WHERE id IN (SELECT submission FROM assertion_actions
     WHERE assertion IN (SELECT id FROM assertions WHERE asserted_by = :test));
   DELETE FROM assertion_actions WHERE assertion IN (SELECT id FROM assertions WHERE asserted_by = :test);
   DELETE FROM assertion_evidence WHERE assertion IN (SELECT id FROM assertions WHERE asserted_by = :test);
   DELETE FROM assertion_premises WHERE assertion IN (SELECT id FROM assertions WHERE asserted_by = :test);
   DELETE FROM assertions WHERE asserted_by = :test;
   DELETE FROM actors WHERE user = :test;
   -- glyph-atlas-accounts
   DELETE FROM rateLimit WHERE key >= '192.0.2.77|' AND key < '192.0.2.77}';
   DELETE FROM session WHERE userId = :test;
   DELETE FROM account_profiles WHERE account IN (SELECT id FROM account WHERE userId = :test);
   DELETE FROM account WHERE userId = :test;
   DELETE FROM passkey WHERE userId = :test;
   DELETE FROM "user" WHERE id = :test;
   ```

   The test account had no reviews, so its actor names no submission
   (`SELECT count(*) FROM submissions WHERE actor IN (SELECT id FROM actors WHERE user = :test)` is
   0). Each delete reads by an index or a key range, and each reaches only the checks' rows: the
   test account's own and the test account's claim on a crop nothing named before the window. The
   IDS's form and representation were there before and stay. Delete `wrangler.check.jsonc` and
   `.dev.vars.cutover` after.

   Then upload the checked Worker, still paused, and make the version that will be deployed:

   ```sh
   bunx wrangler versions upload                         # keeps the secrets, WRITES_PAUSED among them
   bunx wrangler versions secret delete WRITES_PAUSED    # a further version without it, not deployed; prints its id
   ```

   `versions upload` adds to the previous version's secrets and deletes none (its `--help`), and
   neither command deploys, so the site keeps answering from the paused version. With Version URLs
   off, the new versions answer nowhere until they are deployed.
10. **Switch** the Worker's routes to the new layout and resume writes:
   `bunx wrangler versions deploy <id of the version without WRITES_PAUSED>@100%`. This one command
   is the switch: the paused version answers until it runs, and the new one, unpaused, after.
   Passkey sign-in (bound to glyphatlas.org) and a provider sign-in (its callback names
   glyphatlas.org) are checked on the site right after.
   Before writes resume, every `v3/aliases/` shard must be uploaded and checked: the shard count
   `aliases.py --send` printed, and each file in `out/aliases/` present in the bucket with the same
   size. A write naming a text id whose shard is missing is refused with 409 `reload`, and the review
   page treats that as final: a save kept on the reader's device is marked gone and never sent again.
   No script checks the shards yet.

`check.py` compares the copies with the local copy of the new layout, crop by crop: numbers, row
values, bundle boxes and images, runs and the image map. It reads nothing remote.

`parity.py --old <url> --new <url>` asks both Workers the public GET routes about a seeded sample of
crops, corpus glyphs, characters, runs, documents, pages and images, at 2 requests a second, maps
text ids to public ids through the mirror before comparing, and writes `out/parity-<stamp>.txt`; it
exits 1 on any difference outside `CHANGED`.

## Not in the new databases yet

These stay in the live database until cutover, when they move with integer crop ids or become R2
answers:

- the claim ledger (`assertions`, `assertion_*`, `current_claims`, `ledger_submissions`,
  `rejections`), including `writes_word` claims;
- reviews (`events`, `submissions`, `skips`, `seen`, `actors`), accounts (`user`, `session`,
  `account`, `account_profiles`, `passkey`, `verification`, `rateLimit`) and `favourites`;
- `representations` (form anchors), which form claims write: they move with the forms;

The Worker read path builds on the catalogue stamps of #763 (`catalogueStamps`, `catalogueKey`), so a
reviewer's own save still shows at once.

Anything that changes in the live database after the copy reaches the new databases at cutover, from
a fresh copy (`pull.py --fresh`).
