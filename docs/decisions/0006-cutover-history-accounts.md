# 0006 Cutover: review history, claims and accounts

Decision record 0005 moves the crops to `glyph-atlas-v3`. This record moves everything else that names
a crop, and the accounts, so the old `glyph-atlas` database can be deleted.

## What there is

Copied from the live database on 19–20 October 2026:

| Table | Rows | Names a crop by |
|---|---|---|
| `events` (all reviews; no undo has been made) | 1,690 | `target` text id; ids and boxes inside JSON |
| `submissions` (1 undone) | 1,628 | text ids inside `request` and `response` JSON |
| `seen`, `skips` | 8,145, 19 | `target` text id, box and image checksum |
| `actors` | 14 | — (actor text id to user id) |
| `favourites` | 1 | `unit` text id |
| `assertions`, `assertion_actions`, `assertion_evidence` | 14,172, 33, 14,140 | 14,095 `date_*` claims on documents (2,097 on books of the new layout, 11,998 on documents that are not), 45 `has_form` claims on crops, 32 `represented_by` |
| `current_claims` | 9,467 | 43 crop subjects with a `crop_version` string |
| `ledger_submissions`, `form_decisions`, `forms` | 45, 669, 32 | crop ids inside JSON |
| `crop_versions` | 532,617 | one row per crop version; 496 crops have an earlier box, none more than one |
| `unit_suspects`, `unit_shapes` | 108,680, 15,573 | text id; 25,411 and 2,959 name crops that no longer exist |
| `unit_marks` | 8,143 | text id; every one is `seen` |
| `user`, `session`, `account`, `passkey`, `verification`, `account_profiles`, `rateLimit` | small | — |

`rejections`, `form_marks`, `corpus_follow` and `assertion_premises` are empty. `written_forms` holds 2
archival rows.

## The decision

### What the published crops already hold

A crop's label, state, style, withheld flag and revision in `glyph-atlas-v3` come from the old
database's `units` row, which every review has already written to. The cutover's fresh copy carries
them, and `check.py` compares every crop's columns and revision with it. The moved events are history:
nothing replays them, and their `gen` is 0 because the first publication's answers, counted from
those crops, already include them.

### Identity

- **History never follows a redirect.** A history row (event, submission, seen mark, skip, favourite,
  claim, form decision) names the crop it was made on, resolved through the publisher's mirror
  (`out/mirror.sqlite`, text id → integer id). The 56 retired crops kept for their history already
  have numbers (0005 publisher). A history row naming a crop the mirror lacks (today one event, on the
  review overlay of a corpus glyph no longer in the corpus) gets a tombstone: a number and a retired
  bundle entry showing the crop as that review left it. The translation stops until the publisher has
  numbered it, so the event cannot be left behind.
- **Old URLs follow redirects.** Every text id known at the cutover (the mirror's and the sources of
  `unit_redirects`) resolves through alias shards in R2, `v3/aliases/<first three hex digits of its
  SHA-256>.json.gz`, written once, with chains resolved and cycles refused. The set is closed: crops
  published after the cutover are linked by their public id. An old crop URL answers 302 until the
  cutover is confirmed, 301 after.
- **Script facts about crops that no longer exist are dropped** with a count: 25,411 suspect marks and
  2,959 shape orders. The classifier and shape exports derive them again from live crops.

### Cuts

