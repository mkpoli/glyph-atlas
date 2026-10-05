#!/usr/bin/env bun
/**
 * A crop's place is painted in its paper colour, at its final size, before its image arrives.
 *
 * Every crop image is held back for a while on each page, and the boxes that hold crops are sampled
 * throughout: a box still waiting for its image must already paint a colour (its record's `tone`, or
 * the theme's `--crop-paper`), never nothing and never white unless its record names white, and must
 * keep its width and height from the first sample to the last, after its image has come.
 *
 * Against the disposable dataset (needs a build of apps/review):
 *   bun apps/review/tools/crop-paint-check.mjs
 * Against a running page server, such as `vite preview` with ATLAS_REVIEW_API=https://glyphatlas.org:
 *   bun apps/review/tools/crop-paint-check.mjs --server http://127.0.0.1:4173 --pages /en,/en/review
 */
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'

const config = options()
const argv = process.argv.slice(2)
const pages = argv.includes('--pages') ? argv[argv.indexOf('--pages') + 1].split(',') : ['/en', '/en/review']
const HOLD = Number(argv.includes('--hold') ? argv[argv.indexOf('--hold') + 1] : 6000)
const service = await boot(config)
let browser, failures = 0

// The crop images: the site's own display crops, and corpus glyphs a holder serves through IIIF.
const isCrop = url => /\/atlas\/media\/[0-9a-f]{64}\.webp|\/0\/default\.jpg|\/iiif\//.test(url)

// Every box that holds a crop image in view: the crop's box (`.crop-paint`), an image drawn without one,
// and a crop drawn on a run's page (an SVG image over its paper, `.run-paper`). For each, its size, what
// it paints, and whether its image is in. A crop's context, the page around it, is drawn hidden until it
// is in, or inside a box that paints, and is not counted.
const CONTEXT = '.context-photo, .page-photo, .line-clip img'
const probe = `(() => {
  const CONTEXT = ${JSON.stringify(CONTEXT)}
  const crop = url => /\\/atlas\\/media\\/|\\/iiif\\/|\\/0\\/default\\.jpg/.test(url ?? '')
  const fetched = url => performance.getEntriesByName(new URL(url, location.href).href).some(e => e.responseEnd > 0)
  const boxes = [...document.querySelectorAll('.crop-paint, img, svg image')].filter(el => el.matches('.crop-paint') ||
    (el.matches('img') && !el.closest('.crop-paint') && !el.matches(CONTEXT) && crop(el.getAttribute('src'))) ||
    (el.matches('svg image') && crop(el.getAttribute('href'))))
  return boxes.flatMap(el => {
    const r = el.getBoundingClientRect()
    if (r.bottom < 0 || r.top > innerHeight || r.width === 0 && r.height === 0 && !el.matches('.crop-paint')) return []
    if (!el.dataset.paintCheck) el.dataset.paintCheck = String(Math.random()).slice(2)
    const base = { key: el.dataset.paintCheck, what: el.className.baseVal ?? el.className ?? el.tagName, w: r.width, h: r.height }
    if (el.matches('svg image')) {
      const paper = el.previousElementSibling?.matches('.run-paper') ? el.previousElementSibling : null
      return [{ ...base, what: 'run image', paint: paper ? getComputedStyle(paper).fill : 'transparent',
        tone: paper?.style.getPropertyValue('--tone').trim() || null, loaded: fetched(el.getAttribute('href')) }]
    }
    const image = el.matches('img') ? el : el.querySelector('img')
    return [{ ...base, paint: getComputedStyle(el).backgroundColor, tone: el.style.getPropertyValue('--tone').trim() || null,
      loaded: Boolean(image?.complete && image.naturalWidth) }]
  })
})()`

const transparent = paint => !paint || paint === 'none' || paint === 'transparent' || /^rgba\(.*,\s*0\)$/.test(paint)
const white = paint => /^rgba?\(255, 255, 255(, 1)?\)$/.test(paint)

async function hold(page) {
  await page.send('Fetch.enable', { patterns: [{ urlPattern: '*' }] })
  page.listeners.push(async m => {
    if (m.method !== 'Fetch.requestPaused') return
    if (isCrop(m.params.request.url)) await Bun.sleep(HOLD)
    page.send('Fetch.continueRequest', { requestId: m.params.requestId }).catch(() => {})
  })
}

async function check(path, scheme) {
  browser = await Browser.launch({ width: 1280, height: 900 })
  await browser.setColorScheme(scheme)
  await hold(browser)
  browser.send('Page.navigate', { url: service.base + path }).catch(() => {})
  const seen = new Map(), problems = []
  const deadline = Date.now() + HOLD * 3 + 30000
  let quiet = 0
  while (Date.now() < deadline) {
    const boxes = await browser.evaluate(probe).catch(() => [])
    for (const box of boxes) {
      const first = seen.get(box.key)
      if (!first) seen.set(box.key, { ...box, waited: !box.loaded })
      else {
        if (!box.loaded) first.waited = true
        if (Math.abs(box.w - first.w) > 1 || Math.abs(box.h - first.h) > 1)
          problems.push(`a crop box changed size from ${Math.round(first.w)}×${Math.round(first.h)} to ${Math.round(box.w)}×${Math.round(box.h)}${box.loaded ? ' when its image came' : ''}`)
      }
      if (!box.loaded) {
        if (transparent(box.paint)) problems.push(`a crop box (${box.what}) painted nothing before its image came`)
        else if (white(box.paint) && box.tone?.toLowerCase() !== '#ffffff') problems.push('a crop box painted white before its image came')
      }
    }
    const waiting = boxes.filter(b => !b.loaded).length
    quiet = boxes.length && !waiting ? quiet + 1 : 0
    if (quiet > 10) break
    await Bun.sleep(50)
  }
  await browser.close(); browser = null
  const waited = [...seen.values()].filter(b => b.waited).length
  const unique = [...new Set(problems)]
  if (!waited) { failures++; console.error(`FAIL ${path} (${scheme}): no crop was caught before its image came`) }
  else if (unique.length) { failures++; console.error(`FAIL ${path} (${scheme}): ${unique.slice(0, 4).join('; ')}`) }
  else console.log(`ok   ${path} (${scheme}): ${waited} crop boxes painted and steady before their images came`)
}

try {
  for (const path of pages) for (const scheme of ['light', 'dark']) await check(path, scheme)
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
if (failures) { console.error(`${failures} page(s) showed a crop box unpainted or resized while it loaded`); process.exit(1) }
console.log('every crop box was painted at its final size before its image came')
