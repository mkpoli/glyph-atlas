# Character workspace

A character atlas over the local Atlas review journal.

- **Explore** shows shuffled character crops, with character filters and kana/kanji groups.
- **Quick review** deals crops by grapheme, 48 to a round and 24 more on request. Select mismatches, choose an illustrated error type, then save. **Select all** handles rounds with widespread errors.
- **Flagged** collects unresolved human decisions. The character reviewer advances through the visible collection after each save, preserving its order and scroll position.
- **Pages** lists the page photos of the dataset and shows each one with its boxes. It appears only on the local review service.

The inspector shows the character and a small surrounding region.

## Run

The interface is a SvelteKit app rendered on the server. On Cloudflare it runs in the Worker with the
API in `apps/cloudflare`; locally it forwards `/atlas`, `/layers`, `/images` and `/reviews` to the review
service named by `ATLAS_REVIEW_API`.

From the repository root:

```sh
bun install --cwd apps/review
bun run --cwd apps/review build
devrun .venv/bin/atlas review serve work/ainu-records --port 8770
ATLAS_REVIEW_API=http://127.0.0.1:8770 devrun bun run --cwd apps/review preview
```

Open <http://127.0.0.1:4173/>. For frontend development, run `vite dev` with the same variable.
Run development servers through `devrun`.

Accounts are always served by the Worker over D1, also beside the local service. Put a
`BETTER_AUTH_SECRET` (`openssl rand -hex 32`) in `apps/cloudflare/.dev.vars` and apply the migrations
to the local database once: `bunx wrangler d1 migrations apply glyph-atlas --local` in `apps/cloudflare`.

## Accounts

Every write is made by a signed-in user. A browser with no session starts an anonymous one before
its first write. A browser that reviewed before accounts kept a `reviewer-…` id; its session claims
that id once, and the work saved under it becomes the user's. The first account to claim an id holds
it, and one account holds at most five. A user who gives no name is called `anon-…`, a shape no old
id had. The journal is never rewritten:
`actors` records which user each id written into it belongs to.

There are no passwords. A reader signs in with a passkey, with a six-digit code sent by mail, or with
an account elsewhere; signing in from an anonymous session brings its work along. The form marks the
way this browser signed in last, and a reader signed in by mail is offered a passkey for next time.

- **Mail.** Codes are sent through Cloudflare Email Sending from `MAIL_FROM` (`wrangler.jsonc`), whose
  domain has to be onboarded first: `bunx wrangler email sending enable glyphatlas.org`. A local
  Worker writes each mail under `.wrangler/tmp/email/`.
- **Other accounts.** GitHub, Google, Discord, LINE and Kakao are offered once their app's id and
  secret are set as Worker secrets (`GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, and so on). Each app's
  callback is `https://glyphatlas.org/api/auth/callback/<provider>`, as `…/callback/github`.
- **Admins** see a page at `/admin` that lists reviewers and their submissions. Rejecting one undoes
  it as its author's own undo would, recorded with the admin and a reason in `rejections`; rejecting
  all of a reviewer's work also passes over their own later changes to a crop, and leaves any crop
  someone else has changed since, or that one of their own submissions still standing has changed.
  **Old ids** lists the old reviewer ids nobody holds yet. An admin can ban a user and make another user an admin. The first
  admin is made in D1:
  `bunx wrangler d1 execute glyph-atlas --remote --command "UPDATE \"user\" SET role='admin' WHERE email='…'"`.
- **Passkeys** are bound to the host the page is served from, so a local check uses `localhost`
  rather than `127.0.0.1`.

`bun run --cwd apps/cloudflare deploy` builds this app and deploys it with the Worker configuration in
`apps/cloudflare/wrangler.jsonc`.

## Review

Select any crops that do not match the target. Choose **Wrong character**, **Joined characters**,
**Cut off**, or **Not a character**. One choice applies to all selected crops.
Save the round to record those errors and confirm the remaining loaded crops.
Images that fail to load are excluded. Selections without an error type cannot be submitted.

**Skip** leaves a crop unjudged: too faint to read, or set aside for later. Nothing is written and
the crop stays pending for a later round.

OCR suggestions appear for a wrong character or joined characters. Selecting a suggestion is optional;
**None of these** leaves the issue ready to save. Typed text is accepted only for joined characters, as
evidence for later segmentation.

The keyboard positions are `Q W E R T Y A S D F G H`. Use `1`–`4` for the four error types,
Enter to save, and Escape to clear the selection.

Opening a crop from a gallery shows it in the inspector. Choose an error and **Save problem**, or use
**Looks right**; saving closes the inspector and returns to the gallery, where the tile shows its
verdict. The previous/next arrows and Skip move between crops without saving. Character input, crop
adjustment and notes are under **Adjust character or crop**.

Each round is one atomic journal transaction. A conflicting edit refuses the entire round and
preserves the browser's choices. Optional single-character corrections are part of that transaction.
Retries use the same submission ID. **Undo last round** restores characters and decisions, and remains
available after reloading and refuses to overwrite intervening edits.

Imported model rejections remain pending. Human mismatches and uncertainty appear in **Flagged**.
Counts describe available cached crops and editorial decisions; they are not an accuracy estimate.

## Drawing boxes

Pages without units, such as photographs whose interlinear marks were never boxed, are boxed by hand.
Open a page under **Pages**, turn on **Draw** (or press `D`) and drag across a mark. Scroll to zoom,
drag to move, `+`/`-` to zoom and `0` to fit the page. Boxes are stored in page pixels.

The box is saved as soon as it is drawn, as a `manual` unit that is not yet identified. The dialog that
opens then takes its character: search as in the collection, choose, and save. The character goes
through the character layer like any identity correction, so its script follows the character. **Leave
unidentified** keeps the box without one; open it again from the photo or the list beside it later.
**Remove box** retires a drawn box. It stays in the journal with `active` false.

Every drawn box goes on one line per page: role `other`, `meta.scope` `page`, the whole page as its box.
The first box on a page creates it. A drawn box never joins a line an alignment found, since an
interlinear mark is not part of that line's text. `atlas review apply` writes the line and the units to
`lines.parquet` and `units.parquet`. An unidentified box is not listed in Explore, which shows
occurrences of a character.

## OCR setup

See [Review OCR suggestions](../../models/review-ocr/README.md) for model provenance and runtime limits.

```sh
python scripts/fetch_review_ocr.py
```

The server loads the local NDLkotenOCR recognizer and Atlas classifier on demand, preferring CUDA.
Review works when the models are absent or unavailable. No suggestion is accepted automatically.

## Export

The header menu exports character review records. Each record retains the character, crop coordinates,
image checksum and source identity seen by the reviewer. Later corrections or undo leave the
original evidence intact and mark superseded records as non-current.

Atlas detections and ainu-records use different crop identities. The export preserves the evidence
needed to map a review to the source; it does not apply a character to a guessed source crop.
The original page-correction API remains available for existing journal records, independently of
this character interface.

## Verification

```sh
bun run --cwd apps/review build
devrun bun apps/review/tools/browser-check.mjs
.venv/bin/python -m pytest tests/test_review_atlas.py -q
```

Browser checks use disposable data. They cover the crop grid, inspector, review rounds, reload,
undo, optional OCR choices, error types, continuous next navigation, conflicting edits, failed images and mobile layouts. Backend tests cover the URL-keyed image
cache used by the real import, atomic submissions and immutable export snapshots.