Each crop's version at the first build is cut 0. Its earlier box, where there is one (496 crops,
never more than one; 35 more changed only their image encoding and stay cut 0), is cut -1, kept in the crop's bundle entry under `cuts` with its box, pixel size
and image key, so a citation of it still resolves to its image. A `seen` or `skip` row maps to the
cut whose box it names, as the old site decided whether a seen mark or skip still held by the box
alone (37 rows name a crop's box with an older image checksum, from a re-encoded image); crop evidence
maps by its whole `id@pixels@box`. A box no recorded version has comes from before `crop_versions`
kept them (one seen mark today) and takes the cut below the oldest recorded one.
A claim on cut -1 is not live, as the old resolver treated a claim on a crop's earlier version.

A claim rests on the crop's effective cut: its published cut, or the next one while a redrawn box
waits in `reviews`, as the old site rewrote a crop's record at once when its box was redrawn. Claims
on the cut the redraw replaces stop holding at once, as the old `current_claim_recut` trigger dropped
them; claims made while the redraw waits hold through the publication, which gives the crop that
cut; an undone redraw returns the crop to its published cut and the claims made on it. Two edges
follow from naming versions by cut:

- A crop cut back to an earlier box gets a new cut. Claims made on that box before do not return.
- A change of image encoding alone keeps the cut, so the claims on it stay live.

### Tables in `glyph-atlas-v3`

Integer crop ids are columns, never strings in JSON, so the database can join on them. Public ids are
written only at the URL boundary.

- `actors(id INTEGER PRIMARY KEY, key TEXT UNIQUE, user TEXT, via, at)`: `key` is the old actor text,
  `user` the accounts database's user id.
- `submissions(id INTEGER PRIMARY KEY, key TEXT UNIQUE, actor, request, response, at, undone)`:
  `key` keeps the client's `actor:uuid`; `request` and `response` stay as sent, the audit of what the
  client asked and got. No reader takes a crop id from them.
- `events`: the 0005 table. Each old event becomes one row, its `revision` the per-crop sequence by
  time, checked to end at or below the crop's current revision, with the verdict, issue, label, state
  and box it set, and the characters the reviewer typed (`text`); a review sets no style or withheld
  flag. Whether it came from a round, a batch or a single review is its submission's.
- `seen(submission, crop, cut, at)` and `skips(crop, actor, cut, at, submission)`, so an undone round
  stops counting its skips.
- The kinds of event (verdicts and marks), issues and the two mark states are numbered once, in the
  migration, so rehearsal and cutover give the same codes. `actor_stats` starts from the moved history.
- **Marks are states.** The old site shows a pending crop with a mark as `seen` or `hard` (its effective
  state), and the mark takes it out of the review queue. In `glyph-atlas-v3` the mark is the crop's
  state, so `crop_pending` leaves it out by its own condition. Setting a mark is a review-owned write
  like any other: it moves the crop's revision, so a later publish with the older revision cannot undo
  it. At the cutover each live mark becomes the crop's state, its revision one past the live one, with
  an event recording it. The marks are recomputed from `seen` and `skips` by the old triggers' rules
  (current box, submission not undone, two or more skippers for `hard`) and compared with every live
  `unit_marks` row; where they differ, the live mark is moved.
- `favourites(user, crop, at)`.
- The claim ledger keeps its tables. A claim on a crop gains `crop INTEGER` and `cut INTEGER` beside its
  `subject`; crop evidence becomes `(crop, cut)`. `current_claims` is translated row for row, then the
  resolver runs over the moved ledger and must produce the same rows. `rejections`,
  `assertion_premises` and `corpus_follow` are created empty for the features that write them.
  `form_marks`, which no part of the site reads, is not moved.
- `form_decisions` gains `crops` (a JSON list of integers) beside its `units`. A decision lists its
  cluster's glyphs as they were when it was made; `units` keeps that list, and `crops` names the ones
  that still exist. 315 decisions name 9,537 crops since renumbered or deleted, which `crops` leaves
  out (no redirect is followed).
- `suspects(crop INTEGER PRIMARY KEY, p, reads_as, label, box)` and `shapes(crop INTEGER PRIMARY KEY,
  shape_order)`: the quiz selects and orders crops by them in SQL, so they stay tables, written by the
  publisher from catalogue tables of the same names.

### Accounts

Accounts move to a new database, `glyph-atlas-accounts`, bound as `ACCOUNTS`: every table the
authentication library owns, schema and rows, copied from `sqlite_master` rather than a list, with the
same secrets and cookie settings so sessions carry over. Nothing a review writes is in it: a request
reads (and may refresh) its session there, then writes the review to `glyph-atlas-v3` in one batch.
Review rate limits stay on the Workers rate-limiting bindings. Deleting an account removes its rows in
`ACCOUNTS`, then its favourites and actor links in `glyph-atlas-v3`, as a retryable job; `check.py`
reports any `actors.user` or `favourites.user` with no account.

## Order

Before the freeze, the whole move runs as a rehearsal on a fresh snapshot in a Codespace, next to the
data, and produces a per-table report; every unresolved row is fixed or given a rule there, so the
freeze contains no decisions. Each step is resumable and records checksums.

1. **Freeze every writer:** review, claim, favourite and form-decision writes, sign-in, sign-up, passkey
   and session changes answer 503 with a message the client shows; publishing to the old database
   stops; in-flight requests drain. A sign-out expires the browser's cookies and leaves the session
   row valid until it expires or is revoked from another device after writes resume.
2. **Archive** the old database's tables verbatim, from the fresh copies, to a private R2 location with
   checksums. D1 Time Travel keeps a restore point as well.
3. **Copy** the live tables (from the Codespace) and publish the crops into `glyph-atlas-v3`; publish
   the answers.
4. **Translate and load** actors, submissions, events, seen, skips, favourites, the ledger, form
   decisions, suspects and shapes; write the tombstones, cuts and alias shards.
5. **Copy the accounts last**, then check: every crop check of `check.py`; every history row
   translated or accounted for, compared in full (all events, seen marks, skips, claims, current
   claims, form decisions); recomputed marks equal the live ones; every session row present. The site
   checks write (an existing session, renewal, logout, a history page, favourites, a claim, an old
   URL), so they run on this machine: `wrangler dev` runs the new Worker locally with remote bindings
   (`"remote": true` on each D1 and R2 binding) to the real new databases and bucket, with a test
   account made before the freeze. Nothing answers on a public address, and the deployed Worker stays
   paused, so every row written in the window is the checks'; they are listed and deleted after
   (`scripts/atlas3/README.md`, step 9). Version URLs stay off: turned on, they would also open older
   versions, bound to the old database and not paused. The checked Worker is then uploaded with
   `wrangler versions upload`, which keeps `WRITES_PAUSED`, and `wrangler versions secret delete
   WRITES_PAUSED` makes a further version without it; neither deploys.
6. **Switch** the Worker and the publisher to the new layout and resume writes:
   `wrangler versions deploy <id of the version without WRITES_PAUSED>@100%` is the switch, and the
   paused version answers until it runs. Passkey and provider sign-ins, both bound to glyphatlas.org,
   are checked right after. A browser tab from before the switch that sends an old text id gets 409
   and a prompt to reload.

Until writes resume, going back is redeploying the old Worker and lifting the freeze. After that,
fixes go forward. The old database stays, read-only, with its archive and restore point, for 30 days
of normal use, then is deleted.

## Consequences

- One database holds crops and everything written about them, so a review stays one batch.
- Personal data sits apart in a database of a few megabytes; account deletion spans two databases and
  runs as a retryable job.
- `unit_marks`, `crop_versions` and the `units`-keyed triggers are gone; their information lives in
  the crops' states, the bundles' `cuts` and the publisher.
