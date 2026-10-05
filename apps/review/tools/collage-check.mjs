#!/usr/bin/env bun
/**
 * Explore's collage: every tile holds its final box from the first paint, the layout toggle works
 * and is remembered, and the page stays light on a slow machine.
 *
 * Crop images are held back for a while; the tiles' boxes are read before and after they arrive and
 * must not move, and the page's layout shifts must add up to nothing. Within a column, tiles follow
 * each other in the document's order, so the keyboard walks the collage as it reads; no two overlap,
 * and the page never scrolls sideways. At a phone's width the collage has two columns. The toggle turns
 * the collage into the grid of squares and back, and a reload opens the layout chosen last. With the
 * processor slowed fourfold, no task on opening the page (after the fonts are warm), loading the next
 * page of tiles or switching the layout may run past LONG_TASK milliseconds.
 *
 * Against a running page server, such as `vite preview` with ATLAS_REVIEW_API=https://glyphatlas.org:
 *   bun apps/review/tools/collage-check.mjs --server http://127.0.0.1:4173
 * Against the disposable dataset (needs a build of apps/review):
 *   bun apps/review/tools/collage-check.mjs
 */
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'

const config = options()
const argv = process.argv.slice(2)
const value = (name, fallback) => argv.includes(name) ? argv[argv.indexOf(name) + 1] : fallback
const HOLD = Number(value('--hold', 4000)), LONG_TASK = Number(value('--long-task', 200)), PAGE = value('--page', '/en')
const service = await boot(config)
const isCrop = url => /\/atlas\/media\/[0-9a-f]{64}\.webp|\/0\/default\.jpg|\/iiif\//.test(url)
let browser, failures = 0
const check = (ok, message) => { console.log(`${ok ? 'PASS' : 'FAIL'} ${message}`); if (!ok) failures += 1 }

// Every tile's box, in the document's order, relative to the gallery.
const boxes = `(() => { const grid = document.querySelector('.glyph-grid').getBoundingClientRect()
  return [...document.querySelectorAll('.glyph-grid .collage-cell')].map(el => { const r = el.getBoundingClientRect()
    return { id: el.dataset.unit ?? el.dataset.corpus ?? '', x: Math.round(r.left - grid.left), y: Math.round(r.top - grid.top), w: Math.round(r.width), h: Math.round(r.height) } }) })()`
const shifts = `(() => { window.__shift = 0; new PerformanceObserver(list => { for (const e of list.getEntries()) if (!e.hadRecentInput) window.__shift += e.value }).observe({ type: 'layout-shift', buffered: true }) })()`
const longest = `(() => new Promise(done => { let most = 0; new PerformanceObserver(list => { for (const e of list.getEntries()) most = Math.max(most, e.duration) }).observe({ type: 'longtask', buffered: true }); setTimeout(() => done(Math.round(most)), 50) }))()`

function arranged(tiles, label) {
  const overlap = tiles.some((a, i) => tiles.some((b, j) => j > i && a.x < b.x + b.w - 1 && b.x < a.x + a.w - 1 && a.y < b.y + b.h - 1 && b.y < a.y + a.h - 1))
  check(!overlap, `${label}: no two tiles overlap`)
  const columns = new Map()
  for (const tile of tiles) columns.set(tile.x, [...(columns.get(tile.x) ?? []), tile.y])
  check([...columns.values()].every(ys => ys.every((y, i) => !i || y > ys[i - 1])), `${label}: each column follows the document's order (${columns.size} columns)`)
  return columns.size
}

