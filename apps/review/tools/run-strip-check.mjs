#!/usr/bin/env bun
/**
 * The frequent sequences above Explore's collection and beside a character's gallery: the page renders
 * them with its first byte as text and counts, twenty-four at a time, and reads no run's occurrences
 * (`/atlas/runs`) to draw them, so nothing below them moves once the page is up.
 *
 * Against a running page server, such as `vite preview` with ATLAS_REVIEW_API=https://glyphatlas.org:
 *   bun apps/review/tools/run-strip-check.mjs --server http://127.0.0.1:4173 [--character /en/character/U+3082]
 */
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'

const config = options()
const argv = process.argv.slice(2)
const value = (name, fallback) => argv.includes(name) ? argv[argv.indexOf(name) + 1] : fallback
const CHARACTER = value('--character', '/en/character/U+3082')
const service = await boot(config)
let browser, failures = 0
const check = (ok, message) => { console.log(`${ok ? 'PASS' : 'FAIL'} ${message}`); if (!ok) failures += 1 }
const boxes = `(() => [...document.querySelectorAll('.run-strip li, .glyph-grid')].map(el => { const r = el.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top + scrollY), Math.round(r.width), Math.round(r.height)].join(',') }))()`

try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  let runReads = 0
  await browser.send('Network.enable')
  browser.listeners.push(m => { if (m.method === 'Network.requestWillBeSent' && m.params.request.url.includes('/atlas/runs?')) runReads += 1 })
  for (const [name, width, height] of [['desktop', 1440, 1000], ['phone', 390, 844]]) {
    await browser.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 500 })
    for (const path of ['/en', CHARACTER]) {
      runReads = 0
      await browser.goto(service.base + path, { waitFor: 'document.querySelector(".run-strip .run-chips a") !== null', timeout: 60000 })
      const before = await browser.evaluate(boxes)
      await new Promise(r => setTimeout(r, 3000))
      check(JSON.stringify(before) === JSON.stringify(await browser.evaluate(boxes)), `${name} ${path}: the strip and gallery keep their place`)
      check(runReads === 0, `${name} ${path}: no run's occurrences are read for the strip (${runReads})`)
      check(await browser.evaluate('document.documentElement.scrollWidth <= innerWidth'), `${name} ${path}: no sideways scroll`)
      if (path === '/en') {
        const chips = () => browser.evaluate('document.querySelectorAll(".run-chips li a").length')
        check(await chips() === 24, `${name}: twenty-four runs at first (${await chips()})`)
        await browser.evaluate('document.querySelector(".run-more").click()')
        await browser.waitFor('document.querySelectorAll(".run-chips li a").length > 24')
        check(await chips() === 48, `${name}: "Show more" adds twenty-four (${await chips()})`)
      }
    }
  }
} finally {
  await browser?.close()
  await service.stop?.()
}
console.log(failures ? `${failures} failed` : 'run strip check passed')
process.exit(failures ? 1 : 0)
