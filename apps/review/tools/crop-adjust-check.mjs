#!/usr/bin/env bun
/**
 * Redrawing a bad crop, in a real browser, against a disposable dataset.
 *
 * Choosing "bad crop" turns the page view into the crop editor with the crop's box and its handles.
 * A handle resizes the box with a mouse or a finger, the arrows move it and Shift with the arrows
 * resizes it; the save then records the new box, and the button says it saves.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/crop-adjust-check.mjs
 */
import { join } from 'node:path'
import { mkdirSync } from 'node:fs'
import Browser from './browser.mjs'
import { boot, events, options, units } from './harness.mjs'

const config = options()
const service = await boot(config)
const screenshots = '/tmp/atlas-character-shots'
mkdirSync(screenshots, { recursive: true })
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const ready = 'document.querySelector("dialog[open] .crop-viewport")?.dataset.ready === "true" && !document.querySelector(".save-character")?.disabled'
const outline = () => browser.evaluate(`(() => { const r = document.querySelector('dialog[open] .crop-mask').getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height } })()`)
const framing = () => browser.evaluate(`document.querySelector('dialog[open] .crop-plane')?.style.transform`)
const handle = edge => browser.evaluate(`(() => { const r = document.querySelector('dialog[open] .handle.${edge}').getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 } })()`)

