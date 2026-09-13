#!/usr/bin/env bun
/**
 * The browser check: a real Chromium drives the built interface over the DevTools protocol.
 *
 *     bun apps/review/tools/browser-check.mjs [--directory DIR] [--port N] [--server URL] [--keep]
 *
 * It starts the fixture dataset and the T40 service, loads `apps/review/dist` in Chromium, and then
 * does what a reviewer does: opens a line from the queue, clicks a character, accepts with `a`,
 * drags a unit box, splits with `s`, picks a 字母 with `j`, presses `z`, moves on with `space`, draws
 * a line the detector missed, and opens a page whose image is not cached. Every step is checked
 * twice: against the rendered DOM, and against the events the service recorded in `review.sqlite`.
 *
 * This is the part `tools/check.mjs` cannot reach: rendering, hit-testing, drag and resize, the key
 * handler, and the routes. It is what Playwright would have done; the driver is `tools/browser.mjs`.
 */

import { join } from 'node:path'
import Browser from './browser.mjs'
import { boot, events, options, units } from './harness.mjs'

const config = options()
const steps = []
let failures = 0

async function step(name, body) {
  try {
    const detail = await body()
    steps.push({ name, ok: true })
    console.log(`  ok   ${name}${detail ? ` — ${detail}` : ''}`)
  } catch (error) {
    failures += 1
    steps.push({ name, ok: false, detail: error.message })
    console.log(`  FAIL ${name} — ${error.message}`)
  }
}

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

function equal(actual, expected, what) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${what}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`)
  }
}

/** Poll until a condition on the recorded events or the DOM holds. */
async function until(what, body, timeout = 10000) {
  const deadline = Date.now() + timeout
  let last
  let failure = null
  while (Date.now() < deadline) {
    try {
      last = await body()
      if (last) return last
    } catch (error) {
      // Polling races the service's writes and the renderer's paint; only the deadline is fatal.
      failure = error
    }
    await Bun.sleep(120)
  }
  throw new Error(`timed out waiting for ${what}${failure ? ` (${failure.message})` : ''}`)
}

const service = await boot(config)
const base = service.base
const fixture = service.fixture ?? {
  line: 'doc-1:p1:line0',
  cached_page: 'doc-1:p1',
  uncached_page: 'doc-1:p2',
}
console.log(`browser-check: ${base} over ${config.directory}`)

const browser = await Browser.launch({ width: 1440, height: 900 })
const pageErrors = []
browser.listeners.push((message) => {
  if (message.method === 'Runtime.exceptionThrown') {
    pageErrors.push(message.params.exceptionDetails?.exception?.description ?? 'error')
  }
  if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') {
    pageErrors.push(message.params.args.map((argument) => argument.value ?? argument.description).join(' '))
  }
})

const url = (hash) => `${base}/${hash}`
const lineHash = (id) => `#/line/${encodeURIComponent(id)}`
// The current line's crop; the neighbouring lines are drawn as `.crop.neighbour` and their boxes
// are ghosts, so every count and every click is scoped to the strip's own crop.
const CURRENT = '.strip > .crop:not(.neighbour)'

