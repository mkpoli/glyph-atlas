# Character workspace

A character atlas over the local Atlas review journal.

- **Explore** shows shuffled character crops, with reading filters and kana/kanji groups.
- **Quick review** groups up to twelve crops by their proposed reading. Select mismatches, choose an illustrated error type, then save. **Select all** handles rounds with widespread errors.
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

`bun run --cwd apps/cloudflare deploy` builds this app and deploys it with the Worker configuration in
`apps/cloudflare/wrangler.jsonc`.

## Review

Select any crops that do not match the target. Choose **Wrong character**, **Joined characters**,
**Cut off**, or **Not a character**. One choice applies to all selected crops.
Save the round to record those errors and confirm the remaining loaded crops.
Images that fail to load are excluded. Selections without an error type cannot be submitted.

**Skip** leaves a crop unjudged: too faint to read, or set aside for later. Nothing is written and
the crop stays pending for a later round.

OCR suggestions appear for wrong readings and joined characters. Selecting a suggestion is optional;
**None of these** leaves the issue ready to save. Joined text is evidence for later segmentation,
so reporting it never requires typing several characters into a single-character reading field.

The keyboard positions are `Q W E R T Y A S D F G H`. Use `1`–`4` for the four error types,
Enter to save, and Escape to clear the selection.

Opening a crop starts continuous review. Choose an error and **Save issue & next**, or use
**Looks right & next**. The dialog stays open on the next crop. Previous/next arrows skip without
saving. Reading input, crop adjustment and notes are under **Adjust crop or reading**.

Each round is one atomic journal transaction. A conflicting edit refuses the entire round and
preserves the browser's choices. Optional single-character corrections are part of that transaction.
Retries use the same submission ID. **Undo last round** restores readings and decisions, and remains
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

The header menu exports character review records. Each record retains the reading, crop coordinates,
image checksum and source identity seen by the reviewer. Later corrections or undo leave the
original evidence intact and mark superseded records as non-current.

Atlas detections and ainu-records use different crop identities. The export preserves the evidence
needed to map a review to the source; it does not apply a reading to a guessed source crop.
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
