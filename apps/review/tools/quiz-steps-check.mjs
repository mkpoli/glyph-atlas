#!/usr/bin/env bun
// Uses the running Atlas; all writes are intercepted before they reach the service.
// Suggestions are deterministic fixtures. Crops and nearby context come from the real API.
import { spawnSync } from 'node:child_process'
import { join } from 'node:path'
import { readFileSync } from 'node:fs'
import Browser from './browser.mjs'
import { ROUND_BATCH } from '../src/lib/reviewRounds.js'
const index = process.argv.indexOf('--server')
const server = index >= 0 ? process.argv[index + 1] : null
const build = spawnSync('bunx', ['vite', 'build', '--outDir', 'dist-next', '--emptyOutDir'],
  { cwd: join(import.meta.dir, '..'), encoding: 'utf8' })
if (build.status !== 0) throw new Error(build.stdout + build.stderr)
console.log('ok build')
if (!server) process.exit(0)
const assert = (value, message) => { if (!value) throw new Error(message) }
const browser = await Browser.launch({ width: 1440, height: 1000 })
const posted = [], errors = [], heldSuggestions = []
let holdContext = false
let mismatchDetail = null
let pagePhotoRequests = 0
const base = server.replace(/\/$/, '')
const catalogue = await (await fetch(base + '/atlas?purpose=review&state=pending&limit=1')).json()
const startReading = catalogue.categories.find(c => c.pending > ROUND_BATCH)?.label
assert(startReading, `test needs a character with more than ${ROUND_BATCH} pending crops`)
const sampleImage = Buffer.from(await (await fetch(base + catalogue.items[0].image)).arrayBuffer()).toString('base64')
const respond = (requestId, value) => browser.send('Fetch.fulfillRequest', {
  requestId, responseCode: 200,
  responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
  body: Buffer.from(JSON.stringify(value)).toString('base64'),
})
browser.listeners.push(event => {
  if (event.method === 'Runtime.exceptionThrown') errors.push(event.params.exceptionDetails.text)
  if (event.method !== 'Fetch.requestPaused') return
  const { requestId, request } = event.params
  const path = new URL(request.url).pathname
  if (path.startsWith('/images/')) pagePhotoRequests++
  if (request.url.startsWith('https://codh.rois.ac.jp/char-shape/iiif/')) {
    browser.send('Fetch.fulfillRequest', { requestId, responseCode: 200,
      responseHeaders: [{ name: 'Content-Type', value: 'image/jpeg' }], body: sampleImage })
  } else if (request.method === 'GET' && (path === '/' || path.startsWith('/assets/'))) {
    const file = join(import.meta.dir, '..', 'dist-next', path === '/' ? 'index.html' : path.slice(1))
    browser.send('Fetch.fulfillRequest', { requestId, responseCode: 200,
      responseHeaders: [{ name: 'Content-Type', value: path === '/' ? 'text/html' : path.endsWith('.css') ? 'text/css' : 'application/javascript' }],
      body: readFileSync(file).toString('base64') })
  } else if (mismatchDetail && path === '/atlas/characters/' + encodeURIComponent(mismatchDetail)) {
    fetch(request.url).then(response => response.json()).then(detail => respond(requestId, { ...detail, revision: detail.revision + 1 }))
  } else if (request.method !== 'GET') {
    posted.push({ url: request.url, ...JSON.parse(request.postData || '{}') })
    respond(requestId, { id: 'intercepted', results: [] })
  } else if (request.url.includes('/suggestions')) {
    const result = { status: 'ready', candidates: ['ア', 'カ', 'キセ', 'キヤ'].map(text => ({
      text, score: .9, engine: 'Test fixture', before: '前', after: '後',
    })) }
    if (holdContext && path.endsWith('/context')) heldSuggestions.push({ requestId, result })
    else respond(requestId, result)
  } else browser.send('Fetch.continueRequest', { requestId })
})
const click = async selector => {
  await browser.waitFor(`document.querySelector(${JSON.stringify(selector)}) && !document.querySelector(${JSON.stringify(selector)}).disabled`)
  await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({ block: 'center', behavior: 'instant' })`)
  const point = await browser.centre(selector)
  await browser.click(point.x, point.y)
}
const check = async (label, fn) => { await fn(); console.log('ok ' + label) }
const currentId = () => browser.evaluate('document.querySelector(".focus-figure").dataset.unit')
const noneState = () => browser.evaluate('document.querySelector(".no-suggestion").getAttribute("aria-pressed")')
try {
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] })
  await browser.goto(base + '/review?reading=' + encodeURIComponent(startReading), { waitFor: 'document.querySelectorAll(".quiz-choice").length > 0' })
  await browser.waitFor('document.querySelectorAll(".quiz-choice:not(:disabled)").length >= 4')
  await check('questions cover joined characters and bad cuts', async () => {
    const heading = await browser.evaluate('document.querySelector(".quiz-title").innerText')
    assert(heading.includes('Which crops need fixing?') && heading.includes('One complete') && heading.includes('extra characters') && heading.includes('bad cuts'), 'question only asks for a wrong identity')
  })
  await check('next switches character; back restores unsaved selections', async () => {
    assert(!await browser.evaluate('!!document.querySelector(".all-match")'), 'implicit match action remains')
    const before = await browser.evaluate('[...document.querySelectorAll(".quiz-tile")].map(t => t.dataset.unit)')
    await click('.quiz-choice:not(:disabled)')
    await click('.round-switch .quiet-link')
    await browser.waitFor('document.querySelectorAll(".quiz-choice:not(:disabled)").length > 0')
    const next = await browser.evaluate('document.querySelector(".target-character").innerText')
    assert(next !== startReading, 'Next repeated the character')
    await click('.previous-reading')
    await browser.waitFor('document.querySelectorAll(".quiz-choice:not(:disabled)").length >= 4')
    assert(await browser.evaluate('document.querySelector(".target-character").innerText') === startReading, 'Back did not restore character')
    assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.selected").length') === 1, 'Back lost unsaved selection')
    assert(JSON.stringify(await browser.evaluate('[...document.querySelectorAll(".quiz-tile")].map(t => t.dataset.unit)')) === JSON.stringify(before), 'Back reshuffled the round')
    await click('.quiz-tile.selected .quiz-choice')
    assert(posted.length === 0, 'navigation wrote reviews')
    assert(!/match/.test(await browser.evaluate('document.querySelector(".round-selection").innerText')), 'inferred matches in footer')
  })
  await check('scrolling to the end appends same-character crops without duplicates, a click, or losing selection', async () => {
    // Settle at the top first: automatic loading stops once the load-more row is well off screen.
    await browser.evaluate('window.scrollTo(0, 0)')
    const settled = 'document.querySelector(".load-more") && !document.querySelector(".load-more").innerText.startsWith("Loading")'
    let count = -1
    for (let i = 0; i < 20; i++) {
      await browser.waitFor(settled)
      await new Promise(resolve => setTimeout(resolve, 500))
      const now = await browser.evaluate('document.querySelectorAll(".quiz-tile").length')
      if (now === count) break
      count = now
    }
    const label = await browser.evaluate('document.querySelector(".load-more").innerText')
    assert(!/All .* loaded|Save this round/.test(label), 'the round was fully loaded before scrolling; the check needs a larger character')
    const before = await browser.evaluate('[...document.querySelectorAll(".quiz-tile")].map(t => t.dataset.unit)')
    await click('.quiz-choice:not(:disabled)')
    await browser.evaluate('window.__loadMoreClicked = false; document.querySelector(".load-more").addEventListener("click", () => window.__loadMoreClicked = true)')
    await browser.evaluate('window.scrollTo(0, document.body.scrollHeight)')
    await browser.waitFor(`document.querySelectorAll(".quiz-tile").length > ${before.length}`)
    await browser.waitFor(settled)
    const after = await browser.evaluate('[...document.querySelectorAll(".quiz-tile")].map(t => t.dataset.unit)')
    assert(!(await browser.evaluate('window.__loadMoreClicked')), 'the button was clicked')
    assert(await browser.evaluate('document.querySelector(".target-character").innerText') === startReading, 'loading more changed character')
    assert(new Set(after).size === after.length, 'loading more repeated crops')
    assert(JSON.stringify(after.slice(0, before.length)) === JSON.stringify(before), 'loading more replaced old crops')
    assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.selected").length') === 1, 'loading more lost the selection')
    assert(posted.length === 0, 'loading more wrote reviews')
    await click('.quiz-tile.selected .quiz-choice')
    await browser.evaluate('window.scrollTo(0, 0)')
  })
  await check('branching from an earlier character preserves forward drafts', async () => {
    await click('.forward-reading')
    await browser.waitFor('document.querySelectorAll(".quiz-choice:not(:disabled)").length > 0')
    const second = await browser.evaluate('document.querySelector(".target-character").innerText')
    await click('.quiz-choice:not(:disabled)')
    await click('.previous-reading')
    await click('.round-switch .quiet-link')
    await browser.waitFor('document.querySelectorAll(".quiz-choice:not(:disabled)").length > 0')
    assert(await browser.evaluate('document.querySelectorAll(".history-character").length') === 3, 'new round discarded forward history')
    await click('.forward-reading')
    await browser.waitFor('document.querySelectorAll(".quiz-choice:not(:disabled)").length > 0')
    assert(await browser.evaluate('document.querySelector(".target-character").innerText') === second, 'forward draft changed character')
    assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.selected").length') === 1, 'forward draft lost selection')
    await click('.history-character:first-child')
    await browser.waitFor('document.querySelectorAll(".quiz-choice:not(:disabled)").length >= 4')
  })
  let selected
  await check('selecting problems does not save or choose an issue', async () => {
    assert(!await browser.evaluate('!!document.querySelector(".issue-picker")'), 'issues before selection')
    selected = await browser.evaluate(`(() => {
      const buttons = [...document.querySelectorAll('.quiz-choice:not(:disabled)')].slice(0, 4)
      buttons.forEach(button => button.click())
      return buttons.map(button => button.closest('.quiz-tile').dataset.unit)
    })()`)
    await click('.review-selected')
    await browser.waitFor('!!document.querySelector(".issue-picker")')
    assert(await currentId() === selected[0], 'wrong first crop')
    assert(posted.length === 0, 'selecting wrote reviews')
  })
  await check('a problem immediately offers corrections for the SAME crop', async () => {
    const badCrop = await browser.evaluate('document.querySelector("[data-issue=crop]").innerText')
    assert(badCrop.includes('Bad crop') && badCrop.includes('extra ink'), 'crop category too narrow')
    holdContext = true
    await click('[data-issue=reading]')
    await browser.waitFor('!!document.querySelector(".reading-suggestions")')
    await browser.waitFor('!!document.querySelector(".suggestion-options button")')
    assert(await currentId() === selected[0], 'issue selection jumped to next crop')
    assert(await noneState() === 'false', 'none is selected by default')
    assert(!await browser.evaluate('!!document.querySelector(".suggestion-source")'), 'suggestions expose model source groups')
    const texts = await browser.evaluate('[...document.querySelectorAll(".suggestion-options button")].map(b => b.innerText)')
    assert(new Set(texts).size === texts.length, 'suggestions duplicated the same answer')
    assert(await browser.evaluate('document.activeElement.closest(".reading-suggestions") !== null'), 'correction area not focused')
  })
  await check('fast suggestions stay usable and late results survive character navigation', async () => {
    assert(heldSuggestions.length > 0, 'context request was not held')
    assert(await browser.evaluate('document.querySelector(".reading-suggestions").innerText.includes("Finding suggestions")'), 'slower source not pending')
    await click('.round-switch .quiet-link')
    await browser.waitFor('document.querySelectorAll(".quiz-choice:not(:disabled)").length > 0')
    holdContext = false
    await Promise.all(heldSuggestions.splice(0).map(({requestId, result}) => respond(requestId, result)))
    await click('.previous-reading')
    await click('.review-selected')
    await browser.waitFor('!!document.querySelector(".reading-suggestions")')
    assert(await currentId() === selected[0], 'return lost correction target')
    await browser.waitFor('!document.querySelector(".reading-suggestions").innerText.includes("Finding suggestions")')
  })
  await check('None / not sure is selectable and clears a prior candidate', async () => {
    const characterColor = await browser.evaluate('getComputedStyle(document.querySelector(".suggestion-options .script-char")).color')
    await click('.suggestion-options button')
    assert(await browser.evaluate('getComputedStyle(document.querySelector(".suggestion-options button[aria-pressed=true] .script-char")).color') === characterColor, 'selection overwrote script color')
    assert(await browser.evaluate('!!document.querySelector(".reading-suggestions .script-legend") && !document.querySelector(".reading-suggestions .script-badge")'), 'suggestions need one legend instead of per-option tags')
    assert(await noneState() === 'false', 'candidate retained None')
    await click('.no-suggestion')
    assert(await noneState() === 'true', 'None did not select')
    assert(!await browser.evaluate('!!document.querySelector(".suggestion-options button[aria-pressed=true]")'), 'old candidate remained')
    await click('.next-crop')
    await browser.waitFor('!!document.querySelector(".issue-picker")')
    assert(await currentId() === selected[1], 'next did not ask second crop problem')
    await click('.focus-thumb[data-index="0"]')
    await browser.waitFor('!!document.querySelector(".no-suggestion")')
    assert(await noneState() === 'true', 'None lost on navigation')
    await click('.next-crop')
  })
  await check('one context viewport pans the photo and shadow opening together', async () => {
    await browser.waitFor('document.querySelector(".crop-viewport")?.dataset.ready === "true"')
    assert(pagePhotoRequests === 0, 'review downloaded full manuscript pages before any pan or zoom')
    assert(!await browser.evaluate('!!document.querySelector(".crop-context-image, .crop-context-outline, .crop-fallback")'), 'separate crop/context images remain')
    const bounds = () => browser.evaluate(`(() => {
      const mask = document.querySelector('.crop-mask'), photo = document.querySelector('.crop-plane')
      const box = mask.getBoundingClientRect(), image = photo.getBoundingClientRect(), style = getComputedStyle(mask)
      return { x: box.x, y: box.y, px: image.x, py: image.y, border: style.borderWidth, shadow: style.boxShadow }
    })()`)
    await browser.evaluate('document.querySelector(".crop-viewport").scrollIntoView({ block: "center", behavior: "instant" })')
    const before = await bounds(), point = await browser.centre('.crop-viewport')
    assert(before.border === '0px' && before.shadow !== 'none', 'crop uses a border instead of shadow')
    await browser.drag(point, {x: point.x + 42, y: point.y + 28})
    const after = await bounds()
    assert(Math.abs(after.x - before.x - 42) < 2 && Math.abs(after.y - before.y - 28) < 2, 'drag did not move crop opening')
    assert(Math.abs(after.x - before.x - after.px + before.px) < 1 && Math.abs(after.y - before.y - after.py + before.py) < 1, 'image and crop opening drifted apart')
    await browser.key('ArrowRight')
    assert(await currentId() === selected[1], 'viewer arrow advanced the review queue')
    assert(await browser.evaluate('Number(document.querySelector(".crop-viewport").dataset.panX)') !== 42, 'viewer arrow did not pan')
    await click('.zoom-in')
    assert(await browser.evaluate('Number(document.querySelector(".crop-viewport").dataset.zoom)') > 1, 'zoom did not work')
    await click('.reset-crop')
    assert(await browser.evaluate('document.querySelector(".crop-viewport").dataset.panX') === '0', 'reset retained pan')
    assert(await browser.evaluate('document.querySelector(".crop-viewport").dataset.zoom') === '1', 'reset retained zoom')
    const centered = await browser.evaluate(`(() => {
      const viewport = document.querySelector('.crop-viewport'), v = viewport.getBoundingClientRect(), m = document.querySelector('.crop-mask').getBoundingClientRect()
      return {dx:m.x + m.width / 2 - v.x - v.width / 2, dy:m.y + m.height / 2 - v.y - v.height / 2, scrollTop:viewport.scrollTop, scrollLeft:viewport.scrollLeft}
    })()`)
    assert(Math.abs(centered.dx) < 1 && Math.abs(centered.dy) < 1 && centered.scrollTop === 0 && centered.scrollLeft === 0,
      'reset did not center the complete reviewed crop: ' + JSON.stringify(centered))
    assert(posted.length === 0, 'viewing context wrote a review or changed its box')
  })
  await check('changing an issue clears incompatible corrections', async () => {
    await click('[data-issue=merged]')
    await click('.suggestion-options button')
    await click('.focus-back')
    await click('[data-issue=reading]')
    assert(!await browser.evaluate('!!document.querySelector(".suggestion-options button[aria-pressed=true]")'), 'joined text leaked into single-character correction')
    await click('.focus-back')
    await click('[data-issue=merged]')
    await click('.no-suggestion')
    await browser.evaluate('window.scrollTo({top: 0, behavior: "instant"})')
    await browser.screenshot('/tmp/atlas-review-context-desktop.png', { fullPage: true })
    mismatchDetail = selected[2]
    await click('.next-crop')
    assert(await currentId() === selected[2], 'wrong third crop')
  })
  await check('a changed detail cannot supply context for the saved crop', async () => {
    await browser.waitFor('!!document.querySelector(".crop-only")')
    assert(await browser.evaluate('!!document.querySelector(".crop-fallback img")'), 'standalone crop fallback missing')
    assert(!await browser.evaluate('!!document.querySelector(".crop-plane, .crop-mask")'), 'mismatched revision displayed a context')
    assert(await browser.evaluate('document.querySelector(".reset-crop").disabled'), 'pan enabled for mismatched context')
    mismatchDetail = null
  })
  await check('bad crop needs no text; Skip skips without saving', async () => {
    await click('[data-issue=crop]')
    assert(!await browser.evaluate('!!document.querySelector(".reading-suggestions")'), 'bad crop demands text')
    await click('.next-crop')
    assert(await currentId() === selected[3], 'wrong fourth crop')
    // Skip is a grid control, not an issue: leaving the fourth crop's own decision means going back
    // to the grid, skipping it there, and returning to the three crops that already have one.
    await click('.focus-back')
    await browser.waitFor('document.querySelectorAll(".quiz-choice:not(:disabled)").length > 0')
    await click(`.quiz-tile[data-unit="${selected[3]}"] .skip-choice`)
    await click('.review-selected')
    await browser.waitFor('document.querySelectorAll(".focus-thumb").length === 3')
    await click('.focus-thumb[data-index="2"]')
    assert(await currentId() === selected[2], 'skip left stale crop')
    assert(posted.length === 0, 'skip or issue selection saved')
  })
  await check('mobile context and controls fit the viewport', async () => {
    await browser.setViewport(390, 844)
    assert(await browser.evaluate('document.documentElement.scrollWidth <= 392'), 'horizontal overflow')
    await browser.setViewport(1440, 1000)
  })
  await check('final save sends only explicit problems', async () => {
    await click('.save-round')
    await browser.waitFor('document.querySelector(".quiz-grid")?.getAttribute("aria-busy") === "false"')
    assert(posted.length === 1, 'expected one intercepted write')
    assert(await browser.evaluate('document.querySelector(".target-character").innerText') !== startReading, 'saving repeated the same character')
    const payload = posted[0]
    assert(payload.url.endsWith('/atlas/rounds'), 'wrong endpoint')
    assert(payload.answers.length === 3, 'unselected or skipped crops were saved')
    assert(payload.answers.every(a => a.verdict === 'wrong'), 'implicit positive verdict')
    assert(payload.answers.every(a => !a.character && !a.correction && !('noneSelected' in a)), 'correction or UI state leaked')
    assert(payload.answers.every(a => selected.slice(0, 3).includes(a.id)), 'unselected answer')
    assert(payload.answers.map(a => a.issue).sort().join() === 'crop,merged,reading', 'issues lost')
    assert(await browser.evaluate('document.querySelector(".round-count").innerText.includes("3 issues saved")'), 'counter includes unselected crops')
  })
  await check('reselecting Wrong character preserves the written identity layer', async () => {
    await browser.waitFor('document.querySelectorAll(".quiz-choice:not(:disabled)").length >= 1')
    await click('.quiz-choice:not(:disabled)')
    await click('.review-selected')
    await click('[data-issue=reading]')
    await click('.suggestion-options button')
    const chosen = await browser.evaluate('document.querySelector(".suggestion-options button[aria-pressed=true]").innerText')
    await click('.focus-back')
    assert(await browser.evaluate('document.querySelector("[data-issue=reading]").getAttribute("aria-pressed")') === 'true', 'same visible issue lost its selection')
    await click('[data-issue=reading]')
    assert(await browser.evaluate('document.querySelector(".suggestion-options button[aria-pressed=true]")?.innerText') === chosen, 'same issue lost correction')
    await click('.save-round')
    await browser.waitFor('document.querySelector(".quiz-grid")?.getAttribute("aria-busy") === "false"')
    const payload = posted[1]
    assert(payload?.answers.length === 1, 'expected one correction')
    assert(payload.answers[0].issue === 'character' && payload.answers[0].character === chosen && !payload.answers[0].correction, 'identity became a reading edit')
  })
  await check('export defaults to new feedback and full history remains selectable', async () => {
    const pending = await (await fetch(base + '/atlas/reviews')).json()
    assert(pending.scope === 'unprocessed', 'backend has not enabled incremental exports')
    assert(pending.reviews.length === pending.counts.unprocessed, 'pending count differs from export')
    await click('[aria-label="Review options"]')
    await click('.options-menu button:last-of-type')
    await browser.waitFor('!!document.querySelector(".export-count")')
    assert(!await browser.evaluate('document.querySelector(".export-history input").checked'), 'history enabled by default')
    if (!pending.reviews.length) {
      assert(await browser.evaluate('document.querySelector(".export-count").innerText') === 'No new reviews to export.', 'empty export unclear')
      assert(!await browser.evaluate('!!document.querySelector(".export-actions")'), 'empty export offered a download')
    } else assert(await browser.evaluate('JSON.parse(document.querySelector(".export-dialog textarea").value).scope') === 'unprocessed', 'preview included processed feedback by default')
    await click('.export-history input')
    await browser.waitFor('!!document.querySelector(".export-dialog textarea")')
    const full = await browser.evaluate('JSON.parse(document.querySelector(".export-dialog textarea").value)')
    assert(full.scope === 'history' && full.reviews.length === full.counts.total, 'checkbox did not request full history')
    assert(full.reviews.length >= pending.reviews.length, 'history lost reviews')
    assert(await browser.evaluate('document.querySelector(".export-actions a").getAttribute("href")') === '/atlas/reviews.json?include_processed=true', 'download scope differs from preview')
    await click('.export-history input')
    await browser.waitFor('!!document.querySelector(".export-count")')
    const value = await browser.evaluate('document.querySelector(".export-dialog textarea")?.value')
    if (value) assert(JSON.parse(value).scope === 'unprocessed', 'unchecking history retained full export')
    else assert(await browser.evaluate('document.querySelector(".export-count").innerText') === 'No new reviews to export.', 'failed to return to incremental export')
    await click('[aria-label="Close export"]')
  })
  await check('standalone reviewer uses one viewport and keeps manual crop adjustment explicit', async () => {
    const writes = posted.length
    await browser.evaluate(`visit('/crop/' + encodeURIComponent(${JSON.stringify(selected[0])}))`)
    await browser.waitFor('document.querySelector(".character-dialog .crop-viewport")?.dataset.ready === "true"')
    await browser.waitFor('document.querySelector(".save-character")?.disabled === false')
    assert(!await browser.evaluate('!!document.querySelector(".inspector-crop, .nearby")'), 'standalone reviewer retained duplicate panels')
    await click('.advanced-edit > summary')
    await click('.adjust-crop')
    await browser.waitFor('document.querySelector(".context-region.drawing img")?.naturalWidth > 0')
    assert(!await browser.evaluate('!!document.querySelector(".character-dialog .crop-viewport")'), 'crop editor duplicated the viewport')
    const point = await browser.centre('.context-region.drawing')
    await browser.drag({x:point.x - point.w * .15, y:point.y - point.h * .1}, {x:point.x + point.w * .15, y:point.y + point.h * .1})
    await click('.crop-adjustment figcaption button')
    await browser.waitFor('document.querySelector(".character-dialog .crop-viewport")?.dataset.ready === "true"')
    assert(await browser.evaluate('!!document.querySelector(".crop-change")'), 'draft crop was discarded on leaving editor')
    assert(!await browser.evaluate('!!document.querySelector(".context-region.drawing")'), 'drawing surface remained after Done')
    assert(posted.length === writes, 'panning or editing saved a crop without submission')
    await browser.setViewport(390, 844)
    assert(await browser.evaluate('document.querySelector("dialog").scrollWidth <= document.querySelector("dialog").clientWidth + 1'), 'standalone viewport overflows mobile')
    await browser.setViewport(1440, 1000)
    await click('.close-inspector')
  })
  await check('corpus reviewer shares the viewport without treating provenance as page pixels', async () => {
    const before = pagePhotoRequests, writes = posted.length
    await browser.evaluate(`visit('/corpus/' + encodeURIComponent('codh:200008316:200008316_00030_2:B0001:C0027'))`)
    await browser.waitFor('document.querySelector(".corpus-dialog .crop-viewport")?.dataset.ready === "true"')
    await browser.waitFor('document.querySelector(".corpus-dialog .save-character")?.disabled === false')
    assert(!await browser.evaluate('!!document.querySelector(".corpus-dialog .inspector-crop, .corpus-dialog .nearby, .corpus-dialog .page-photo")'), 'corpus has duplicate views or wrong page source')
    assert(pagePhotoRequests === before, 'corpus provenance used as a page image hash')
    await browser.evaluate('document.querySelector(".crop-viewport").scrollIntoView({ block: "center", behavior: "instant" })')
    const point = await browser.centre('.corpus-dialog .crop-viewport')
    await browser.drag(point, {x: point.x + 25, y:point.y + 20})
    assert(await browser.evaluate('Number(document.querySelector(".crop-viewport").dataset.panX)') === 25, 'corpus context did not pan')
    await click('.reset-crop')
    assert(posted.length === writes, 'corpus viewing wrote a review')
    await click('.close-inspector')
  })
  assert(!errors.length, errors.join(' | '))
  console.log('all quick review checks passed; no data written')
} catch (error) {
  console.log('Browser state:', await browser.evaluate('document.querySelector("dialog[open]")?.innerText ?? document.body.innerText.slice(0,1600)'))
  throw error
} finally { await browser.close() }