try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  await browser.writeAs('crop-adjust-check')
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })
  const stored = units(config.directory)
  const id = Object.entries(stored).find(([, unit]) => unit.active && unit.box && unit.page_id === 'doc-1:p1')?.[0]
  assert(id, 'the fixture has a crop with a box on a cached page')
  const before = stored[id].box
  await browser.goto(`${service.base}/en/crop/${encodeURIComponent(id)}`, { waitFor: ready, timeout: 90000 })

  // The crop is redrawn in the one view, as it is framed: nothing reframes or moves when editing starts.
  await browser.evaluate('document.querySelector("dialog[open] .crop-viewport").scrollIntoView({ block: "center" })')
  const framed = await framing()
  await browser.key('b')
  await browser.waitFor('document.querySelectorAll("dialog[open] .crop-mask.editing .handle").length === 8', 30000)
  assert(await framing() === framed, 'starting to redraw moved the page view: ' + framed + ' → ' + await framing() + ' ' + await browser.evaluate('JSON.stringify(document.querySelector("dialog[open] .crop-viewport").getBoundingClientRect())'))
  assert(await browser.evaluate('document.activeElement?.classList.contains("crop-viewport")'), 'the view does not take the keyboard')
  assert(await browser.evaluate('document.querySelectorAll("dialog[open] .crop-box, dialog[open] .crop-preview, dialog[open] .crop-adjustment").length') === 0, 'a second view is shown')
  const size = await browser.evaluate(`document.querySelector('dialog[open] .handle.se').getBoundingClientRect().width`)
  assert(size <= 9, `the handles are ${size} px wide`)
  const start = await outline()

  // A mouse drags the bottom-right handle out.
  const corner = await handle('se')
  await browser.drag(corner, { x: corner.x + 24, y: corner.y + 18 })
  const grown = await outline()
  // The page view may end close to the crop's right edge, where the box stops growing.
  assert(grown.w > start.w && grown.h > start.h + 10 && Math.abs(grown.x - start.x) < 2 && Math.abs(grown.y - start.y) < 2, `the corner did not resize the box: ${JSON.stringify([start, grown])}`)

  assert(await framing() === framed, 'resizing the box moved the page view')
  // A finger drags the top edge down.
  await browser.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 1 })
  const top = await handle('n')
  const touch = (type, y) => browser.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x: Math.round(top.x), y: Math.round(y) }] })
  await touch('touchStart', top.y)
  for (let step = 1; step <= 6; step++) await touch('touchMove', top.y + step * 3)
  await touch('touchEnd')
  const touched = await outline()
  assert(touched.y > grown.y + 8 && touched.h < grown.h - 8, `a touch on the top edge did not move it: ${JSON.stringify([grown, touched])}`)

  // The arrows move it, and Shift with the arrows resizes it; an arrow here never steps to another crop.
  await browser.evaluate('document.querySelector("dialog[open] .crop-viewport").focus()')
  await browser.key('ArrowLeft')
  const moved = await outline()
  assert(moved.x < touched.x && Math.abs(moved.w - touched.w) < 1, `ArrowLeft did not move the box: ${JSON.stringify([touched, moved])}`)
  // Zoomed in, a step is still at least one page pixel, so the arrows keep moving the box.
  for (let i = 0; i < 6; i++) await browser.evaluate(`document.querySelector('dialog[open] .crop-viewport').dispatchEvent(new KeyboardEvent('keydown', { key: '+', bubbles: true }))`)
  const zoomed = await outline()
  await browser.key('ArrowLeft')
  assert((await outline()).x < zoomed.x, 'zoomed in, ArrowLeft no longer moves the box')
  await browser.evaluate(`document.querySelector('dialog[open] .crop-viewport').dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }))`)
  const homed = await outline()
  await browser.key('ArrowRight')
  const back = await outline()
  assert(back.x > homed.x, 'ArrowRight did not move the box back')
  const moved2 = back
  await browser.key('ArrowDown', { shift: true })
  const taller = await outline()
  assert(taller.h > moved2.h && Math.abs(taller.y - moved2.y) < 1, 'Shift+ArrowDown did not make the box taller')
  assert(await browser.evaluate('!!document.querySelector("dialog[open]")'), 'an arrow in the crop editor left the crop')
  assert((await browser.evaluate('document.querySelector(".save-character").innerText')).startsWith('Save'), 'the button does not say it saves the new box')
  await browser.screenshot(join(screenshots, 'recrop-desktop-light.png'))
  // Escape leaves the editor with the new box kept, and the inspector stays open.
  await browser.key('Escape')
  await browser.waitFor('!document.querySelector("dialog[open] .crop-mask.editing")')
  assert(await browser.evaluate('!!document.querySelector("dialog[open]") && !!document.querySelector("dialog[open] .crop-change button")'), 'Escape closed the inspector or dropped the box')
  assert(await browser.evaluate('document.activeElement?.closest(".crop-change, .inspector-savebar") != null'), 'the focus was lost leaving the editor')

  const mark = events(config.directory).length
  await browser.evaluate('document.querySelector(".save-character").click()')
  await browser.waitFor('document.querySelector("dialog[open]") === null', 30000)
  const written = events(config.directory).slice(mark)
  const recorded = written.find(e => e.target_id === id && e.field === 'box')?.new
  assert(recorded, 'no box was recorded')
  assert(recorded.w > before.w && recorded.y > before.y && recorded.h !== before.h,
    `the recorded box does not follow the edits: ${JSON.stringify([before, recorded])}`)
  assert(written.find(e => e.target_id === id && e.field === 'review')?.new === 'reviewed', 'the redrawn crop is not reviewed')
  console.log(`PASS bad crop redrawn with a handle, a touch and the arrows, saved as ${JSON.stringify(recorded)}`)

  // On the site a saved box waits for the next publication to cut it: the crop says so, and its box
  // shows what the new cut will be. The fixture's service cuts at once, so the site's answer is played.
  const record = await (await fetch(`${service.base}/atlas/characters/${encodeURIComponent(id)}`)).json()
  const pending = { ...record, box_pending: true }
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: `*/atlas/characters/${encodeURIComponent(id).replaceAll('%', '%25')}` }, { urlPattern: `*/atlas/characters/${encodeURIComponent(id)}` }] })
  browser.listeners.push(m => { if (m.method === 'Fetch.requestPaused') browser.send('Fetch.fulfillRequest', {
    requestId: m.params.requestId, responseCode: 200, responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
    body: Buffer.from(JSON.stringify(pending)).toString('base64') }) })
  // Opened from its character's gallery, the inspector reads the crop in the browser, where the answer
  // above is played.
  await browser.goto(`${service.base}/en/character/${units(config.directory)[id].unicode}`, { waitFor: `!!document.querySelector('[data-unit="${id}"]')`, timeout: 90000 })
  await browser.evaluate(`document.querySelector('[data-unit="${id}"]').click()`)
  await browser.waitFor(`document.querySelector('dialog[open] .state-pill')?.textContent === 'Box corrected, awaiting re-cut'`, 30000)
  // The saved box is the one outlined on the page.
  await browser.waitFor(`!!document.querySelector('dialog[open] .crop-mask')`, 30000)
  const shownBox = await browser.evaluate(`(() => { const r = document.querySelector('dialog[open] .crop-mask')?.getBoundingClientRect(); return r ? r.width / r.height : null })()`)
  assert(shownBox && Math.abs(shownBox - recorded.w / (recorded.h * (record.source_scale?.[1] ?? 1) / (record.source_scale?.[0] ?? 1))) < 0.1, `a box awaiting its cut is not outlined: ${shownBox}`)
  await browser.screenshot(join(screenshots, 'recrop-pending-light.png'))
  console.log('PASS a saved box awaiting its cut is named and shown')

  assert(!errors.length, 'page errors: ' + errors.join('; '))
  console.log('PASS crop adjust')
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
