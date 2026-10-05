#!/usr/bin/env bun
/**
 * The crop stands still while its inspector loads, in a real browser on a slow network.
 *
 * The page view is framed on the crop by one rule, and the crop is drawn at that place from the first
 * paint: before the scripts run, while only the listing's row is in, and once the record and then the
 * page photograph around it arrive. Here the scripts, the crop's record and its context photograph are
 * each held back, and the crop's on-screen rectangle is sampled throughout; every sample must match
 * where the loaded view outlines the crop.
 *
 * Against the disposable dataset (needs a build of apps/review):
 *   devrun bun apps/review/tools/crop-steady-check.mjs
 * Against a running page server, such as `vite preview` with ATLAS_REVIEW_API=https://glyphatlas.org:
 *   bun apps/review/tools/crop-steady-check.mjs --server http://127.0.0.1:4173 --ids <id>,<id>
 */
import Browser from './browser.mjs'
import { boot, options, units } from './harness.mjs'

const config = options()
const argv = process.argv.slice(2)
const service = await boot(config)
const HOLD = 2500
const assert = (condition, message) => { if (!condition) throw new Error(message) }
let browser, failures = 0

// The crop as the reader sees it: the outline once the view is ready, before then the crop image,
// whose visible part is its content box when the image is contained in a square.
const probe = `(() => {
  const v = document.querySelector('dialog[open] .crop-viewport') ?? document.querySelector('.crop-viewport')
  if (!v) return null
  const vr = v.getBoundingClientRect()
  const el = v.querySelector('.crop-mask, .box-editor') ?? v.querySelector('.crop-early img') ?? v.querySelector('img:not(.context-photo):not(.page-photo)')
  if (!el || (el.tagName === 'IMG' && !el.naturalWidth)) return { view: [vr.width, vr.height], ready: v.dataset.ready === 'true', crop: null }
  let r = el.getBoundingClientRect(), x = r.x, y = r.y, w = r.width, h = r.height
  if (el.tagName === 'IMG' && getComputedStyle(el).objectFit === 'contain') {
    const k = Math.min(w / el.naturalWidth, h / el.naturalHeight)
    x += (w - el.naturalWidth * k) / 2; y += (h - el.naturalHeight * k) / 2; w = el.naturalWidth * k; h = el.naturalHeight * k
  }
  return { view: [vr.width, vr.height], ready: v.dataset.ready === 'true', hydrated: document.documentElement.dataset.hydrated !== undefined,
    crop: { x: x - vr.x, y: y - vr.y, w, h } }
})()`

/** Hold back the requests `held(url)` names for HOLD ms each; let every other request through. */
async function throttle(held) {
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*' }] })
  browser.listeners.push(async (m) => {
    if (m.method !== 'Fetch.requestPaused') return
    if (held(m.params.request.url)) await Bun.sleep(HOLD)
    browser.send('Fetch.continueRequest', { requestId: m.params.requestId }).catch(() => {})
  })
}

/** Sample the crop until the view is ready and a moment after; return every sample. */
async function watch(start) {
  const samples = []
  let readySince = null
  const deadline = Date.now() + 120000
  await start()
  let sample = null
  while (Date.now() < deadline) {
    sample = await browser.evaluate(probe).catch(error => ({ error: error.message }))
    if (sample?.crop) samples.push(sample)
    if (sample?.ready && readySince === null) readySince = Date.now()
    if (readySince !== null && Date.now() - readySince > 1500) return samples
    await Bun.sleep(40)
  }
  throw new Error('the view never became ready: ' + JSON.stringify(sample))
}

/**
 * Every sample drawn in the final view's size must show the crop where the ready view outlines it. With
 * `shape` false only its centre and longer side are held: before the record comes, a gallery's crop is
 * shaped by its listing row's box, which the local review service gives in other proportions than the
 * box it cuts the crop to.
 */
function steady(name, samples, { shape = true } = {}) {
  const last = samples.at(-1)
  assert(last?.ready, `${name}: the view is not ready`)
  const same = samples.filter(s => s.view[0] === last.view[0] && s.view[1] === last.view[1])
  const early = same.filter(s => !s.ready)
  const measures = c => shape ? c : { cx: c.x + c.w / 2, cy: c.y + c.h / 2, side: Math.max(c.w, c.h) }
  const off = same.find(s => Object.entries(measures(s.crop)).some(([k, v]) => Math.abs(v - measures(last.crop)[k]) > 1.5))
  const round = c => Object.fromEntries(Object.entries(c).map(([k, v]) => [k, Math.round(v * 10) / 10]))
  if (off) {
    failures++
    console.error(`FAIL ${name}: the crop moved from ${JSON.stringify(round(off.crop))} (ready ${off.ready}, hydrated ${off.hydrated}) to ${JSON.stringify(round(last.crop))}`)
  } else {
    console.log(`ok   ${name}: ${early.length} samples before the view was ready, all at ${JSON.stringify(round(last.crop))}`)
  }
  assert(early.length > 0, `${name}: no sample caught the crop before its context loaded; the hold is too short`)
}

try {
  let ids = argv.includes('--ids') ? argv[argv.indexOf('--ids') + 1].split(',') : null
  if (!ids) {
    assert(!config.external, '--server needs --ids')
    const stored = units(config.directory)
    ids = Object.entries(stored).filter(([, unit]) => unit.active && unit.box && unit.page_id === 'doc-1:p1').slice(0, 2).map(([id]) => id)
  }
  assert(ids.length, 'no crops to open')
  const record = async id => (await fetch(`${service.base}/atlas/characters/${encodeURIComponent(id)}`)).json()

  // A crop's own address: the scripts, its record and its context photograph come late.
  for (const id of ids) {
    const { context_image } = await record(id)
    browser = await Browser.launch({ width: 1440, height: 1000 })
    await throttle(url => /\.js(\?|$)/.test(url) || url.endsWith(context_image) || /\/atlas\/characters\/[^/?]+(\?|$)/.test(url))
    steady(`/crop/${id}`, await watch(() => browser.send('Page.navigate', { url: `${service.base}/en/crop/${encodeURIComponent(id)}` })))
    await browser.close(); browser = null
  }

  // The inspector opened from a gallery: the listing's row first, then the record, then the photograph.
  browser = await Browser.launch({ width: 1440, height: 1000 })
  await browser.goto(`${service.base}/en/`, { waitFor: 'document.querySelectorAll(".glyph-tile[data-unit]").length > 0', timeout: 90000 })
  await throttle(url => url.includes('/atlas/characters/') || url.includes('/atlas/media/') || url.includes('/images/'))
  steady('gallery', await watch(() => browser.evaluate('document.querySelector(".glyph-tile[data-unit]").click()')), { shape: Boolean(config.external) })
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
if (failures) { console.error(`${failures} crop view(s) moved while loading`); process.exit(1) }
console.log('the crop stood still in every view')
