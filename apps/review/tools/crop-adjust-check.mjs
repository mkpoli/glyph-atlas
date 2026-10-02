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
const outline = () => browser.evaluate(`(() => { const r = document.querySelector('dialog[open] .context-outline').getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height } })()`)
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

  await browser.key('b')
  await browser.waitFor('document.querySelector("dialog[open] .context-region.drawing img")?.naturalWidth > 0', 30000)
  assert(await browser.evaluate('document.querySelectorAll("dialog[open] .handle").length') === 8, 'the crop editor shows no handles')
  assert(await browser.evaluate('document.activeElement?.classList.contains("context-region")'), 'the crop editor does not take the keyboard')
  await browser.evaluate('document.querySelector("dialog[open] .context-region").scrollIntoView({ block: "center" })')
  const start = await outline()

  // A mouse drags the bottom-right handle out.
  const corner = await handle('se')
  await browser.drag(corner, { x: corner.x + 24, y: corner.y + 18 })
  const grown = await outline()
  // The page view may end close to the crop's right edge, where the box stops growing.
  assert(grown.w > start.w && grown.h > start.h + 10 && Math.abs(grown.x - start.x) < 2 && Math.abs(grown.y - start.y) < 2, `the corner did not resize the box: ${JSON.stringify([start, grown])}`)

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
  await browser.evaluate('document.querySelector("dialog[open] .context-region").focus()')
  await browser.key('ArrowLeft')
  const moved = await outline()
  assert(moved.x < touched.x && Math.abs(moved.w - touched.w) < 1, `ArrowLeft did not move the box: ${JSON.stringify([touched, moved])}`)
  await browser.key('ArrowDown', { shift: true })
  const taller = await outline()
  assert(taller.h > moved.h && Math.abs(taller.y - moved.y) < 1, 'Shift+ArrowDown did not make the box taller')
  assert(await browser.evaluate('!!document.querySelector("dialog[open]")'), 'an arrow in the crop editor left the crop')
  assert((await browser.evaluate('document.querySelector(".save-character").innerText')).startsWith('Save'), 'the button does not say it saves the new box')
  await browser.screenshot(join(screenshots, 'recrop-desktop-light.png'))
  // Escape leaves the editor with the new box kept, and the inspector stays open.
  await browser.key('Escape')
  await browser.waitFor('!document.querySelector("dialog[open] .context-region.drawing")')
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

  assert(!errors.length, 'page errors: ' + errors.join('; '))
  console.log('PASS crop adjust')
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
