#!/usr/bin/env bun
import { join } from 'node:path'
import { rmSync } from 'node:fs'
import Browser from './browser.mjs'
import { boot, options, HERE } from './harness.mjs'

const config = options()
if (config.external) throw new Error('This check writes reviews; use its disposable fixture.')
const root = config.directory
config.directory = join(root, 'collection')
const seed = Bun.spawn([config.python, join(HERE, 'corpus-fixture.py'), config.directory], { stdout: 'pipe', stderr: 'pipe' })
const [out, err, code] = await Promise.all([new Response(seed.stdout).text(), new Response(seed.stderr).text(), seed.exited])
if (code) throw new Error(err)
const fixture = JSON.parse(out.trim().split('\n').pop())
const service = await boot(config)
let browser
const assert = (ok, message) => { if (!ok) throw new Error(message) }
const get = async path => (await fetch(service.base + path)).json()
const detail = id => get('/atlas/corpus/character?' + new URLSearchParams({ id }))
try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  // The source metadata and IIIF rectangles are real API responses; external pixels are a fixture.
  const pixels = await get('/atlas?limit=1') // warm the collection before browser timing
  const imagePath = pixels.items[0].image
  const image = Buffer.from(await (await fetch(service.base + imagePath)).arrayBuffer()).toString('base64')
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*codh.rois.ac.jp/char-shape/iiif/*' }] })
  browser.listeners.push(m => { if (m.method === 'Fetch.requestPaused') browser.send('Fetch.fulfillRequest', {
    requestId: m.params.requestId, responseCode: 200, responseHeaders: [{ name: 'Content-Type', value: 'image/jpeg' }], body: image,
  }) })
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })
  const click = async selector => {
    await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`)
    const p = await browser.centre(selector); await browser.click(p.x, p.y)
  }
  const tile = `[data-corpus="${fixture.reported}"]`
  const exportRows = async () => (await get('/atlas/reviews')).reviews.filter(row => row.origin === 'corpus')
  await browser.goto(service.base + '/en', { waitFor: `document.querySelector(${JSON.stringify(tile)})` })
  const order = await browser.evaluate(`[...document.querySelectorAll('.glyph-tile')].map(x => x.dataset.corpus || x.dataset.unit)`)
  await click(tile)
  await browser.waitFor(`document.querySelector('.corpus-dialog .save-character')?.disabled === false`)
  assert(await browser.evaluate(`location.pathname === '/en' && document.querySelector('dialog').open`), 'tile left the app')
  assert(await browser.evaluate(`document.querySelector('.corpus-source-label').textContent.includes('CODH label 在')`), 'imported label missing')
  await browser.waitFor(`document.querySelector('.corpus-dialog .crop-viewport')?.dataset.ready === 'true'`)
  assert(await browser.evaluate(`!!document.querySelector('.corpus-dialog .crop-mask') && !!document.querySelector('.corpus-dialog .context-shade')`), 'source context has no clear crop opening')
  assert(await browser.evaluate(`!document.querySelector('.corpus-dialog .page-photo, .corpus-dialog .nearby, .corpus-dialog .inspector-crop')`), 'corpus viewport uses a page hash or duplicate display')
  assert(await browser.evaluate(`getComputedStyle(document.querySelector('.corpus-dialog .crop-mask')).borderWidth === '0px'`), 'crop opening has a thick frame')
  assert(await browser.evaluate(`document.querySelector('.corpus-credit a').href.includes('icv-kuzushiji')`), 'source link is a bare image')
  await click('.corpus-dialog [data-issue="reading"]')
  await browser.waitFor(`document.activeElement?.textContent === '有'`)
  await click('.corpus-dialog .suggestion-options button')
  await click('.corpus-dialog .save-character')
  await Bun.sleep(400)
  const saved = await detail(fixture.reported)
  assert(saved.label === '有' && saved.source_label === '在' && saved.state === 'checked', 'correction was not saved')
  assert(JSON.stringify(order) === JSON.stringify(await browser.evaluate(`[...document.querySelectorAll('.glyph-tile')].map(x => x.dataset.corpus || x.dataset.unit)`)), 'save reordered the gallery')
  await browser.waitFor(`!document.querySelector('dialog')`)
  await click(tile)
  await browser.waitFor(`document.querySelector('.corpus-dialog .save-character')?.disabled === false`)
  const beforeSkip = (await exportRows()).length
  await click('.skip-character')
  await Bun.sleep(200)
  assert((await exportRows()).length === beforeSkip, 'Skip wrote a review')
  if (await browser.evaluate(`!!document.querySelector('dialog')`)) await click('.close-inspector')
  await browser.evaluate(`visit('/en/corpus/' + encodeURIComponent(${JSON.stringify(fixture.next)}))`)
  await browser.waitFor(`document.querySelector('.corpus-dialog .save-character')?.disabled === false`)
  await click('.skip-character')
  await browser.waitFor(`!document.querySelector('dialog')`)
  assert((await exportRows()).length === beforeSkip, 'Skip wrote a review for a deep-linked character')
  // Deep link, flag without typing, and a fresh navigation back to the durable queue.
  await browser.evaluate(`visit('/en')`)
  await Bun.sleep(100)
  await browser.evaluate(`visit('/en/corpus/' + encodeURIComponent(${JSON.stringify(fixture.next)}))`)
  await browser.waitFor(`document.querySelector('.corpus-dialog .save-character')?.disabled === false`)
  await browser.setViewport(390, 844)
  await click('.corpus-dialog [data-issue="crop"]')
  await click('.corpus-dialog .save-character')
  await browser.waitFor(`!document.querySelector('dialog')`)
  await browser.evaluate(`visit('/en/flagged')`)
  // The collection page shows the same tile, so wait for the flagged page itself: a click that lands
  // before the navigation finishes opens a dialog the new page then closes.
  await browser.waitFor(`location.pathname === '/en/flagged' && document.querySelector('.glyph-grid[aria-busy="false"] [data-corpus="${fixture.next}"]')`)
  await click(`[data-corpus="${fixture.next}"]`)
  await browser.waitFor(`document.querySelector('.corpus-dialog .state-pill')?.classList.contains('flagged')`)
  assert(await browser.evaluate(`document.querySelector('dialog').scrollWidth <= document.querySelector('dialog').clientWidth + 1`), 'mobile dialog overflows')
  const proposal = (await exportRows()).find(row => row.source_update.proposed_character === '有')
  assert(proposal?.source_update.original_character === '在' && proposal.source_update.applied_upstream === false, 'export lost source proposal provenance')
  assert(errors.length === 0, 'browser exceptions: ' + errors.join(', '))
  console.log('PASS corpus panel, source context, suggested correction, durable flag, save, neutral skips, export, mobile')
} finally {
  await browser?.close()
  await service.stop()
  rmSync(root, { recursive: true, force: true })
}
