#!/usr/bin/env bun
/**
 * The screenshots of the interface, taken in a real Chromium over the DevTools protocol.
 *
 *     bun apps/review/tools/shots.mjs [--directory DIR] [--port N] [--server URL]
 *
 * Every shot is written to `apps/review/shots/<name>.png`: the queue, the page view and the line
 * view at 1440 px and at 400 px, in light and dark, plus the 割書 line with two columns, the very
 * long line whose boxes are virtualised, the 字母 picker and the 409 dialog. The dataset is the
 * fixture of `tools/fixture.py`, so a shot is reproducible: rebuild, rerun, compare.
 */

import { mkdirSync, rmSync, statSync } from 'node:fs'
import { join } from 'node:path'
import Browser from './browser.mjs'
import { boot, HERE, options } from './harness.mjs'

const config = options()
const SHOTS = join(HERE, '..', 'shots')
const DESKTOP = { width: 1440, height: 900 }
const NARROW = { width: 400, height: 820 }

const service = await boot(config)
const base = service.base
const fixture = service.fixture ?? {
  line: 'doc-1:p1:line0',
  cached_page: 'doc-1:p1',
  uncached_page: 'doc-1:p2',
  warigaki: 'doc-1:p3:line0',
  long_line: 'doc-1:p3:line1',
}

mkdirSync(SHOTS, { recursive: true })
for (const stale of new Bun.Glob('*.png').scanSync(SHOTS)) rmSync(join(SHOTS, stale), { force: true })

const browser = await Browser.launch(DESKTOP)
const url = (hash) => `${base}/${hash}`
const lineHash = (id) => `#/line/${encodeURIComponent(id)}`
const pageHash = (id) => `#/page/${encodeURIComponent(id)}`

const LINE_READY = 'document.querySelectorAll(".strip .crop .box").length > 0'

