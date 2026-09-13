# Review workspace

A local Svelte interface over the Atlas review service. Its paper and ink palette follows
[ainu-records](https://rec.aynu.org/).

- **Overview** shows imported volumes, mapped pages, outstanding character reviews and editorial decisions. Audited accuracy stays unmeasured until an audit is scored.
- **Sources** lists every page, including pages without text or character boxes. Filter by volume, page number or work remaining.
- **Page reader** puts the scan beside its transcription. Save corrections with evidence, leave page notes, revisit a correction or withdraw it. Unsubmitted drafts remain in the browser; submitted feedback stays in the review journal.
- **Character review** provides the existing crop, reading, 字母, split/merge and keyboard tools.

## Run

From the repository root, with Python dependencies installed:

```sh
bun install --cwd apps/review
bun run --cwd apps/review build
devrun .venv/bin/atlas review serve work/ainu-records --port 8770 --source /path/to/ainu-records
```

Open <http://127.0.0.1:8770/>. `--source` must name a current ainu-records checkout. It is optional
for local review. Bun is required for native source validation. The service serves the built files
from `apps/review/dist`.

Stop the command with Ctrl-C when finished. `devrun` contains the service in a memory-capped cgroup.
For frontend development, `ATLAS_REVIEW_API` selects the API service; run Vite through `devrun` too.

## Prepare source updates

On a page, save the correction, then choose **Prepare source update**. Atlas resolves the source
volume by its platform entry and checks the page canvas and the checksum of the reviewed text.
The connected checkout's own parser validates text, ruby and wordlist targets against existing
corrections. Changed source text, duplicate IDs and overlapping corrections prevent export.

The preview contains the current and proposed files. Download the patch and review record, then
review the readings against the scan. No source checkout is modified by the workspace.

Apply a reviewed patch in a clean source worktree:

```sh
git apply --check /path/to/ainu-records-corrections.patch
git apply /path/to/ainu-records-corrections.patch
```

Run ainu-records' relevant checks and submit the resulting source changes for PR review. The review
record includes source revision, target identities and original file checksums. Revalidate if the
source has changed. Character-box edits remain in the Atlas review journal; this source-update
export covers page transcription corrections.

## Persistence and conflicts

Corrections and page notes survive reopening, `atlas review apply` and `atlas review replay`.
Each correction records the checksum of the source text reviewed. Reimported text makes an older
correction require review again, even when its original substring still matches. Page writes and
withdrawals carry the revision last seen by the browser; a stale tab preserves its draft and shows
the conflict.

Correction undo appends a withdrawal event. The existing character split/merge undo retires the
new units; it does not restore the retired inputs.

## Verification

Run from the repository root. Browser checks use disposable datasets and the Chromium already
installed in the Playwright cache; they close their service and browser after the run.

```sh
bun run --cwd apps/review build
devrun bun apps/review/tools/check.mjs
devrun bun apps/review/tools/browser-check.mjs
devrun bun apps/review/tools/workspace-check.mjs
bun test apps/review/tools/source-patch.test.js
AINU_RECORDS_DIR=/path/to/ainu-records .venv/bin/python -m pytest tests/test_ainu_native.py -q
```

The native tests copy the source checkout's TypeScript modules into a temporary fixture. They cover
volume addressing, native ruby/wordlist rules, source identity checks, duplicate/overlapping changes
and preservation of existing records. Without Bun or `AINU_RECORDS_DIR`, these integration tests
are explicitly skipped.
