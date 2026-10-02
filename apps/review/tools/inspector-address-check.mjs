#!/usr/bin/env bun
/**
 * The inspector's address, in a real browser, against a disposable dataset.
 *
 * Opening a crop from the collection shows the crop's address; stepping replaces it; closing goes
 * back to the collection's address with its scroll, and Back and Forward close and reopen the crop.
 * A crop's address loaded on its own opens the crop over the collection.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/inspector-address-check.mjs
 */
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'

const config = options()
const service = await boot(config)
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const open = 'document.querySelector("dialog[open] .record-id code")?.textContent'
const closed = 'document.querySelector("dialog[open]") === null'
const path = () => browser.evaluate('location.pathname + location.search')

try {
  browser = await Browser.launch({ width: 1280, height: 900 })
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })
  await browser.goto(`${service.base}/en`, { waitFor: 'document.querySelectorAll(".glyph-grid [data-unit]").length > 12', timeout: 90000 })
  await browser.waitFor('document.documentElement.dataset.hydrated !== undefined', 60000)
  const ids = await browser.evaluate('[...document.querySelectorAll(".glyph-grid [data-unit]")].map(t => t.dataset.unit)')
  await browser.evaluate('window.sameCollection = document.querySelector(".glyph-grid")')
  await browser.evaluate('document.querySelector(".glyph-grid [data-unit]:nth-child(10)").scrollIntoView({ block: "center" })')
  const scroll = await browser.evaluate('scrollY'), length = await browser.evaluate('history.length')
  assert(scroll > 0, 'the collection should scroll for this check')

  const first = ids[9]
  const at = await browser.centre(`.glyph-grid [data-unit="${first}"]`)
  await browser.click(at.x, at.y)
  await browser.waitFor(`${open} === ${JSON.stringify(first)}`)
  assert(await path() === `/en/crop/${encodeURIComponent(first)}`, `opening shows the crop's address, not ${await path()}`)
  assert(await browser.evaluate('history.length') === length + 1, 'opening adds one history entry')
  console.log('PASS opening a crop shows its address')

  await browser.evaluate('document.querySelector("dialog[open] .next-character").click()')
  await browser.waitFor(`${open} === ${JSON.stringify(ids[10])}`)
  assert(await path() === `/en/crop/${encodeURIComponent(ids[10])}`, 'stepping shows the next crop\'s address')
  assert(await browser.evaluate('history.length') === length + 1, 'stepping replaces the entry')
  console.log('PASS stepping replaces the address')

  await browser.evaluate('document.querySelector("dialog[open] .close-inspector").click()')
  await browser.waitFor(closed)
  await browser.waitFor('location.pathname === "/en"')
  assert(await browser.evaluate('document.querySelector(".glyph-grid") === window.sameCollection'), 'closing must not remount the collection')
  assert(await browser.evaluate('scrollY') === scroll, `closing keeps the scroll (${scroll}, now ${await browser.evaluate('scrollY')})`)
  assert(await browser.evaluate(`document.activeElement?.dataset.unit`) === first, 'closing returns the focus to the tile that opened it')
  console.log('PASS closing returns to the collection, its scroll and focus')

  await browser.evaluate('history.forward()')
  await browser.waitFor(`${open} === ${JSON.stringify(ids[10])}`)
  assert(await path() === `/en/crop/${encodeURIComponent(ids[10])}`, 'Forward reopens the crop')
  await browser.evaluate('document.querySelector("dialog[open] .previous-character").click()')
  await browser.waitFor(`${open} === ${JSON.stringify(first)}`)
  console.log('PASS Forward reopens the crop with its collection to step through')
  await browser.evaluate('history.back()')
  await browser.waitFor(closed)
  assert(await path() === '/en', 'Back closes the crop')
  assert(await browser.evaluate('document.querySelector(".glyph-grid") === window.sameCollection'), 'Back must not remount the collection')
  console.log('PASS Back closes the crop')

  // A character page keeps its own address under the crop.
  await browser.goto(`${service.base}/en/character/U+3044`, { waitFor: 'document.querySelectorAll("[data-unit]").length > 0', timeout: 90000 })
  await browser.waitFor('document.documentElement.dataset.hydrated !== undefined', 60000)
  const listed = await path()
  await browser.evaluate('document.querySelector("[data-unit]").click()')
  await browser.waitFor(`${open} !== undefined`)
  assert((await path()).startsWith('/en/crop/'), 'a character page opens the crop\'s address')
  await browser.key('Escape')
  await browser.waitFor(closed)
  await browser.waitFor(`location.pathname + location.search === ${JSON.stringify(listed)}`)
  console.log('PASS Escape returns to the character page\'s address')

  await browser.goto(`${service.base}/en/crop/${encodeURIComponent(first)}`, { waitFor: `${open} === ${JSON.stringify(first)}`, timeout: 90000 })
  await browser.waitFor('document.documentElement.dataset.hydrated !== undefined', 60000)
  assert(await browser.evaluate('document.querySelectorAll(".glyph-grid [data-unit]").length > 0'), 'a shared crop opens over its collection')
  await browser.evaluate('document.querySelector("dialog[open] .close-inspector").click()')
  await browser.waitFor(closed)
  assert(await path() === '/en', 'closing a shared crop shows the collection\'s address')
  await browser.evaluate('history.back()')
  await browser.waitFor(`${open} === ${JSON.stringify(first)}`)
  console.log('PASS a shared crop link opens over its collection, and Back reopens it')

  assert(!errors.length, 'page errors: ' + errors.join('; '))
  console.log('PASS inspector address')
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
