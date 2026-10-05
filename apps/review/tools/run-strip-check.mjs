#!/usr/bin/env bun
/**
 * The frequent sequences above Explore's collection and beside a character's gallery: the page renders
 * them with its first byte, a bounded number at a time, and their crops arrive into cards that keep
 * their size, so nothing below them moves.
 *
 * Each run's occurrence (`/atlas/runs`) is held back for a while; the strip's cards and the gallery
 * below are read before and after the crops arrive and must not move. The strip draws six cards and
 * twelve more runs as text, and "Show more" adds twelve.
 *
 * Against a running page server, such as `vite preview` with ATLAS_REVIEW_API=https://glyphatlas.org:
 *   bun apps/review/tools/run-strip-check.mjs --server http://127.0.0.1:4173 [--character /en/character/U+3082]
 */
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'

const config = options()
const argv = process.argv.slice(2)
const value = (name, fallback) => argv.includes(name) ? argv[argv.indexOf(name) + 1] : fallback
const HOLD = Number(value('--hold', 3000)), CHARACTER = value('--character', '/en/character/U+3082')
const service = await boot(config)
let browser, failures = 0
const check = (ok, message) => { console.log(`${ok ? 'PASS' : 'FAIL'} ${message}`); if (!ok) failures += 1 }
const boxes = `(() => [...document.querySelectorAll('.run-strip li, .glyph-grid')].map(el => { const r = el.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top + scrollY), Math.round(r.width), Math.round(r.height)].join(',') }))()`

try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*/atlas/runs?*', requestStage: 'Request' }] })
  browser.listeners.push(m => { if (m.method === 'Fetch.requestPaused') setTimeout(() => browser.send('Fetch.continueRequest', { requestId: m.params.requestId }).catch(() => {}), HOLD) })
  for (const [name, width, height] of [['desktop', 1440, 1000], ['phone', 390, 844]]) {
    await browser.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 500 })
    for (const path of ['/en', CHARACTER]) {
      await browser.goto(service.base + path, { waitFor: 'document.querySelector(".run-strip") !== null', timeout: 60000 })
      const before = await browser.evaluate(boxes)
      check(await browser.evaluate('document.querySelectorAll(".run-strip svg.run-image").length') === 0, `${name} ${path}: the strip is there before any occurrence arrives`)
      await browser.waitFor('document.querySelectorAll(".run-strip svg.run-image").length > 0', HOLD + 30000)
      await new Promise(r => setTimeout(r, 1500))
      const after = await browser.evaluate(boxes)
      check(JSON.stringify(before) === JSON.stringify(after), `${name} ${path}: cards and gallery keep their place as ${await browser.evaluate('document.querySelectorAll(".run-strip svg.run-image").length')} occurrences arrive`)
      check(await browser.evaluate('document.documentElement.scrollWidth <= innerWidth'), `${name} ${path}: no sideways scroll`)
      if (path === '/en') {
        const counts = () => browser.evaluate('[document.querySelectorAll(".run-cards li").length, document.querySelectorAll(".run-chips li a").length]')
        const [cards, chips] = await counts()
        check(cards === 6 && chips === 12, `${name}: six cards and twelve runs as text (${cards}, ${chips})`)
        await browser.evaluate('document.querySelector(".run-more").click()')
        await browser.waitFor('document.querySelectorAll(".run-chips li a").length > 12')
        check((await counts())[1] === 24, `${name}: "Show more" adds twelve`)
      }
    }
  }
} finally {
  await browser?.close()
  await service.stop?.()
}
console.log(failures ? `${failures} failed` : 'run strip check passed')
process.exit(failures ? 1 : 0)
