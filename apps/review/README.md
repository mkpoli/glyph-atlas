# The review interface

The browser interface of `kuzushiji-atlas`: pages, lines and units of one dataset, served by the
review service of T40 (`src/kuzushiji_atlas/review/server.py`). A reviewer opens a line from the
queue, reads the crop beside the transcription, and records decisions as review events that the
service can replay.

```sh
bun install                        # once, in this directory
bun run dev                        # development: Vite on :5173, API proxied to :8770
bun run build                      # production build into apps/review/dist
bun run check                      # the headless check against a live service
bun run browser-check              # the same, driven through a real Chromium
bun run shots                      # regenerate apps/review/shots
```

In this environment the development command is `devrun bun run dev` (from `apps/review`), which runs
Vite inside a memory-capped cgroup; `ATLAS_REVIEW_API=http://127.0.0.1:<port>` points the dev proxy at
a service on another port. The two check tools need Chromium: they take the one in
`~/.cache/ms-playwright`, or `--chrome /path/to/chrome`.

`atlas review serve <dataset> --port 8770` serves the API and, once `bun run build` has run, this
interface at `/`: `server.create_app` mounts `apps/review/dist` last, so `/documents`, `/pages`,
`/lines`, `/units`, `/images`, `/queue` and `/reviews` keep their own routes and everything else
answers with the built `index.html`.

## Where the build goes

The task card says the build lands in `src/kuzushiji_atlas/review/static/`. This application keeps
its sources under `apps/review/`, as the same card's first sentence says, and builds into
`apps/review/dist/`; a build product inside `src/` is what the repository's `.gitignore` (`dist/`)
and the packaging config would fight over, and `dist/` beside the sources is what Vite expects. The
server mounts that directory. Both paths are one line apart if the card's wording is preferred:

```js
// apps/review/vite.config.js
build: { outDir: 'dist' }          // -> apps/review/dist
```

## Stack and layout

Svelte 5 with runes, built by Vite with `bun`. No runtime dependency: the app is one JS file and one
CSS file.

```
apps/review/
  index.html            vite.config.js     svelte.config.js     package.json
  src/
    main.js             app.css            the design tokens and every rule of the three views
    App.svelte                             the shell: routes, the key handler, the dialogs
    lib/api.js                             the T40 endpoints, one function each
    lib/session.svelte.js                  state: queue, page, line, selection, undo, conflicts
    lib/geometry.js                        boxes, drag arithmetic, reading order, 割書 columns
    views/QueueView.svelte                 the queue and progress per document
    views/PageView.svelte                  the page image, the line boxes, drawing a missed line
    views/LineView.svelte                  the crop, the unit boxes, the transcription, the actions
    components/Crop.svelte                 one region of the page image at a scale, with overlays
    components/CandidatePicker.svelte      the 字母 list with reference glyphs
    components/ConflictDialog.svelte       the 409 dialog
    components/EventLog.svelte             this client's events and the undo button
    components/Help.svelte                 the keys
  tools/                 fixture.py, harness.mjs, check.mjs, browser.mjs, browser-check.mjs, shots.mjs
  shots/                 the screenshots below
```

