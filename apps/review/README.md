# Character workspace

A character atlas over the local Atlas review journal.

- **Explore** shows shuffled character crops, with reading filters and kana/kanji groups.
- **Quick review** groups up to twelve crops by their proposed reading. Select mismatches, mark uncertain crops, then save the round. **Flag all** handles rounds with widespread errors.
- **Flagged** collects unresolved human decisions. Open a character to correct its reading, adjust its crop, or add a note.

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

Click a crop to mark a mismatch. **?** marks uncertainty; **↗** opens the inspector.
Unselected, loaded crops are confirmed when you choose **All match** or **Save round**.
Images that fail to load are excluded from the submission.

The twelve keyboard positions are `Q W E R T Y A S D F G H`.
Hold Shift to mark uncertainty. Enter saves the round, including after clicking a crop.

Each round is one atomic journal transaction. A conflicting edit refuses the entire round and
preserves the browser's choices. Retries use the same submission ID. **Undo last round** remains
available after reloading and refuses to overwrite intervening edits.

Imported model rejections remain pending. Human mismatches and uncertainty appear in **Flagged**.
Counts describe available cached crops and editorial decisions; they are not an accuracy estimate.

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
undo, conflicting edits, failed images and mobile layouts. Backend tests cover the URL-keyed image
cache used by the real import, atomic submissions and immutable export snapshots.
