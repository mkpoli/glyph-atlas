#!/usr/bin/env bun
// Run through devrun. All editorial writes use a disposable dataset.
import { join } from 'node:path'
import { mkdirSync } from 'node:fs'
import Browser from './browser.mjs'
import { boot, options, events, units } from './harness.mjs'
const config = options(), service = await boot(config)
const screenshots = '/tmp/atlas-character-shots'
mkdirSync(screenshots, { recursive: true })
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })
  // Deterministic OCR suggestions for synthetic glyphs; real model smoke is a separate read-only check.
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*\/suggestions?*' }] })
  browser.listeners.push(m => { if (m.method === 'Fetch.requestPaused') browser.send('Fetch.fulfillRequest', {
    requestId: m.params.requestId, responseCode: 200,
    responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
    body: Buffer.from(JSON.stringify({ status: 'ready', candidates: [
      { text: 'シヨロ', engine: 'NDLkotenOCR', score: .9 },
      { text: 'カ', engine: 'fixture classifier', score: .7 },
      { text: 'ア', engine: 'fixture classifier', score: .2 },
    ] })).toString('base64'),
  }) })
  async function click(selector) { await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`); const p = await browser.centre(selector); await browser.click(p.x, p.y) }
  async function route(hash, ready) { await browser.evaluate(`visit(${JSON.stringify(hash)})`); await browser.waitFor(ready) }
  const roundReady = 'document.querySelectorAll(".quiz-choice").length > 0 && !document.querySelector(".quiz-submit .primary")?.disabled'
  const inspectorReady = 'document.querySelector("dialog[open] .inspector-crop img")?.naturalWidth > 0 && !document.querySelector(".save-character")?.disabled'
  await browser.goto(service.base + '/', { waitFor: 'document.querySelectorAll(".glyph-tile").length > 0' })
  await browser.waitFor('Array.from(document.querySelectorAll(".glyph-grid img")).slice(0,12).every(i => i.complete && i.naturalWidth)')
  assert(!await browser.evaluate('document.querySelector("nav").innerText.includes("Sources")'), 'old source navigation remains')
  const before = await browser.evaluate('document.querySelector(".glyph-grid img").src')
  await click('.shuffle')
  await browser.waitFor(`document.querySelector('.glyph-grid img')?.src !== ${JSON.stringify(before)}`)
  console.log('PASS crop grid and shuffle')

  await click('.glyph-tile:nth-child(10)')
  await browser.waitFor(inspectorReady)
  const originalImage = await browser.evaluate('document.querySelector(".inspector-crop img").getAttribute("src")')
  const originalId = decodeURIComponent(originalImage.split('/characters/')[1].split('/image')[0])
  const originalReading = units(config.directory)[originalId].reading
  const order = await browser.evaluate('Array.from(document.querySelectorAll(".glyph-grid [data-unit]")).map(i=>i.dataset.unit)')
  const scroll = await browser.evaluate('scrollY')
  await browser.evaluate('window.sameReviewDialog = document.querySelector("dialog");window.sameCollection = document.querySelector(".glyph-grid")')
  assert(!await browser.evaluate('document.querySelector(".advanced-edit").open'), 'manual typing should be optional')
  await click('dialog .issue-card[data-issue="merged"]')
  await browser.waitFor('document.querySelector("dialog .suggestion-options") !== null')
  assert(await browser.evaluate('document.querySelector(".save-character").innerText.includes("Save issue")'), 'reporting an error must not confirm the wrong label')
  await browser.screenshot(join(screenshots, 'error-review-desktop.png'))
  await click('.save-character')
  await browser.waitFor('document.querySelector("dialog[open]") === null')
  assert(await browser.evaluate('document.querySelector(".glyph-grid") === window.sameCollection'), 'save must not remount the collection')
  assert(await browser.evaluate('JSON.stringify(Array.from(document.querySelectorAll(".glyph-grid [data-unit]")).map(i=>i.dataset.unit))') === JSON.stringify(order), 'save must preserve crop order')
  assert(await browser.evaluate('scrollY') === scroll, 'save must preserve collection scroll position')
  const firstReport = events(config.directory).find(e => e.target_id === originalId && e.field === 'review')
  assert(firstReport?.new === 'disputed' && JSON.parse(firstReport.evidence).issue === 'merged', 'joined report must persist without text')
  const reportedTile = `.glyph-grid [data-unit="${originalId}"]`
  console.log('PASS no typing, correct error save, back to a stable collection')

  await click(reportedTile)
  await browser.waitFor(inspectorReady)
  await click('dialog .issue-card[data-issue="reading"]')
  await browser.waitFor('document.querySelector("dialog .suggestion-options button")?.innerText === "カ"')
  await click('dialog .suggestion-options button')
  await click('dialog .no-suggestion')
  assert(await browser.evaluate('document.querySelector(".written-input input").value === document.querySelector(".inspector-title h2").textContent'),
    'None of these cancels the proposed identity')
  await click('dialog .suggestion-options button')
  await click('.save-character')
  await browser.waitFor('document.querySelector("dialog[open]") === null')
  assert(units(config.directory)[originalId].unicode === 'U+30AB', 'choosing an OCR suggestion updates the written identity')
  assert(units(config.directory)[originalId].reading === originalReading, 'an identity correction preserves the reading')
  console.log('PASS optional correction by clicking a suggestion')

  await click(reportedTile)
  await browser.waitFor(inspectorReady)
  await click('.advanced-edit summary')
  await click('.adjust-crop')
  await browser.waitFor('document.querySelector(".context-region img")?.naturalWidth > 0')
  await browser.evaluate('document.querySelector(".context-region").scrollIntoView({block:"center"})')
  const region = await browser.evaluate('(() => { const r=document.querySelector(".context-region").getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}; })()')
  await browser.drag({x:region.x+region.w*.25,y:region.y+region.h*.25},{x:region.x+region.w*.65,y:region.y+region.h*.72})
  assert(await browser.evaluate('document.querySelector(".crop-change") !== null'), 'dragging adjusts crop')
  await click('.save-character')
  await browser.waitFor('document.querySelector("dialog[open]") === null')
  assert(events(config.directory).some(e => e.field === 'box'), 'crop adjustment saved')
  console.log('PASS optional crop adjustment')

  await route('/review?reading=あ', roundReady)
  const availableTiles = await browser.evaluate('Array.from(document.querySelectorAll(".quiz-tile")).flatMap((tile, index) => tile.classList.contains("unavailable") ? [] : [index + 1])')
  assert(availableTiles.length >= 4, 'fixture has four viewable crops')
  const tile = n => `.quiz-tile:nth-child(${availableTiles[n]})`
  await click(tile(0) + ' .quiz-choice')
  await click(tile(1) + ' .quiz-choice')
  assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.selected").length === 2'), 'multiple crops selected')
  assert(await browser.evaluate('document.querySelector(".quiz-submit .primary").disabled'), 'untyped selection must not silently confirm')
  await click('.quiz-workspace .issue-card[data-issue="merged"]')
  assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.wrong").length === 2'), 'one error type applies to both selected crops')
  assert(!await browser.evaluate('document.querySelector(".quiz-submit .primary").disabled'), 'suggestions must not be required to save')
  const skippedId = await browser.evaluate(`document.querySelector('${tile(2)}').dataset.unit`)
  await click(tile(2) + ' .skip-choice')
  assert(await browser.evaluate(`document.querySelector('${tile(2)}').classList.contains('skipped')`), 'Skip skips the crop')
  await click(tile(3) + ' .quiz-choice')
  await browser.key('1')
  await browser.waitFor(`document.querySelector('${tile(3)} .suggestion-options button')?.innerText === "カ"`)
  const roundImage = await browser.evaluate(`document.querySelector('${tile(3)} img').getAttribute("src")`)
  const correctedId = decodeURIComponent(roundImage.split('/characters/')[1].split('/image')[0])
  const beforeCorrection = units(config.directory)[correctedId]
  await click(tile(3) + ' .suggestion-options button')
  await click(tile(3) + ' .no-suggestion')
  await click(tile(3) + ' .suggestion-options button')
  await browser.screenshot(join(screenshots, 'error-quiz-desktop.png'))
  await browser.evaluate('document.activeElement.blur()')
  await browser.key('Enter')
  await browser.waitFor(`document.querySelector(".round-count")?.innerText.includes("${availableTiles.length - 1} reviewed")`)
  const rounds = events(config.directory).filter(e => e.field === 'review' && e.evidence?.includes('"kind": "visual-quiz"'))
  assert(rounds.length === availableTiles.length - 1, 'only decided viewable crops are saved')
  assert(!rounds.some(e => e.target_id === skippedId), 'Skip wrote no review')
  assert(rounds.filter(e => e.new === 'disputed').length === 2, 'joined crops remain flagged')
  assert(rounds.filter(e => e.new === 'reviewed').length === availableTiles.length - 3, 'matches and explicit correction confirmed')
  assert(rounds.filter(e => JSON.parse(e.evidence).issue === 'merged').length === 2, 'distinct error types retained')
  const corrected = units(config.directory)[correctedId]
  assert(corrected.unicode === 'U+30AB' && corrected.reading === beforeCorrection.reading,
    'the round changes the selected identity while preserving its reading')
  const exported = await (await fetch(service.base + '/atlas/reviews')).json()
  const correctionReview = exported.reviews.filter(row => row.event.target_id === correctedId).pop()
  assert(correctionReview?.current, 'the round identity correction is current in the export')
  assert(JSON.parse(correctionReview.event.evidence).correction.unicode === 'U+30AB',
    'the export carries the corrected identity')
  console.log('PASS multi-select, illustrated error types, optional suggestion, keyboard save and advance')

  await browser.send('Page.reload')
  await browser.waitFor(roundReady)
  assert(await browser.evaluate('document.querySelector(".undo-round") !== null'), 'last round retained after reload')
  await click('.undo-round')
  await browser.waitFor('document.querySelector(".undo-round") === null')
  assert(events(config.directory).filter(e => e.evidence?.startsWith('undo of ')).length === availableTiles.length, 'undo includes the identity correction and excludes the skip')
  const restored = units(config.directory)[correctedId]
  assert(restored.unicode === beforeCorrection.unicode && restored.reading === beforeCorrection.reading,
    'undo restores identity and reading')
  console.log('PASS exported identity correction and durable undo preserve the reading')

  await browser.waitFor(roundReady)
  const nextAvailable = await browser.evaluate('document.querySelectorAll(".quiz-tile:not(.unavailable)").length')
  await click('.stage-toolbar .bulk-toggle')
  assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.selected").length') === nextAvailable, 'select all viewable crops')
  await click('.stage-toolbar .bulk-toggle')
  assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.selected").length === 0'), 'deselect all')
  await click('.quiz-tile:not(.unavailable) .quiz-choice')
  await click('.quiz-workspace .issue-card[data-issue="blank"]')
  const lastImage = await browser.evaluate('Array.from(document.querySelectorAll(".quiz-tile:not(.unavailable) img")).at(-1).getAttribute("src")')
  const lastId = decodeURIComponent(lastImage.split('/characters/')[1].split('/image')[0])
  const mark = events(config.directory).length
  await fetch(service.base + '/reviews', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ target_type:'unit', target_id:lastId, field:'note', new:'Concurrent edit', client_id:'other' }) })
  await click('.quiz-submit .primary')
  await browser.waitFor('document.querySelector(".quiz-workspace .error-message")?.innerText.includes("round changed")')
  assert(events(config.directory).length === mark + 1, 'stale round has no partial saves')
  assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.wrong").length === 1'), 'stale round keeps choices')
  console.log('PASS conflicts preserve choices without partial saves')

  await browser.setViewport(390, 844)
  await browser.screenshot(join(screenshots, 'error-quiz-mobile.png'))
  assert(await browser.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'quiz mobile overflow')
  await route('/', 'document.querySelectorAll(".glyph-tile").length > 0')
  await click('.glyph-tile')
  await browser.waitFor(inspectorReady)
  await click('dialog .issue-card[data-issue="merged"]')
  await browser.screenshot(join(screenshots, 'error-review-mobile.png'))
  assert(await browser.evaluate('document.querySelector("dialog").scrollWidth <= innerWidth + 1'), 'reviewer mobile overflow')
  await click('.close-inspector')
  console.log('PASS mobile error choices and continuous reviewer')

  await browser.send('Network.enable')
  await browser.send('Network.setBlockedURLs', { urls: ['*/atlas/characters/*/image*'] })
  await browser.send('Network.setCacheDisabled', { cacheDisabled: true })
  await route('/review?reading=い', 'document.querySelectorAll(".quiz-tile.unavailable").length > 0')
  await browser.waitFor('document.querySelectorAll(".quiz-tile.unavailable").length === document.querySelectorAll(".quiz-tile").length')
  assert(await browser.evaluate('document.querySelector(".quiz-submit .primary").classList.contains("next-round")'), 'unseen crops offer only Next round')
  const beforeUnavailable = events(config.directory).length
  await click('.next-round')
  await browser.waitFor('document.querySelectorAll(".quiz-tile.unavailable").length > 0')
  assert(events(config.directory).length === beforeUnavailable, 'unseen crops cannot be confirmed')
  assert(errors.length === 0, 'browser exceptions: ' + errors.join(', '))
  console.log('PASS unavailable images and no browser exceptions')
} catch (error) {
  if (browser) { console.log('Failure detail:', await browser.evaluate('Array.from(document.querySelectorAll(".error-message")).map(e=>e.innerText).join("; ")')); await browser.screenshot(join(screenshots, 'failure.png')) }
  console.log('Service failure:', service.log.slice(-8).join('').slice(-10000))
  throw error
} finally { await browser?.close(); await service.stop() }