/** Each shot: viewport, colour scheme, route, and what to do before the shutter. */
const shots = [
  {
    name: 'queue-desktop-light',
    viewport: DESKTOP,
    scheme: 'light',
    hash: '#/queue',
    waitFor: 'document.querySelectorAll("tr.queue-row").length > 0',
  },
  { name: 'queue-desktop-dark', viewport: DESKTOP, scheme: 'dark', hash: '#/queue', waitFor: 'document.querySelectorAll("tr.queue-row").length > 0' },
  { name: 'queue-400-light', viewport: NARROW, scheme: 'light', hash: '#/queue', waitFor: 'document.querySelectorAll("tr.queue-row").length > 0' },
  {
    name: 'page-desktop-light',
    viewport: DESKTOP,
    scheme: 'light',
    hash: pageHash(fixture.cached_page),
    waitFor: 'document.querySelectorAll(".page-canvas .crop .box").length > 0',
  },
  {
    name: 'page-desktop-dark',
    viewport: DESKTOP,
    scheme: 'dark',
    hash: pageHash(fixture.cached_page),
    waitFor: 'document.querySelectorAll(".page-canvas .crop .box").length > 0',
  },
  {
    name: 'page-400-light',
    viewport: NARROW,
    scheme: 'light',
    hash: pageHash(fixture.cached_page),
    waitFor: 'document.querySelectorAll(".page-canvas .crop .box").length > 0',
  },
  {
    name: 'page-uncached-desktop-light',
    viewport: DESKTOP,
    scheme: 'light',
    hash: pageHash(fixture.uncached_page),
    waitFor: 'document.querySelector(".unavailable, .panel.small") !== null',
  },
  { name: 'line-desktop-light', viewport: DESKTOP, scheme: 'light', hash: lineHash(fixture.line), waitFor: LINE_READY },
  { name: 'line-desktop-dark', viewport: DESKTOP, scheme: 'dark', hash: lineHash(fixture.line), waitFor: LINE_READY },
  { name: 'line-400-light', viewport: NARROW, scheme: 'light', hash: lineHash(fixture.line), waitFor: LINE_READY },
  { name: 'line-400-dark', viewport: NARROW, scheme: 'dark', hash: lineHash(fixture.line), waitFor: LINE_READY },
  {
    name: 'line-warigaki-desktop-light',
    viewport: DESKTOP,
    scheme: 'light',
    hash: lineHash(fixture.warigaki),
    waitFor: 'document.querySelectorAll(".transcription .column").length > 1',
  },
  {
    name: 'line-long-desktop-light',
    viewport: DESKTOP,
    scheme: 'light',
    hash: lineHash(fixture.long_line),
    waitFor: 'document.querySelectorAll(".strip .crop .box").length > 0',
  },
  {
    name: 'line-long-scrolled-desktop-light',
    viewport: DESKTOP,
    scheme: 'light',
    hash: lineHash(fixture.long_line),
    waitFor: 'document.querySelectorAll(".strip .crop .box").length > 0',
    async before(browser_) {
      // Scrolling a 240-unit line: only the boxes in the window are in the DOM.
      await browser_.evaluate(`(() => {
        const reader = document.querySelector('.reader')
        reader.scrollTop = reader.scrollHeight * 0.45
        return reader.scrollTop
      })()`)
      await browser_.waitFor('document.querySelector(".reader + p, .reader p") !== null')
      await Bun.sleep(200)
    },
  },
  {
    name: 'line-selection-desktop-light',
    viewport: DESKTOP,
    scheme: 'light',
    hash: lineHash(fixture.line),
    waitFor: LINE_READY,
    async before(browser_) {
      const char = await browser_.centre('.transcription .char:nth-child(2)')
      await browser_.click(char.x, char.y)
      await browser_.key('ArrowDown', { shift: true })
      await browser_.evaluate('document.querySelector(".unit-table tr.current") !== null')
    },
  },
  {
    name: 'candidates-desktop-light',
    viewport: DESKTOP,
    scheme: 'light',
    hash: lineHash(fixture.line),
    waitFor: LINE_READY,
    async before(browser_) {
      const box = await browser_.centre('.strip .crop .box:nth-of-type(1)')
      await browser_.click(box.x, box.y)
      await browser_.key('j')
      await browser_.waitFor('document.querySelectorAll(".candidates .candidate").length > 0')
    },
  },
  {
    name: 'conflict-desktop-light',
    viewport: DESKTOP,
    scheme: 'light',
    hash: lineHash(fixture.line),
    waitFor: LINE_READY,
    async before(browser_) {
      const unit = await browser_.evaluate('document.querySelector(".unit-table tr.current")?.dataset.unitId')
      const box = await browser_.centre('.strip .crop .box:nth-of-type(3)')
      await browser_.click(box.x, box.y)
      // Another client moves the unit under this one, then this client tries to review it.
      const changed = await browser_.evaluate(`fetch('/reviews', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ target_type: 'unit', target_id: ${JSON.stringify(unit)},
          field: 'reading', new: 'ぬ', base_revision: 0, client_id: 'another-reviewer',
          idempotency_key: 'conflict-shot' }),
      }).then((response) => response.status)`)
      if (changed !== 200) throw new Error(`the other client's review answered ${changed}`)
      await browser_.key('a')
      await browser_.waitFor('document.querySelector(".overlay .dialog h2")?.textContent.includes("409") === true')
    },
  },
]

let written = 0
for (const shot of shots) {
  await browser.setViewport(shot.viewport.width, shot.viewport.height)
  await browser.setColorScheme(shot.scheme)
  await browser.goto(url(shot.hash), { waitFor: shot.waitFor })
  if (shot.before) await shot.before(browser)
  await Bun.sleep(250)
  const path = join(SHOTS, `${shot.name}.png`)
  await browser.screenshot(path)
  const size = statSync(path).size
  written += 1
  console.log(`  ${shot.name.padEnd(34)} ${shot.viewport.width}×${shot.viewport.height} ${shot.scheme.padEnd(5)} ${(size / 1024).toFixed(0)} kB`)
}

await browser.close()
service.stop({ keep: config.keep })
console.log(`\n${written} screenshots in apps/review/shots`)
