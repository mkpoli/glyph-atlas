#!/usr/bin/env bun
/**
 * The display queue, in a real browser against a disposable fixture.
 *
 * Checks that the crops are shown in the colours the scan holds, that B&W is a choice which is
 * remembered, that the reviewer shows the character and the strokes around it together with the crop
 * marked in the right place, that the suggestion area takes the keyboard after an issue is chosen,
 * that Enter on a candidate chooses it instead of saving the round, and that the export is a file the
 * browser really saves.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/display-check.mjs
 */
import { readdirSync, readFileSync, rmSync, mkdirSync } from 'node:fs'
import Browser from './browser.mjs'
import { boot, options, events } from './harness.mjs'

const config = options()
if (config.external) throw new Error('This check creates a review; use its disposable fixture.')
const service = await boot(config)
const downloads = '/tmp/atlas-display-downloads'
rmSync(downloads, { recursive: true, force: true })
mkdirSync(downloads, { recursive: true })
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const sleep = ms => Bun.sleep(ms)

/** The filter the browser actually renders for one image, and how many are on screen. */
const filtersOf = selector => `[...document.querySelectorAll(${JSON.stringify(selector)})]
  .map(image => getComputedStyle(image).filter)`

try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })
  await browser.send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: downloads, eventsEnabled: true })
  const downloadEvents = []
  browser.listeners.push(m => { if (m.method?.startsWith('Browser.download')) downloadEvents.push(m.method) })

  // The fixture ships no OCR model, so candidates are stubbed: this check is about the interaction,
  // and the real model is exercised by the suggestion smoke check.
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*/suggestions?*' }] })
  browser.listeners.push(m => { if (m.method === 'Fetch.requestPaused') browser.send('Fetch.fulfillRequest', {
    requestId: m.params.requestId, responseCode: 200,
    responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
    body: Buffer.from(JSON.stringify({ status: 'ready', candidates: [
      { text: 'ヌ', engine: 'NDLkotenOCR', score: 0.9 },
      { text: 'メ', engine: 'fixture classifier', score: 0.4 },
    ] })).toString('base64'),
  }) })

  await browser.goto(`${service.base}/`, { waitFor: `document.querySelector('.glyph-tile .glyph-image')?.naturalWidth > 0` })

  // 1. Original colour by default, and actually coloured.
  assert(await browser.evaluate(`document.documentElement.dataset.ink`) === 'original', 'Original is the default mode')
  const exploreFilters = await browser.evaluate(filtersOf('.glyph-image'))
  assert(exploreFilters.length > 0, 'the collection drew some crops')
  assert(exploreFilters.every(f => f === 'none'),
    `every crop is drawn in the scan's own colour: ${JSON.stringify(exploreFilters.slice(0, 3))}`)
  await browser.screenshot('/tmp/atlas-display-original.png')

  // 2. B&W is available, takes effect, and is remembered.
  await browser.evaluate(`[...document.querySelectorAll('.ink-toggle button')].find(b => b.textContent === 'B&W').click()`)
  await browser.waitFor(`document.documentElement.dataset.ink === 'bw'`)
  const greyFilters = await browser.evaluate(filtersOf('.glyph-image'))
  assert(greyFilters.length > 0 && greyFilters.every(f => /grayscale/.test(f)),
    `B&W filters every crop: ${JSON.stringify(greyFilters.slice(0, 3))}`)
  await browser.screenshot('/tmp/atlas-display-bw.png')
  await browser.goto(`${service.base}/`, { waitFor: `document.querySelector('.glyph-tile') !== null` })
  assert(await browser.evaluate(`document.documentElement.dataset.ink`) === 'bw', 'the choice survives a reload')
  await browser.evaluate(`[...document.querySelectorAll('.ink-toggle button')].find(b => b.textContent === 'Original').click()`)
  await browser.waitFor(`document.documentElement.dataset.ink === 'original'`)
  const backToColour = await browser.evaluate(filtersOf('.glyph-image'))
  assert(backToColour.every(f => f === 'none'), 'switching back restores the colour everywhere')

  // 3. A single viewer keeps the crop clear inside the original colour photograph.
  const unit = service.fixture.unit
  await browser.evaluate(`visit('/character/' + encodeURIComponent(${JSON.stringify(unit)}))`)
  await browser.waitFor(`document.querySelector('dialog[open] .crop-viewport')?.dataset.ready === 'true'`)
  assert(await browser.evaluate(`document.querySelectorAll('.inspector-tabs, .inspector-crop, .nearby').length`) === 0, 'duplicate crop/context views remain')
  const reviewSource = await (await fetch(service.base + '/atlas/characters/' + encodeURIComponent(unit))).json()
  const geometry = await browser.evaluate(`(() => {
    const view = document.querySelector('dialog[open] .crop-viewport')
    const image = [...view.querySelectorAll('.crop-plane img')].find(i => getComputedStyle(i).visibility === 'visible')
    const mask = view.querySelector('.crop-mask')
    const i = image.getBoundingClientRect(), m = mask.getBoundingClientRect()
    const source = ${JSON.stringify(reviewSource)}
    const bounds = image.classList.contains('page-photo')
      ? { x: 0, y: 0, w: image.naturalWidth, h: image.naturalHeight } : source.context_box
    const crop = source.crop_box, scale = i.width / bounds.w
    return { positive: m.width > 0 && m.height > 0,
      leftError: Math.abs(m.left - (i.left + (crop.x - bounds.x) * scale)),
      topError: Math.abs(m.top - (i.top + (crop.y - bounds.y) * scale)),
      widthError: Math.abs(m.width - crop.w * scale), heightError: Math.abs(m.height - crop.h * scale),
      border: getComputedStyle(mask).borderWidth, filter: getComputedStyle(image).filter,
      shaded: !!view.querySelector('.context-shade') }
  })()`)
  assert(geometry.positive && geometry.shaded, 'crop opening or surrounding shade missing')
  assert(geometry.border === '0px' && geometry.filter === 'none', 'crop has a frame or photograph is filtered')
  assert(['leftError', 'topError', 'widthError', 'heightError'].every(key => geometry[key] < 1),
    `crop opening does not align with source geometry: ${JSON.stringify(geometry)}`)
  const view = await browser.centre('.crop-viewport')
  await browser.drag({ x: view.x, y: view.y }, { x: view.x + 30, y: view.y + 20 })
  assert(await browser.evaluate(`Math.abs(Number(document.querySelector('.crop-viewport').dataset.panX)) > 0`), 'drag did not pan the context')
  await browser.evaluate(`document.querySelector('.reset-crop').click()`)
  assert(await browser.evaluate(`document.querySelector('.crop-viewport').dataset.panX === '0' && document.querySelector('.crop-viewport').dataset.panY === '0'`), 'reset did not return to the crop')
  const resetGeometry = await browser.evaluate(`(() => {
    const viewport = document.querySelector('.crop-viewport'), mask = viewport.querySelector('.crop-mask')
    const v = viewport.getBoundingClientRect(), m = mask.getBoundingClientRect()
    return { scrollLeft: viewport.scrollLeft, scrollTop: viewport.scrollTop,
      centerX: (m.left + m.right - v.left - v.right) / 2,
      centerY: (m.top + m.bottom - v.top - v.bottom) / 2 }
  })()`)
  assert(resetGeometry.scrollLeft === 0 && resetGeometry.scrollTop === 0
    && Math.abs(resetGeometry.centerX) < 1 && Math.abs(resetGeometry.centerY) < 1,
    `native scrolling moved the crop away from viewport center: ${JSON.stringify(resetGeometry)}`)
  await browser.screenshot('/tmp/atlas-display-context.png')

  // 4. Choosing an issue that offers suggestions puts the keyboard in them.
  await browser.evaluate(`document.querySelector('.issue-card').click()`)
  await browser.waitFor(`document.querySelector('.reading-suggestions') !== null`)
  const focused = await browser.evaluate(`(() => {
    const active = document.activeElement
    const area = document.querySelector('.reading-suggestions')
    return { inArea: area.contains(active), tag: active.tagName, text: (active.textContent || '').slice(0, 20),
             hasOptions: !!document.querySelector('.suggestion-options button') }
  })()`)
  assert(focused.inArea, `focus is in the suggestion area: ${JSON.stringify(focused)}`)
  assert(focused.tag === 'BUTTON' || focused.tag === 'DIV', `focus is on a target, not the dialog: ${focused.tag}`)
  await browser.screenshot('/tmp/atlas-display-suggestions.png')

  // 5. Enter on a candidate chooses it and does not save the round.
  if (focused.hasOptions) {
    const before = events(service.fixture.directory).length
    const chosen = await browser.evaluate(`(() => {
      const button = document.querySelector('.suggestion-options button')
      button.focus(); return button.textContent
    })()`)
    await browser.key('Enter')
    await sleep(700)
    const after = events(service.fixture.directory).length
    assert(after === before, `choosing a suggestion must not save a review: ${before} -> ${after}`)
    assert(await browser.evaluate(`document.querySelector('dialog[open]') !== null`), 'the reviewer stays open')
    assert(await browser.evaluate(`!!document.querySelector('.suggestion-options button.chosen')`),
      `the candidate ${JSON.stringify(chosen)} was chosen rather than the round saved`)
  } else {
    throw new Error('the suggestion area offered no candidates to activate')
  }
  await browser.evaluate(`document.querySelector('.close-inspector').click()`)
  await browser.waitFor(`document.querySelector('dialog[open]') === null`)

  // 6. Export remains usable even when the browser blocks downloading or clipboard access.
  await browser.evaluate(`document.querySelector('.header-menu .icon-button').click()`)
  await browser.waitFor(`document.querySelector('.options-menu button') !== null`)
  await browser.evaluate(`document.querySelector('.options-menu button:last-of-type').click()`)
  await browser.waitFor(`document.querySelector('.export-count') !== null`)
  assert(await browser.evaluate(`document.querySelector('.export-count').textContent`) === 'No new reviews to export.', 'empty feedback has an explicit message')
  assert(!await browser.evaluate(`!!document.querySelector('.export-actions')`), 'empty export has no download action')
  await browser.evaluate(`document.querySelector('[aria-label="Close export"]').click()`)
  // Create one explicit issue in this disposable fixture to exercise the actual download paths.
  const source = await (await fetch(service.base + '/atlas/characters/' + encodeURIComponent(unit))).json()
  const saved = await fetch(service.base + '/atlas/characters/' + encodeURIComponent(unit), {
    method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({id: crypto.randomUUID(), client_id: 'display-check', revision: reviewSource.revision,
      image_sha256: reviewSource.image_sha256, verdict: 'wrong', issue: 'crop'}),
  })
  assert(saved.ok, 'fixture review was saved')
  await browser.evaluate(`document.querySelector('.header-menu .icon-button').click()`)
  await browser.evaluate(`document.querySelector('.options-menu button:last-of-type').click()`)
  await browser.waitFor(`document.querySelector('.export-actions a') !== null`, 15000)
  const visible = JSON.parse(await browser.evaluate(`document.querySelector('[aria-label="Reviews JSON"]').value`))
  assert(visible.kind === 'atlas-character-reviews' && visible.scope === 'unprocessed' && visible.reviews.length === 1, 'the new feedback is readable on screen')
  assert(await browser.evaluate(`document.querySelector('.export-actions a').getAttribute('href') === '/atlas/reviews.json'`),
    'the primary export uses the server attachment, without a temporary blob URL')
  assert(!/Download started/.test(await browser.evaluate('document.body.innerText')), 'no unverified download claim')
  await browser.send('Browser.setDownloadBehavior', { behavior: 'deny', eventsEnabled: true })
  let link = await browser.centre('.export-actions a')
  await browser.click(link.x, link.y)
  await sleep(300)
  assert(readdirSync(downloads).length === 0, 'the test browser blocked the download')
  assert(await browser.evaluate(`document.querySelector('[aria-label="Reviews JSON"]').value.length > 0`), 'blocked download leaves a visible export')
  await browser.evaluate(`Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async () => { throw new Error('denied') } } })`)
  const copy = await browser.centre('.copy-export')
  await browser.click(copy.x, copy.y)
  await browser.waitFor(`document.querySelector('.export-foot [role="status"]').textContent.includes('Ctrl+C')`)
  assert(await browser.evaluate(`(() => { const t = document.querySelector('[aria-label="Reviews JSON"]'); return t.selectionStart === 0 && t.selectionEnd === t.value.length && document.activeElement === t })()`), 'manual copy selects the whole export')
  await browser.send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: downloads, eventsEnabled: true })
  link = await browser.centre('.export-actions a')
  await browser.click(link.x, link.y)
  await sleep(2500)
  const files = readdirSync(downloads)
  assert(files.length === 1, `one downloaded file: ${JSON.stringify(files)}`)
  const body = JSON.parse(readFileSync(`${downloads}/${files[0]}`, 'utf8'))
  assert(body.kind === 'atlas-character-reviews', `the file is the export: ${body.kind}`)
  assert(Array.isArray(body.reviews), 'the file holds a reviews array')
  assert(body.reviews.length === 1, 'the explicit fixture review is included')
  assert(downloadEvents.some(name => name === 'Browser.downloadProgress'), 'the browser reported download progress')

  // The native save path bypasses the download manager and confirms only a completed write.
  await browser.evaluate(`window.showSaveFilePicker = async () => ({ name: 'reviews.json', createWritable: async () => ({
    write: async value => { window.savedExport = value }, close: async () => { window.saveClosed = true }, abort: async () => {},
  }) })`)
  const saveAs = await browser.centre('.save-export')
  await browser.click(saveAs.x, saveAs.y)
  await browser.waitFor(`document.querySelector('.export-foot [role="status"]').textContent === 'Saved reviews.json'`)
  assert(await browser.evaluate(`window.saveClosed && window.savedExport === document.querySelector('[aria-label="Reviews JSON"]').value`), 'native save writes the visible export before confirming')
  await browser.evaluate(`window.showSaveFilePicker = async () => { throw new DOMException('cancelled', 'AbortError') }`)
  await browser.click(saveAs.x, saveAs.y)
  await browser.waitFor(`document.querySelector('.export-foot [role="status"]').textContent === 'Save cancelled'`)
  await browser.evaluate(`window.showSaveFilePicker = async () => ({ name: 'failed.json', createWritable: async () => ({
    write: async () => { throw new Error('write failed') }, abort: async () => { window.saveAborted = true },
  }) })`)
  await browser.click(saveAs.x, saveAs.y)
  await browser.waitFor(`document.querySelector('.export-foot [role="status"]').textContent.startsWith('Could not save.')`)
  assert(await browser.evaluate('window.saveAborted === true'), 'a failed write is aborted')

  assert(errors.length === 0, `page errors: ${errors.join('; ')}`)
  console.log(`display: ${exploreFilters.length} crops unfiltered by default, ${greyFilters.length} grayscale under B&W,`)
  console.log(`         aligned crop opening and pan/reset, focus in the suggestions, Enter chose a candidate,`)
  console.log(`         export saved ${files[0]} (${body.reviews.length} reviews)`)
} finally {
  if (browser) await browser.close()
  await service.stop()
}
