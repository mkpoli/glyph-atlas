#!/usr/bin/env bun
// Exercise the replacement UI against a disposable dataset through devrun.
import { join } from 'node:path'
import { mkdirSync } from 'node:fs'
import Browser from './browser.mjs'
import { boot, options, events } from './harness.mjs'
const config = options(), service = await boot(config)
const screenshots = '/tmp/atlas-character-shots'
mkdirSync(screenshots, { recursive: true })
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })
  async function click(selector) { await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block: 'center'})`); const p = await browser.centre(selector); await browser.click(p.x, p.y) }
  async function route(hash, ready) { await browser.evaluate(`location.hash=${JSON.stringify(hash)}`); await browser.waitFor(ready) }
  const roundReady = 'document.querySelectorAll(".quiz-choice").length === 12 && !document.querySelector(".quiz-submit .primary")?.disabled'
  await browser.goto(service.base + '/#/', { waitFor: 'document.querySelectorAll(".glyph-tile").length > 0' })
  await browser.waitFor('Array.from(document.querySelectorAll(".glyph-grid img")).slice(0,12).every(i => i.complete && i.naturalWidth)')
  assert(!await browser.evaluate('document.querySelector("nav").innerText.includes("Sources")'), 'old source navigation remains')
  const before = await browser.evaluate('document.querySelector(".glyph-grid img").src')
  await click('.shuffle')
  await browser.waitFor(`document.querySelector('.glyph-grid img')?.src !== ${JSON.stringify(before)}`)
  await browser.screenshot(join(screenshots, 'explore.png'))
  console.log('PASS real crop grid, shuffle, character-only navigation')

  await click('.glyph-tile')
  await browser.waitFor('document.querySelector("dialog[open] .reading-input input") !== null')
  await click('.inspector-tabs button:last-child')
  await browser.waitFor('document.querySelector(".context-region img")?.naturalWidth > 0')
  assert(await browser.evaluate('document.querySelector(".context-outline") !== null'), 'context highlights selected character')
  await click('.context-caption button')
  const region = await browser.evaluate('(() => { const r=document.querySelector(".context-region").getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}; })()')
  await browser.drag({x:region.x+region.w*.25,y:region.y+region.h*.25},{x:region.x+region.w*.65,y:region.y+region.h*.72})
  assert(await browser.evaluate('document.querySelector(".crop-change") !== null'), 'dragging adjusts the character crop')
  await browser.evaluate(`(() => { const input=document.querySelector('.reading-input input');input.value='し';input.dispatchEvent(new Event('input',{bubbles:true})); })()`)
  await click('.save-character')
  await browser.waitFor('document.querySelector("dialog[open]") === null')
  assert(events(config.directory).some(e => e.field === 'reading' && e.new === 'し'), 'reading edit is durable')
  assert(events(config.directory).some(e => e.field === 'box'), 'crop adjustment is durable')
  console.log('PASS character inspector, context, crop drag and reading correction')

  await route('#/review?reading=あ', roundReady)
  await click('.quiz-tile:nth-child(1) .quiz-choice')
  await click('.quiz-tile:nth-child(2) .quiz-tile-tools button:nth-of-type(1)')
  await click('.quiz-tile:nth-child(3) .quiz-tile-tools button:nth-of-type(2)')
  await browser.waitFor('document.querySelector(".inspector-decisions") !== null')
  await click('.inspector-decisions .negative')
  assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.wrong").length === 2'), 'context choice returns to current round')
  assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.unsure").length === 1'), 'uncertain is distinct')
  await browser.screenshot(join(screenshots, 'quiz.png'))
  await browser.evaluate('document.querySelector(".quiz-choice").focus()')
  await browser.key('Enter')
  await browser.waitFor('document.querySelector(".round-count")?.innerText.includes("12 reviewed")')
  const rounds = events(config.directory).filter(e => e.evidence?.includes('"kind": "visual-quiz"'))
  assert(rounds.length === 12, 'exactly twelve review events saved')
  assert(rounds.filter(e => e.new === 'disputed').length === 3, 'mismatch and unsure stay unresolved')
  assert(rounds.filter(e => e.new === 'reviewed').length === 9, 'only visible matching crops are confirmed')
  console.log('PASS visual round saves all answers atomically and advances')

  await browser.send('Page.reload')
  await browser.waitFor(roundReady)
  assert(await browser.evaluate('document.querySelector(".undo-round") !== null'), 'last round retained after reload')
  await click('.undo-round')
  await browser.waitFor('document.querySelector(".undo-round") === null')
  const undone = events(config.directory).filter(e => e.evidence?.startsWith('undo of '))
  assert(undone.length === 12, 'whole round withdrawn in journal')
  console.log('PASS reload persistence and durable round undo')

  await browser.waitFor(roundReady)
  await click('.bulk-toggle')
  assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.wrong").length === 12'), 'flag all')
  await click('.bulk-toggle')
  assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.wrong").length === 0'), 'clear bulk selection')
  await click('.quiz-tile:first-child .quiz-choice')
  const lastImage = await browser.evaluate('document.querySelector(".quiz-tile:last-child img").getAttribute("src")')
  const lastId = decodeURIComponent(lastImage.split('/characters/')[1].split('/image')[0])
  const mark = events(config.directory).length
  await fetch(service.base + '/reviews', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ target_type:'unit', target_id:lastId, field:'note', new:'Concurrent edit', client_id:'other' }) })
  await click('.quiz-submit .primary')
  await browser.waitFor('document.querySelector(".quiz-workspace .error-message")?.innerText.includes("round changed")')
  assert(events(config.directory).length === mark + 1, 'stale round has no partial saves')
  assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.wrong").length === 1'), 'stale round keeps choices')
  console.log('PASS stale round preserves choices and writes no partial answers')

  await browser.setViewport(390, 844)
  await browser.screenshot(join(screenshots, 'quiz-mobile.png'))
  assert(await browser.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'quiz mobile overflow')
  await route('#/', 'document.querySelectorAll(".glyph-tile").length > 0')
  await browser.screenshot(join(screenshots, 'explore-mobile.png'))
  assert(await browser.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'homepage mobile overflow')
  console.log('PASS mobile collection and review layouts')

  await browser.send('Network.enable')
  await browser.send('Network.setBlockedURLs', { urls: ['*/atlas/characters/*/image*'] })
  await browser.send('Network.setCacheDisabled', { cacheDisabled: true })
  await route('#/review?reading=い', 'document.querySelectorAll(".quiz-tile.unavailable").length > 0')
  assert(await browser.evaluate('document.querySelector(".quiz-submit .primary").disabled'), 'unseen crops can be confirmed')
  console.log('PASS failed images cannot be marked reviewed')
  assert(errors.length === 0, 'browser exceptions: ' + errors.join(', '))
  console.log('PASS no browser exceptions')
} catch (error) { if (browser) { console.log('Failure detail:', await browser.evaluate('Array.from(document.querySelectorAll(".error-message")).map(e=>e.innerText).join("; ")')); await browser.screenshot(join(screenshots, 'failure.png')) } throw error } finally { await browser?.close(); service.stop() }
