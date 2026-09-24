# Character workspace

A character atlas over the local Atlas review journal.

- **Explore** shows shuffled character crops, with reading filters and kana/kanji groups.
- **Quick review** groups up to twelve crops by their proposed reading. Select mismatches, choose an illustrated error type, then save. **Select all** handles rounds with widespread errors.
- **Flagged** collects unresolved human decisions. The character reviewer advances through the visible collection after each save, preserving its order and scroll position.

The inspector shows the character and a small surrounding region. There is no book reader or page catalogue.

## Run

From the repository root:

```sh
bun install --cwd apps/review
bun run --cwd apps/review build
devrun .venv/bin/atlas review serve work/ainu-records --port 8770
```

Open <http://127.0.0.1:8770/>. Stop the command with Ctrl-C when finished.
For frontend development, Vite forwards `/atlas` to `ATLAS_REVIEW_API` (default port 8770).
Run development servers through `devrun`.

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