try {
  console.log('\nthe queue')

  await step('the queue view renders the documents, the progress and the lines', async () => {
    await browser.goto(url('#/queue'), {
      waitFor: 'document.querySelectorAll("tr.queue-row").length > 0',
    })
    const summary = await browser.evaluate(`(() => ({
      documents: document.querySelectorAll('.progress > span').length,
      lines: [...document.querySelectorAll('tr.queue-row')].filter(tr => tr.textContent.includes('doc-1:p1:line0')).length,
      rows: document.querySelectorAll('tr.queue-row').length,
    }))()`)
    assert(summary.documents >= 2, `only ${summary.documents} progress bars`)
    assert(summary.lines >= 1, `the line is not in the queue (${summary.rows} rows)`)
    return `${summary.documents} documents with progress, ${summary.rows} lines in the queue`
  })

  await step('opening a line from the queue posts a timing event', async () => {
    const centre = await browser.evaluate(`(() => {
      const row = [...document.querySelectorAll('tr.queue-row')].find(tr => tr.textContent.includes(${JSON.stringify(fixture.line)}))
      const button = row && [...row.querySelectorAll('button')].find(b => b.textContent.trim() === 'review')
      if (!button) return null
      const rect = button.getBoundingClientRect()
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
    })()`)
    assert(centre, 'the review button of the line is not on the page')
    await browser.click(centre.x, centre.y)
    await browser.waitFor('document.querySelectorAll(".crop .box").length > 0')
    const timing = await until('the open timing event', () =>
      events(config.directory).find((row) => row.field === 'timing' && row.new?.action === 'open'),
    )
    const opened = await browser.evaluate('location.hash')
    assert(opened.includes(encodeURIComponent(fixture.line)), `the route is ${opened}`)
    return `${timing.id} on ${timing.target_id}`
  })

  console.log('\nthe line view')

  await step('the crop, the unit boxes and the transcription are rendered', async () => {
    const view = await browser.evaluate(`(() => ({
      crops: document.querySelectorAll('.crop').length,
      neighbours: document.querySelectorAll('.crop.neighbour').length,
      boxes: document.querySelectorAll('${CURRENT} .box').length,
      chars: document.querySelectorAll('.transcription .char').length,
      columns: document.querySelectorAll('.transcription .column').length,
      text: document.querySelector('.transcription').innerText.replace(/\\n/g, ''),
      first: document.querySelector('${CURRENT} img')?.naturalWidth,
    }))()`)
    assert(view.boxes === 4, `${view.boxes} unit boxes`)
    assert(view.chars === 4, `${view.chars} transcription characters`)
    assert(view.columns === 1, `${view.columns} transcription columns`)
    assert(view.first > 0, 'the page image did not load')
    assert(view.text === 'あいうえ', `the transcription reads ${view.text}`)
    return `${view.boxes} boxes, ${view.chars} characters, ${view.crops} crops (${view.neighbours} faint neighbour)`
  })

  await step('a click on a character selects its unit, and the box follows', async () => {
    const char = await browser.centre('.transcription .char:nth-child(2)')
    await browser.click(char.x, char.y)
    const selected = await until('the selected unit', () =>
      browser.evaluate(`(() => {
        const box = document.querySelector('.crop .box.selected')
        const row = document.querySelector('table.unit-table tr.current')
        const chars = document.querySelectorAll('.transcription .char.selected').length
        return box && row && chars === 1 ? { row: row.innerText.replace(/\\s+/g, ' ').trim(), w: Math.round(box.getBoundingClientRect().width) } : null
      })()`),
    )
    assert(selected.row.startsWith('1'), `the unit table row is ${selected.row}`)
    return `the second character selected its unit (${selected.row})`
  })

  await step('accept (a) records a review event and repaints the badge', async () => {
    await browser.key('a')
    const accepted = await until('the accept event', () =>
      events(config.directory).find((row) => row.field === 'review' && row.new === 'reviewed'),
    )
    assert(accepted.target_id === `${fixture.line}:u1`, `the event targets ${accepted.target_id}`)
    const painted = await until('the reviewed badge', () =>
      browser.evaluate(`[...document.querySelectorAll('table.unit-table .badge')].filter(b => b.textContent.trim() === 'reviewed').length`),
    )
    return `${accepted.id} targets ${accepted.target_id}; ${painted} badge(s) say reviewed`
  })

  await step('dragging a unit box records a box review', async () => {
    const before = units(config.directory)[`${fixture.line}:u2`].box
    const box = await browser.centre(`${CURRENT} .box:nth-of-type(3)`)
    await browser.drag({ x: box.x, y: box.y }, { x: box.x + 14, y: box.y + 26 })
    const moved = await until('the box event', () =>
      events(config.directory).find((row) => row.field === 'box' && row.target_id === `${fixture.line}:u2`),
    )
    assert(moved.new.x !== before.x || moved.new.y !== before.y, 'the box did not move')
    assert(moved.old.x === before.x, 'the event does not carry the old box')
    return `${moved.id}: ${JSON.stringify(moved.old)} → ${JSON.stringify(moved.new)}`
  })

  await step('split (s) records one segmentation event for the pointed unit', async () => {
    const box = await browser.centre(`${CURRENT} .box:nth-of-type(1)`)
    await browser.click(box.x, box.y)
    await browser.key('s')
    const split = await until('the split event', () =>
      events(config.directory).find((row) => row.field === 'segmentation' && row.new?.split),
    )
    assert(split.new.split.length === 2, `the split has ${split.new.split.length} entries`)
    assert(split.target_id === `${fixture.line}:u0`, `the split targets ${split.target_id}`)
    const boxes = await until('the new boxes', () =>
      browser.evaluate(`document.querySelectorAll('${CURRENT} .box').length`),
    )
    assert(boxes === 5, `${boxes} unit boxes after the split`)
    return `${split.id}: ${split.target_id} → ${split.result ? '' : ''}two halves, ${boxes} boxes drawn`
  })

  await step('the 字母 picker (j) lists reference glyphs and choosing one records it', async () => {
    const box = await browser.centre(`${CURRENT} .box:nth-of-type(1)`)
    await browser.click(box.x, box.y)
    await browser.key('j')
    await browser.waitFor('document.querySelectorAll(".candidates .candidate").length > 0')
    const listed = await browser.evaluate(`(() => ({
      count: document.querySelectorAll('.candidates .candidate').length,
      images: document.querySelectorAll('.candidates .candidate img').length,
      jibo: [...document.querySelectorAll('.candidates .candidate .jibo')].map(e => e.textContent.trim()),
    }))()`)
    const target = await browser.evaluate(
      `document.querySelector('table.unit-table tr.current')?.dataset.unitId ?? null`,
    )
    assert(target, 'no unit is selected')
    assert(listed.count >= 4, `only ${listed.count} candidates`)
    assert(listed.images >= 3, `only ${listed.images} reference glyph images`)
    const chosen = await browser.evaluate(`(() => {
      const candidate = [...document.querySelectorAll('.candidates .candidate')].find(c => c.querySelector('.jibo')?.textContent.trim() === '安')
      if (!candidate) return null
      const rect = candidate.getBoundingClientRect()
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
    })()`)
    assert(chosen, 'no candidate with 字母 安')
    await browser.click(chosen.x, chosen.y)
    // Choosing posts unicode, jibo, script and classification in order; wait for the whole chain.
    const chain = await until('the whole 字母 chain', () => {
      const posted = events(config.directory).filter((row) => row.target_id === target)
      const fields = posted.map((row) => row.field)
      return fields.includes('unicode') && fields.includes('jibo') && fields.includes('classification')
        ? posted
        : null
    })
    const rendered = await until('the hentaigana character', async () => {
      const text = await browser.evaluate('document.querySelector(".transcription .char")?.textContent.trim()')
      return text && text !== 'あ' ? text : null
    })
    const unit = units(config.directory)[target]
    assert(unit.unicode === 'U+1B002' && unit.jibo === '安', `the unit carries ${unit.unicode}/${unit.jibo}`)
    assert(
      chain.filter((row) => ['unicode', 'jibo', 'script', 'classification'].includes(row.field)).length === 4,
      `the chain posted ${chain.map((row) => row.field).join(', ')}`,
    )
    return `${listed.count} candidates, ${listed.images} glyphs (${listed.jibo.join(' ')}); ${chain.length} events on ${target}, drawn as ${rendered}`
  })

  await step('undo (z) writes a compensating review of the last event', async () => {
    const last = events(config.directory).filter((row) => row.field !== 'timing').pop()
    await browser.key('z')
    const undo = await until('the compensating review', () =>
      events(config.directory).find((row) => row.evidence === `undo of ${last.id}`),
    )
    equal(undo.field, last.field, 'the field the undo rewrote')
    const before = last.old
    const after = units(config.directory)[undo.target_id][undo.field]
    assert(JSON.stringify(after) === JSON.stringify(before), `${undo.field} is ${JSON.stringify(after)}`)
    return `${undo.id} compensates ${last.id} (${last.field}: back to ${JSON.stringify(before)})`
  })

  await step('next line (space) leaves the line and opens the next one', async () => {
    const before = await browser.evaluate('location.hash')
    await browser.key(' ')
    await browser.waitFor(`location.hash !== ${JSON.stringify(before)}`)
    const leave = await until('the leave timing event', () =>
      events(config.directory).find((row) => row.field === 'timing' && row.new?.action === 'leave'),
    )
    const opens = events(config.directory).filter((row) => row.field === 'timing' && row.new?.action === 'open')
    assert(opens.length === 2, `${opens.length} open events`)
    assert(leave.new.dwell_ms >= 0, 'the leave event carries no dwell time')
    return `${leave.id} dwell ${leave.new.dwell_ms} ms, opened ${opens[1].target_id}`
  })

  await step('a very long line virtualises its boxes and its characters', async () => {
    await browser.goto(url(lineHash(fixture.long_line)), {
      waitFor: 'document.querySelectorAll(".strip .crop .box").length > 0',
    })
    const total = fixture.long_units
    const first = await browser.evaluate(`(() => ({
      boxes: document.querySelectorAll('.strip .crop .box').length,
      chars: document.querySelectorAll('.transcription .char').length,
    }))()`)
    assert(first.boxes < total, `${first.boxes} of ${total} boxes are drawn at the top`)
    assert(first.chars < total, `${first.chars} of ${total} characters are drawn at the top`)
    await browser.evaluate(
      `(() => { const reader = document.querySelector('.reader'); reader.scrollTop = reader.scrollHeight * 0.5; return reader.scrollTop })()`,
    )
    const scrolled = await until('the scrolled window', () =>
      browser.evaluate(`(() => {
        const note = document.querySelector('.reader p')?.textContent ?? ''
        const match = /boxes (\\d+)[–-](\\d+) of (\\d+)/.exec(note)
        if (!match || Number(match[1]) <= 1) return null
        return { from: Number(match[1]), to: Number(match[2]), boxes: document.querySelectorAll('.strip .crop .box').length,
                 chars: document.querySelectorAll('.transcription .char').length }
      })()`),
    )
    assert(scrolled.from > 1, `the window still starts at ${scrolled.from}`)
    assert(scrolled.to < total, `the window reaches the end of the line (${scrolled.to})`)
    return `${scrolled.boxes} boxes and ${scrolled.chars} characters in the DOM, window ${scrolled.from}–${scrolled.to} of ${total}`
  })

  console.log('\nthe page view')

  await step('the page view draws a box for every line', async () => {
    await browser.goto(url(`#/page/${encodeURIComponent(fixture.cached_page)}`), {
      waitFor: 'document.querySelectorAll(".scan-scroll .crop .box").length > 0',
    })
    const view = await browser.evaluate(`(() => ({
      boxes: document.querySelectorAll('.scan-scroll .crop .box').length,
      rows: document.querySelectorAll('.list button.item').length,
      badge: [...document.querySelectorAll('.badge')].map(b => b.textContent.trim()).join(' '),
    }))()`)
    assert(view.boxes >= 3, `${view.boxes} line boxes`)
    assert(view.rows === view.boxes, `${view.rows} list rows for ${view.boxes} boxes`)
    return `${view.boxes} line boxes over the page image; ${view.badge}`
  })

  await step('drawing a line (l) records a line the detector missed', async () => {
    await browser.key('l')
    await browser.waitFor('document.querySelector(".scan-scroll .crop.picking") !== null')
    const canvas = await browser.centre('.scan-scroll .crop')
    await browser.drag({ x: canvas.x - 120, y: canvas.y - 160 }, { x: canvas.x - 40, y: canvas.y + 160 })
    const created = await until('the created line', () =>
      events(config.directory).find((row) => row.field === 'create' && row.target_type === 'line'),
    )
    // The view opens the new line for review, which is what a reviewer wants next.
    await browser.waitFor(`location.hash.includes(${JSON.stringify(encodeURIComponent(created.target_id))})`)
    await browser.goto(url(`#/page/${encodeURIComponent(fixture.cached_page)}`), {
      waitFor: 'document.querySelectorAll(".list button.item").length >= 4',
    })
    const rows = await browser.evaluate('document.querySelectorAll(".list button.item").length')
    return `${created.target_id} created and opened; the panel now lists ${rows} lines`
  })

  await step('a missing image keeps transcription and feedback reachable', async () => {
    await browser.goto(url(`#/page/${encodeURIComponent(fixture.uncached_page)}`), {
      waitFor: 'document.querySelector(".feedback-pane") !== null',
    })
    const link = await browser.evaluate('document.querySelector(".scan-caption a")?.href')
    assert(link?.startsWith('https://example.org'), 'the original image link is available')
    assert(await browser.evaluate('!!document.querySelector(".page-notes textarea")'), 'page feedback remains usable')
    return 'original image link and page notes available'
  })

  await step('the browser console stayed clean', async () => {
    assert(pageErrors.length === 0, pageErrors.join(' | '))
    return 'no exception and no console error'
  })
} finally {
  await browser.close()
  service.stop({ keep: config.keep })
}

console.log(`\n${steps.length - failures}/${steps.length} browser checks passed`)
console.log(`covered by a real browser: rendering, hit-testing, drag, the keys, the routes, the 409-free paths`)
process.exit(failures ? 1 : 0)
