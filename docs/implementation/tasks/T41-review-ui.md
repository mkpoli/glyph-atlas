# T41 Review interface

Goal: a browser interface for reviewing lines in their page context, fast enough that a reviewer
handles a printed line in well under a minute.

Read first: T40 endpoints, `~/projects/Ainu/worktrees/ainu-records-wikisource/data/characters/README.md`
(a per-copy character review interface with form and 字母 fields; the interaction patterns carry
over), `~/projects/Philology/honkoku-client/packages/ui` (design tokens).

Stack: Svelte 5 with Bun under `apps/review/`, served by the T40 server in production and by
`devrun bun run dev` in development.

Views
- Page view: the page image with line boxes; click a line to open it.
- Line view: the line crop vertical at readable size, unit boxes drawn, the transcription beside it
  with each character linked to its unit; the neighbouring two lines faint for context.
- Actions with keys: accept (a), reject (x), move or resize a box (drag), split at a point (s),
  merge with next (m), set reading (r, text input), choose 字母 (j, a list of candidates with their
  reference glyphs and scores), mark unresolved group (g), note (n), next line (space).
- Queue view: what the server's `queue` returns, with progress per document.

Outputs: the app, screenshots in light and dark at desktop and 400 px width, `apps/review/README.md`.

Edge cases: pages whose image is not cached (show the IIIF URL and skip); 割書 lines with two
columns.

Tests: Playwright script under `apps/review/tools/` that loads a fixture page, splits a unit and
checks the posted review; visual check by screenshot.

Acceptance: a reviewer completes the calibration tranche of T24 with the timing recorded per page.

Size: large. Depends on: T40.
