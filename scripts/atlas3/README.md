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
  were made on, so they stay with the old one. The version it replaced joins the entry's `cuts` (its
  cut number, box, image and pack place), which later records carry along unless they name their own.
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
   before step 8 uploads anything.
3. **Copy** the live tables again, `pull.py --fresh`, from a Codespace with the Cloudflare
   credentials: the copy is about 3 GB and the uploads after it are large.
4. **Publish** the copy (`publish.py`, which re-cuts crops whose box changed and numbers retired
   ones), merge its images into the map (`media_map.py`) and publish the answers (`answers.py`).
5. **Move the history:** `history.py` translates reviews, submissions, seen marks, skips, favourites,
   claims, form decisions, suspects and shapes from the copies into `out/history.sqlite`
   (`migrations-atlas/0003_history.sql`), with every text id resolved through `out/mirror.sqlite` and
   never through a redirect, and writes a per-table report; it exits 1 if any row stops the move. It
   also writes the earlier cuts, the tombstones and the marks the publisher applies. An event on a crop
   the mirror lacks stops the run until `publish.py out/history-tombstones.sqlite` has numbered its
   tombstone. `load_history.py` then applies `0003_history.sql` (which numbers the mark states `seen`
   and `hard`; the mirror takes the same numbers), loads every translated row in stamped parts, sets
   each pending marked crop's state to its mark one revision on with an event, and writes
   `out/history-cuts.sqlite` for `publish.py` to give 496 crops their earlier cut. `answers.py`
   follows, since marks change the counts. `aliases.py` (counts only without `--send`) maps every text
   id in the mirror, and every source of `unit_redirects` followed to the first text id the mirror has,
   to its integer crop id, and uploads the map as `v3/aliases/<first three hex digits of the text id's
   SHA-256>.json.gz`, read by `aliasCrop` in the Worker. A redirect cycle stops it with examples; a
   chain that ends outside the mirror is counted as gone and left out.
6. **Move the book facts:** `books.py` translates each book's dating (both axes), the titles, the
   characters another site lists per book and the site-wide values (`collection`, `corpus_index`,
   `review_epoch`, `variant_sources`, `word_sources`) from the copies into `out/books.sqlite`
   (`migrations-atlas/0005_books.sql`), books by integer id and occurrences by crop through
   `out/mirror.sqlite`, and writes the parts and their figures to `out/books-report.txt`; facts about
   a document that is not a book are left out with a count. `books.py --send` applies `0005_books.sql`
   and loads only what differs from the local copy, in stamped parts. A book's title comes from its
   first page bundle, else from its corpus publication. The corpus glyphs per book and the homepage
   sample are derived from the crops by `answers.py` (*not built*), so neither is copied. Keeping the
   facts current after the cutover is *not built*: the dates export, the corpus publication's titles,
   ainu-records' character lists and the catalogue's site values still write the old database.
7. **Copy the accounts** into their own database, `glyph-atlas-accounts`, last, with sign-in and
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
   first) and then the indexes as parts under `accounts-load/<copy>/`, named by the copy's checksums,
   and replays them on an empty database to confirm they rebuild the copy. Only `--send` writes to D1:
   each part is confirmed by its stamp in an `imports` table, and a database holding another copy's
   rows or tables of its own is refused. If `--send` stops partway, run `load --send` again on the
   same copy, without `pull --fresh`: it sends only the missing parts, and a copy differing in any
   row would be refused by the half-loaded database. `check` compares the table set, the schema, row counts and a checksum
   of every row per table, first between the old database and `accounts.sqlite`, then between
   `accounts.sqlite` and `glyph-atlas-accounts`, and exits 1 on any difference. Run it before the
   site checks of step 8: their sign-ins and session
   renewals write to `glyph-atlas-accounts`, after which it no longer matches.

   `accounts.sqlite` and `accounts-load/` hold sessions and account tokens; the script creates them
   readable by the owner only. Once `check` passes, delete both, in the Codespace too:

       rm -rf work/atlas-v3/accounts.sqlite* work/atlas-v3/accounts-load

8. **Check:** `check.py` must pass every check, and a set of site URLs must answer the same from a
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
     AND EXISTS (SELECT 1 FROM assertions WHERE crop = :crop AND predicate = 'has_form' AND asserted_by = :test);
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
9. **Switch** the Worker's routes to the new layout and resume writes:
   `bunx wrangler versions deploy <id of the version without WRITES_PAUSED>@100%`. This one command
   is the switch: the paused version answers until it runs, and the new one, unpaused, after.
   Passkey sign-in (bound to glyphatlas.org) and a provider sign-in (its callback names
   glyphatlas.org) are checked on the site right after.

`check.py` compares the copies with the local copy of the new layout, crop by crop: numbers, row
values, bundle boxes and images, runs and the image map. It reads nothing remote.

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
  `representations`);
- the model votes in `units.visual`, and the text-id alias files;
- family heads and search names for characters that first appear after the first build;
- `has_grapheme` claims, which file a crop under a grapheme other than its character's family: the
  new `crops` has no per-crop grapheme column yet, so the read path decides how such a crop joins a
  family gallery before cutover.

The Worker read path builds on the catalogue stamps of #763 (`catalogueStamps`, `catalogueKey`), so a
reviewer's own save still shows at once.

Anything that changes in the live database after the copy reaches the new databases at cutover, from
a fresh copy (`pull.py --fresh`).