try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  await browser.send('Page.addScriptToEvaluateOnNewDocument', { source: shifts })
  for (const [name, width, height] of [['desktop', 1440, 1000], ['phone', 390, 844]]) {
    await browser.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 500 })
    // Crop images wait HOLD ms; everything else passes.
    await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] })
    const held = m => { if (m.method !== 'Fetch.requestPaused') return
      const go = () => browser.send('Fetch.continueRequest', { requestId: m.params.requestId }).catch(() => {})
      if (m.params.resourceType === 'Image' && isCrop(m.params.request.url)) setTimeout(go, HOLD); else go() }
    browser.listeners.push(held)
    await browser.goto(service.base + PAGE, { waitFor: 'document.querySelectorAll(".glyph-grid .collage-cell").length > 0', timeout: 60000 })
    const before = await browser.evaluate(boxes)
    const waiting = await browser.evaluate(`[...document.querySelectorAll('.glyph-grid .crop-paint img')].filter(i => !i.complete).length`)
    await browser.waitFor(`[...document.querySelectorAll('.glyph-grid .crop-paint img')].slice(0, 16).every(i => i.complete)`, HOLD + 30000)
    await new Promise(r => setTimeout(r, 500))
    const after = await browser.evaluate(boxes)
    const moved = before.filter((box, i) => JSON.stringify(box) !== JSON.stringify(after[i]))
    check(waiting > 0 && !moved.length, `${name}: ${before.length} tiles keep their boxes while ${waiting} images load${moved.length ? ` (moved: ${moved.slice(0, 3).map(b => b.id).join(', ')})` : ''}`)
    const shift = await browser.evaluate('window.__shift ?? -1')
    check(shift >= 0 && shift < 0.01, `${name}: layout shift ${shift.toFixed(4)}`)
    const columns = arranged(after, name)
    if (width < 500) check(columns === 2, `${name}: two columns at ${width} px`)
    check(await browser.evaluate('document.documentElement.scrollWidth <= innerWidth'), `${name}: no sideways scroll`)
    browser.listeners.splice(browser.listeners.indexOf(held), 1)
    await browser.send('Fetch.disable')
  }

  // The toggle, remembered across a reload; then back to the collage.
  await browser.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false })
  await browser.goto(service.base + PAGE, { waitFor: 'document.querySelectorAll(".glyph-grid .collage-cell").length > 0', timeout: 60000 })
  const press = async label => { const p = await browser.evaluate(`(() => { const b = [...document.querySelectorAll('.layout-toggle button')][${label === 'grid' ? 1 : 0}]; const r = b.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 } })()`); await browser.click(p.x, p.y) }
  await press('grid')
  await browser.waitFor('document.documentElement.dataset.gallery === "grid"')
  check(await browser.evaluate('getComputedStyle(document.querySelector(".glyph-grid")).display === "grid" && new Set([...document.querySelectorAll(".glyph-grid .collage-cell")].slice(0, 16).map(t => Math.round(t.getBoundingClientRect().height))).size === 1'), 'the toggle lays the tiles out as a grid of squares')
  check(await browser.evaluate('localStorage.getItem("atlas.galleryLayout")') === '"grid"', 'the choice is remembered')
  await browser.send('Page.reload', {})
  await new Promise(r => setTimeout(r, 300))
  await browser.waitFor('document.querySelectorAll(".glyph-grid .collage-cell").length > 0', 60000)
  check(await browser.evaluate('document.documentElement.dataset.gallery') === 'grid', 'a reload opens the grid chosen last, before the page hydrates')
  await browser.waitFor('document.documentElement.dataset.hydrated !== undefined', 60000)
  check(await browser.evaluate('document.querySelector(".layout-toggle button[aria-pressed=true]").textContent') === await browser.evaluate('document.querySelectorAll(".layout-toggle button")[1].textContent'), 'the toggle shows the grid as chosen')
  await press('collage')
  await browser.waitFor('document.documentElement.dataset.gallery === "collage"')
  check(await browser.evaluate('getComputedStyle(document.querySelector(".glyph-grid")).display === "block"'), 'the toggle turns the grid back into the collage')

  // A slow machine: four times slower, with the fonts already warm from the loads above.
  await browser.send('Emulation.setCPUThrottlingRate', { rate: 4 })
  await browser.goto(service.base + PAGE, { waitFor: 'document.querySelectorAll(".glyph-grid .collage-cell").length > 0', timeout: 120000 })
  await new Promise(r => setTimeout(r, 1500))
  const opening = await browser.evaluate(longest)
  check(opening <= LONG_TASK, `opening the page at 4× slower: longest task ${opening} ms`)
  await browser.evaluate('window.__mark = performance.now()')
  const count = await browser.evaluate('document.querySelectorAll(".glyph-grid .collage-cell").length')
  await browser.evaluate('document.querySelector(".scroll-sentinel").scrollIntoView()')
  await browser.waitFor(`document.querySelectorAll(".glyph-grid .collage-cell").length > ${count}`, 120000).catch(() => {})
  await new Promise(r => setTimeout(r, 1500))
  const paging = await browser.evaluate(`(() => new Promise(done => { let most = 0; new PerformanceObserver(list => { for (const e of list.getEntries()) if (e.startTime > window.__mark) most = Math.max(most, e.duration) }).observe({ type: 'longtask', buffered: true }); setTimeout(() => done(Math.round(most)), 50) }))()`)
  check(paging <= LONG_TASK, `the next page of tiles at 4× slower: longest task ${paging} ms (${count} → ${await browser.evaluate('document.querySelectorAll(".glyph-grid .collage-cell").length')} tiles)`)
  await browser.evaluate('scrollTo(0, 0); window.__mark = performance.now()')
  await press('grid'); await new Promise(r => setTimeout(r, 800)); await press('collage'); await new Promise(r => setTimeout(r, 800))
  const toggling = await browser.evaluate(`(() => new Promise(done => { let most = 0; new PerformanceObserver(list => { for (const e of list.getEntries()) if (e.startTime > window.__mark) most = Math.max(most, e.duration) }).observe({ type: 'longtask', buffered: true }); setTimeout(() => done(Math.round(most)), 50) }))()`)
  check(toggling <= LONG_TASK, `switching the layout at 4× slower: longest task ${toggling} ms`)
} finally {
  await browser?.close()
  await service.stop?.()
}
console.log(failures ? `${failures} failed` : 'collage check passed')
process.exit(failures ? 1 : 0)