The palette, the type stacks and the radii are taken from `packages/ui/tokens.css` of
[mkpoli/honkoku-client](https://github.com/mkpoli/honkoku-client) (MIT), which the card names as the
reference for this kind of tool. `data-theme` on `<html>` follows the system until the top bar's
button picks light or dark; the screenshots use Chromium's `prefers-color-scheme` emulation instead,
so the system path is what is pictured.

## The views

**Queue** (`#/queue`) — the lines the server hands out (`unreviewed`, `disagreement`, `random`, per
document, paginated), and progress per document from `GET /documents`: pages, lines, units and the
units a reviewer has touched. Opening a line posts a `timing` event and leaves the queue.

**Page** (`#/page/<page id>`) — the page image with a box per line, numbered with its `seq` and
faint when the role is not `main`. A click opens the line view; `l` or *new line* starts a drag that
`POST /lines` records as a line the detector missed, which is then opened for review. The panel on
the right lists the lines of the page with their unit counts and revisions.

**Line** (`#/line/<line id>`) — the crop at readable size with the unit boxes drawn; a neighbouring
line on each side, faint, on the side it occupies on the page (the next line to the left for
vertical text, below for horizontal text); the transcription beside the crop, set vertically with
`writing-mode: vertical-rl`, every character linked to its unit — hovering or selecting either one
marks the other. The inspector lists the units with their reading, code point, 字母, review state and
revision, and shows the last warnings the server returned.

## Keys and actions

| Key | Action | What is recorded |
| --- | --- | --- |
| `a` | accept the selection | `review = reviewed` |
| `x` | reject the selection | `review = rejected` |
| drag | move a unit; drag a corner to resize | `box`, from the revision the view shows |
| `s` | split the focused unit at the pointer | `field = "segmentation"`, `new = {"split": [...]}` |
| `m` | merge the selection | `field = "segmentation"`, `new = {"merge": [ids]}` |
| `c` | create a unit for a character with no box, then draw it | `POST /units` |
| `l` | page view: draw a line the detector missed | `POST /lines` |
| `r` | set the reading of the focused unit | `reading` |
| `j` | choose the 字母 of the focused unit | `unicode`, `jibo`, `script`, `classification`, in that order |
| `g` | mark the selection an unresolved group | `group_id` (one id for the selection) and `granularity = sequence` |
| `n` | note on the focused unit, or on the line | `note` (recorded, changes no state) |
| `z` | undo this client's last event | a compensating review, see below |
| `space` | the next line of the queue | closes the line (`timing`) and opens the next |
| `←↑→↓` | move the selection; `shift` extends it | |
| `enter` | open the focused line | |
| `escape` | cancel the mode, then clear the selection | |
| `?` | the list of keys | |

The selection is one unit, or a range: click, `shift`-click, or `shift`-arrow. `m`, `g` and the
batch actions of `a`/`x` work on the whole range.

`j` posts the chain one review at a time, each from the revision the last one left, because the
service answers 409 to a review made from a stale revision. It records `unicode` (the code point),
`jibo` (字母), `script` (hentaigana when the candidate has a 字母) and `classification = identified`.
The list is `GET /units/{id}/candidates`: the ordinary kana of the reading first, then every
hentaigana with that 音価, each with its NINJAL reference glyph image, its MJ figure and the T31 score
where the unit carries one.

## Timing

A line posts `field = "timing"` twice: on open (`{"action": "open", "opened_ms": …}`) and on leave
(`{"action": "leave", "closed_ms": …, "dwell_ms": …, "units": n}`), which is what the calibration
protocol of T24/T25 times pages and reviewers with. A page view posts the same pair on
`target_type = "page"` when it is opened and left. Timing events are recorded but never offered for
undo, since nothing about them can be compensated.

## Conflicts

Every review carries the revision the view loaded. A `base_revision` the server has moved past, or a
review of a unit a split or merge retired, answers **409** with `{"error", "revision",
"base_revision", "state"}`. The interface keeps the refused request and shows the server's current
state field by field next to the change that was asked for, then offers:

* **reapply from revision N** — the same change, sent again from the revision the server reported;
* **discard and show the server's state** — the view reloads the line or the page.

## Undo

`z` walks this client's own stack (kept in `localStorage`, so a reload does not lose it) and posts a
*compensating review* for the last event: the same field with the event's `old` value, and
`evidence = "undo of rv…"`. A created unit or line is compensated by `active = false` and
`review = rejected`.

A split or a merge can only be compensated in part, and the interface says so: the outputs are
retired, but the **inputs stay retired**, because the service refuses any review of a retired unit —
including one that would set `active` back to `true`. Re-drawing the inputs with `c` is what a
reviewer does; `atlas review replay` is what repairs a log. This is a property of the T40 store, not
of this interface, and `tools/check.mjs` asserts both halves of it.

## Edge cases

* **A page whose image is not cached** — `GET /pages/{id}` answers with the upstream URL when
  `sha256` is null. The page view shows a banner with the URL and a *skip this page* button, and if
  the image does not load either, a panel with the URL, a *skip* button and a way back to the queue.
  Skipping opens the next line of the queue whose page is not the skipped one.
* **割書 lines with two columns** — `lib/geometry.js` groups the units of a line by their run across
  the line (down for vertical text, across for horizontal text) and renders one transcription column
  per run, the rightmost first. A line whose units form one run renders one column, so the same code
  serves both.
* **Very long lines** — above 120 active units the line view virtualises: only the boxes in the
  scrolled window are in the DOM, the transcription shows the characters of that window, and the
  panel sticks to the top of the reader, with a line saying which window is drawn. The fixture's
  240-unit line is the case the check asserts.

## Tests and screenshots

`tools/fixture.py` writes the fixture dataset: `documents`, `pages`, `lines` and `units` tables and a
synthetic page image in the image cache, with a plain page, a 割書 page, a 240-unit line and two
pages whose images are deliberately not cached.

`tools/check.mjs` (`bun run check`) builds that dataset, starts `atlas review serve` over it, and
drives the endpoints `src/lib/api.js` calls: the mount of the built interface, the documents, the
queue, a page and its lines, a line and its units, the candidates; then opening a line (`timing`),
accepting, moving a box, splitting, choosing a 字母, undoing a field change, merging, undoing a
merge, creating a unit and a line, a stale `base_revision` (409) and a retired unit (409), a repeated
`idempotency_key`, and leaving the line. It then opens `<directory>/review.sqlite` and asserts the
events the service recorded — their order, their ids, their fields, their payloads, their actor, and
that `atlas review apply` writes the same number of events to `reviews.jsonl`. It exits non-zero on
the first failing check.

**What `check.mjs` does not cover**: it never loads a page, so rendering, hit-testing, pointer drag
and resize, the key handler, the routes and the screenshots are outside it. Playwright is not
installed: its browsers are already in `~/.cache/ms-playwright`, but the npm package is a heavy
download for a task that only needs the same protocol. `tools/browser.mjs` is a small DevTools
client instead (a WebSocket and the JSON messages), and `tools/browser-check.mjs`
(`bun run browser-check`) drives the built interface in that Chromium: it opens a line from the
queue, clicks a character and checks the unit box follows, accepts with `a`, drags a box, splits with
`s`, lists and picks a 字母 with reference glyphs, presses `z`, moves on with `space`, scrolls the
240-unit line, draws a missed line on the page, opens a page whose image is not cached, and fails if
the browser console reports anything. Each step is checked against the DOM *and* against the events
in `review.sqlite`. That is what a Playwright script would have covered.

`tools/shots.mjs` (`bun run shots`) takes the screenshots in the same browser, at 1440×900 and at
400×820, in light and dark: the queue, the page view, the line view, the uncached page, the 割書
line, the 240-unit line (top and scrolled), a range selection, the 字母 picker and the 409 dialog.
`apps/review/shots/*.png` are committed; regenerate them after a change to the interface.

The screenshots of `bun run shots` are the interface as built; a screenshot of a *change* is only
worth reviewing next to the command that produced it:

```sh
bun run build && bun run browser-check && bun run shots
```

## Notes for the next card

* `atlas review apply <dir>` writes the reviewed state back to the tables; the interface only writes
  events, so a review session ends with that command.
* The interface does not read `groups.parquet`; `g` records `group_id` and `granularity` on the units
  themselves, which is what a single review event can change.
* Nothing here reads another client's events: the service has no endpoint for the log, so the
  event panel shows what this client posted, and the recorded log is checked from SQLite in the
  tools.
