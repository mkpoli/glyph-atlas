#!/usr/bin/env bun
/**
 * Seen crops, in a real browser, against a disposable dataset.
 *
 * Moving on from a round records the crops the reader was shown and left unflagged as `seen`, so
 * they are not dealt again. A crop counts as shown only once at least half of it has been on screen:
 * rounds load more crops than fit, and further batches load as the reader scrolls, so a crop below
 * the fold was never looked at and must stay pending.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/seen-check.mjs
 */
import Browser from './browser.mjs'
import { boot, events, options } from './harness.mjs'

const config = options()
const service = await boot(config)
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const tiles = `[...document.querySelectorAll('.quiz-tile')].map(tile => {
  const r = tile.getBoundingClientRect(), shown = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0))
  return { id: tile.dataset.unit, half: shown >= r.height / 2 }
})`
const loadedTiles = `[...document.querySelectorAll('.quiz-tile img')].length > 0 && [...document.querySelectorAll('.quiz-tile img')].every(i => i.complete && i.naturalWidth > 0)`
const seenIds = () => events(config.directory).filter(e => e.field === 'seen' && e.new).map(e => e.target_id)
const passRound = async () => {
  await browser.waitFor(`document.querySelector('.next-round') && !document.querySelector('.next-round').disabled`, 30000)
  const before = seenIds().length
  // A click without scrolling: moving the page would put more crops on screen.
  await browser.evaluate(`document.querySelector('.next-round').click()`)
  const deadline = Date.now() + 30000
  while (seenIds().length === before && Date.now() < deadline) await Bun.sleep(100)
  return seenIds().slice(before)
}

try {
  browser = await Browser.launch({ width: 1200, height: 700 })
  await browser.goto(`${service.base}/#/review`)
  await browser.waitFor(loadedTiles, 60000)
  await Bun.sleep(300)
  const first = await browser.evaluate(tiles)
  const onScreen = first.filter(t => t.half).map(t => t.id), below = first.filter(t => !t.half).map(t => t.id)
  assert(onScreen.length > 0 && below.length > 0, `the round needs crops both on and below the screen (${onScreen.length}/${below.length})`)
  const recorded = await passRound()
  assert(recorded.length === onScreen.length && onScreen.every(id => recorded.includes(id)),
    `the crops on screen are seen (${recorded.length} recorded, ${onScreen.length} on screen)`)
  assert(!below.some(id => recorded.includes(id)), 'no crop below the fold is recorded as seen')
  const pending = new Set()
  for (let offset = 0; ; offset += 96) {
    const page = await (await fetch(`${service.base}/atlas?purpose=review&state=pending&production=all&limit=96&offset=${offset}`)).json()
    page.items.forEach(item => pending.add(item.id))
    if (offset + 96 >= page.total) break
  }
  assert(below.every(id => pending.has(id)), 'crops below the fold stay pending')
  console.log(`ok   off-screen crops stay pending: ${onScreen.length} seen, ${below.length} below the fold not sent`)

  // A reader who scrolls through the whole round has seen all of it.
  await browser.waitFor(loadedTiles, 60000)
  const height = await browser.evaluate('document.documentElement.scrollHeight')
  for (let y = 0; y <= height; y += 300) { await browser.evaluate(`scrollTo(0, ${y})`); await Bun.sleep(80) }
  await Bun.sleep(300)
  const second = await browser.evaluate(`[...document.querySelectorAll('.quiz-tile')].filter(t => t.querySelector('img')?.complete).map(t => t.dataset.unit)`)
  const all = await passRound()
  assert(second.every(id => all.includes(id)), `a scrolled-through round is seen in full (${all.length} of ${second.length})`)
  console.log(`ok   a scrolled-through round is seen in full: ${all.length} crops`)
  console.log('\nseen-check passed')
} finally {
  await browser?.close()
  await service.stop?.()
}
