#!/usr/bin/env bun
/**
 * A crop's written form composed from its character's own description, in a real browser, against a
 * disposable dataset and the repository's character tables.
 *
 * A crop named 疑 opens the form picker's IDS editor on 疑's description, ⿰𠤕⿱龴疋. The editor's
 * own verbs are tried on a sequence typed with ASCII operator names: a character typed on a slot goes
 * to the part field and Enter puts it there, operator keys split a slot and swap an operator (and
 * refuse a swap that would drop a filled part), Tab and Shift+Tab step through the parts, Backspace
 * empties one, Ctrl+Z and Ctrl+Shift+Z undo and redo, and × takes a level away. Then 𠤕 is split
 * into ⿱匕矢, 矢 is swapped for 失 and 龴 for コ from the components the variant table offers, and the
 * result, ⿰⿱匕失⿱コ疋, is saved as the crop's form claim; the crop keeps its character.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/ids-form-check.mjs [--shots DIR]
 */
import { Database } from 'bun:sqlite'
import { join } from 'node:path'
import { mkdirSync } from 'node:fs'
import Browser from './browser.mjs'
import { boot, options, units } from './harness.mjs'

const config = options()
const service = await boot(config)
const shotsAt = process.argv.indexOf('--shots')
const screenshots = shotsAt >= 0 ? process.argv[shotsAt + 1] : '/tmp/atlas-ids-form-shots'
mkdirSync(screenshots, { recursive: true })
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const ready = 'document.querySelector("dialog[open] .crop-viewport")?.dataset.ready === "true" && !document.querySelector(".save-character")?.disabled'
const claimed = id => {
  const db = new Database(join(config.directory, 'review.sqlite'), { readonly: true })
  try {
    return db.query(`SELECT r.value FROM assertions a JOIN forms f ON f.id=a.object JOIN representations r ON r.id=f.anchor
      WHERE a.subject=? AND a.predicate='has_form' AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract')
      ORDER BY a.rowid`).all(id).map(row => row.value)
  } finally { db.close() }
}
const value = () => browser.evaluate(`document.querySelector('dialog[open] .ids-field input').value`)
const becomes = text => browser.waitFor(`document.querySelector('dialog[open] .ids-field input').value === ${JSON.stringify(text)}`)
  .catch(async () => { throw new Error(`the sequence is ${JSON.stringify(await value())}, not ${JSON.stringify(text)}`) })
// A part of the box tree, by its path, chosen and focused as a click and the keyboard would.
const focusPath = path => browser.evaluate(`(() => { const el = document.querySelector('dialog[open] .ids-tree [data-path="${path}"]'); el.click(); el.focus() })()`)
const chosenPath = () => browser.evaluate(`document.querySelector('dialog[open] .ids-tree [aria-pressed="true"]')?.dataset.path ?? null`)
const focusedPath = () => browser.evaluate(`document.activeElement?.dataset?.path ?? null`)
const typeSequence = text => browser.evaluate(`(() => { const i = document.querySelector('dialog[open] .ids-field input'); i.focus(); i.value = ${JSON.stringify(text)}; i.dispatchEvent(new Event('input', { bubbles: true })); i.blur() })()`)
const choosePart = async part => {
  await browser.evaluate(`[...document.querySelectorAll('dialog[open] .ids-part')].find(b => b.textContent === ${JSON.stringify(part)}).click()`)
  await browser.waitFor(`!!document.querySelector('dialog[open] .ids-swaps')`)
}
const pick = text => browser.evaluate(`[...document.querySelectorAll('dialog[open] .ids-swap')].find(b => b.textContent === ${JSON.stringify(text)}).click()`)
// The composer at the device's pixel ratio, cropped to the picker.
async function shoot(name) {
  await browser.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 2, mobile: false })
  const box = await browser.evaluate(`(() => { const r = document.querySelector('dialog[open] .form-picker').getBoundingClientRect(); return { x: r.x - 8, y: r.y - 8, width: r.width + 16, height: r.height + 16 } })()`)
  for (const scheme of ['light', 'dark']) {
    await browser.setColorScheme(scheme)
    await Bun.sleep(200)
    const shot = await browser.send('Page.captureScreenshot', { format: 'png', clip: { ...box, scale: 1 }, captureBeyondViewport: true })
    await Bun.write(join(screenshots, `${name}-${scheme}.png`), Buffer.from(shot.data, 'base64'))
  }
  await browser.setColorScheme('light')
}

try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  await browser.writeAs('ids-form-check')
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })
  const id = Object.entries(units(config.directory)).find(([, unit]) => unit.active && unit.unicode === 'U+3042')[0]
  await browser.goto(`${service.base}/en/crop/${encodeURIComponent(id)}`, { waitFor: ready, timeout: 90000 })
  // The crop is named 疑 first, as a review of it.
  const renamed = await browser.evaluate(`(async () => {
    const crop = await (await fetch('/atlas/characters/${encodeURIComponent(id)}')).json()
    const response = await fetch('/layers/units/${encodeURIComponent(id)}', { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ id: crypto.randomUUID(), revision: crop.revision, image_sha256: crop.image_sha256, verdict: 'wrong', issue: 'character', character: '疑' }) })
    return response.status
  })()`)
  assert(renamed === 200, `the crop could not be named 疑: ${renamed}`)
  assert(units(config.directory)[id].unicode === 'U+7591', 'the crop is not named 疑')
  // The local service derives a character's forms on its first card read, which reads every
  // description first (10–30 s on a busy machine), and the picker gives a card read 15 s. The site
  // reads the stored rows, so the check pays that once here, before the page asks.
  const card = await fetch(`${service.base}/layers/characters/U%2B7591?expand=none`)
  assert(card.ok, `疑's card could not be read: ${card.status}`)
  await browser.goto(`${service.base}/en/crop/${encodeURIComponent(id)}`, { waitFor: ready, timeout: 90000 })

  await browser.evaluate('document.querySelector("dialog[open] .form-add").click()')
  await browser.waitFor('!!document.querySelector("dialog[open] .form-compose")')
  await browser.evaluate('document.querySelector("dialog[open] .form-compose").click()')
  await browser.waitFor(`document.querySelector('dialog[open] .ids-field input')?.value === '⿰𠤕⿱龴疋'`, 20000)
  console.log('PASS the editor starts from 疑\'s own description')

  // Typing into the picker's search leaves the draft alone, and a unary operator round a chosen part
  // leaves nothing chosen that is not there.
  await browser.evaluate(`(() => { const i = document.querySelector('dialog[open] .form-picker > .character-search input'); i.focus(); i.value = '失'; i.dispatchEvent(new Event('input', { bubbles: true })) })()`)
  await Bun.sleep(300)
  assert(await value() === '⿰𠤕⿱龴疋', `a search reset the draft: ${await value()}`)
  await browser.evaluate(`(() => { const i = document.querySelector('dialog[open] .form-picker > .character-search input'); i.value = ''; i.dispatchEvent(new Event('input', { bubbles: true })) })()`)
  await browser.evaluate(`[...document.querySelectorAll('dialog[open] .ids-part')].find(b => b.textContent === '疋').click()`)
  await browser.evaluate(`document.querySelector('dialog[open] .ids-key[data-op="⿾"]').click()`)
  await browser.waitFor(`document.querySelector('dialog[open] .ids-field input').value === '⿰𠤕⿱龴⿾疋'`)
  await browser.evaluate(`(() => { const i = document.querySelector('dialog[open] .ids-field input'); i.value = '⿰𠤕⿱龴疋'; i.dispatchEvent(new Event('input', { bubbles: true })) })()`)
  await browser.waitFor(`document.querySelector('dialog[open] .ids-field input').value === '⿰𠤕⿱龴疋'`)
  assert(!errors.length, 'page errors: ' + errors.join('; '))
  console.log('PASS a search keeps the draft, and a unary operator wraps a chosen part')

  // Typed with ASCII names, the missing part is an empty slot, written ？ once the field is left.
  await typeSequence('LR木UD口')
  await becomes('⿰木⿱口？')
  assert(await browser.evaluate(`document.querySelectorAll('dialog[open] .ids-part.empty').length`) === 1, 'the missing part is not an empty slot')
  // A character typed on the empty slot goes to the part field; Enter puts it in the slot.
  await focusPath('1.1')
  await browser.key('日')
  assert(await browser.evaluate(`document.activeElement === document.querySelector('dialog[open] .ids-slot input')`), 'typing on a slot does not reach the part field')
  await browser.key('Enter')
  await becomes('⿰木⿱口日')
  console.log('PASS ASCII names are read as operators, and a part typed on a slot fills it')

  // An operator key on an operator swaps it, keeping its parts and padding with an empty slot; back
  // to two parts the empty one goes; a swap that would drop a filled part is refused.
  await focusPath('1')
  await browser.key('3')
  await becomes('⿰木⿲口日？')
  await browser.key('1')
  await becomes('⿰木⿰口日')
  await browser.key('e')
  await browser.waitFor(`!!document.querySelector('dialog[open] .ids-problem')`)
  assert(await value() === '⿰木⿰口日', `a refused swap changed the sequence: ${await value()}`)
  // An operator key on a component splits it, the component first and the new slot chosen.
  await focusPath('0')
  await browser.key('2')
  await becomes('⿰⿱木？⿰口日')
  assert(await chosenPath() === '0.1', `the new slot is not chosen: ${await chosenPath()}`)
  assert(!await browser.evaluate(`!!document.querySelector('dialog[open] .ids-problem')`), 'the refusal stayed after a change')
  console.log('PASS operator keys split a slot and swap an operator, and refuse to drop a filled part')

  // Tab and Shift+Tab step through the parts in reading order.
  assert(await focusedPath() === '0.1', `the chosen slot does not have the focus: ${await focusedPath()}`)
  await browser.key('Tab')
  assert(await chosenPath() === '1' && await focusedPath() === '1', `Tab did not move to the next part: ${await chosenPath()}`)
  await browser.key('Tab')
  assert(await chosenPath() === '1.0', `Tab did not move into the node: ${await chosenPath()}`)
  await browser.key('Tab', { shift: true })
  await browser.key('Tab', { shift: true })
  assert(await chosenPath() === '0.1', `Shift+Tab did not move back: ${await chosenPath()}`)
  // Backspace empties a part; Ctrl+Z and Ctrl+Shift+Z undo and redo it.
  await focusPath('1.0')
  await browser.key('Backspace')
  await becomes('⿰⿱木？⿰？日')
  await browser.key('z', { ctrl: true })
  await becomes('⿰⿱木？⿰口日')
  await browser.key('z', { ctrl: true, shift: true })
  await becomes('⿰⿱木？⿰？日')
  await browser.key('z', { ctrl: true })
  await becomes('⿰⿱木？⿰口日')
  await shoot('ids-editor-keys')
  // × takes one level away, keeping its first filled part.
  await browser.evaluate(`document.querySelector('dialog[open] .ids-op[data-path="0"]').closest('.ids-node').querySelector('.ids-unwrap').click()`)
  await becomes('⿰木⿰口日')
  assert(!errors.length, 'page errors: ' + errors.join('; '))
  console.log('PASS Tab steps through the parts, Backspace empties one, undo and redo, and × takes a level away')

  await typeSequence('⿰𠤕⿱龴疋')
  await becomes('⿰𠤕⿱龴疋')

  await choosePart('𠤕')
  await browser.waitFor(`[...document.querySelectorAll('dialog[open] .ids-split')].some(b => b.textContent === '⿱匕矢')`)
  await pick('⿱匕矢')
  await browser.waitFor(`document.querySelector('dialog[open] .ids-field input').value === '⿰⿱匕矢⿱龴疋'`)
  await choosePart('矢')
  await browser.waitFor(`[...document.querySelectorAll('dialog[open] .ids-swap')].some(b => b.textContent === '失')`)
  await pick('失')
  await browser.waitFor(`document.querySelector('dialog[open] .ids-field input').value === '⿰⿱匕失⿱龴疋'`)
  await choosePart('龴')
  await browser.waitFor(`[...document.querySelectorAll('dialog[open] .ids-swap')].some(b => b.textContent === 'コ')`)
  await shoot('ids-editor-swaps')
  await pick('コ')
  await browser.waitFor(`document.querySelector('dialog[open] .ids-field input').value === '⿰⿱匕失⿱コ疋'`)
  await shoot('ids-editor-composed')
  console.log(`PASS 𠤕 split, 矢 swapped for 失 and 龴 for コ: ${await value()}`)

  await browser.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false })
  // The derived list offers the same form.
  await browser.waitFor(`[...document.querySelectorAll('dialog[open] .form-option')].some(b => b.textContent.includes('⿰⿱匕失⿱コ疋')) || !!document.querySelector('dialog[open] .form-note')`, 30000)
  assert(await browser.evaluate(`[...document.querySelectorAll('dialog[open] .form-option')].some(b => b.textContent.includes('⿰⿱匕失⿱コ疋'))`),
    'the derived forms of 疑 do not offer ⿰⿱匕失⿱コ疋: ' + JSON.stringify(await browser.evaluate(`[...document.querySelectorAll('dialog[open] .form-section')].map(s => s.textContent.slice(0, 200))`)))
  await browser.evaluate('document.querySelector("dialog[open] .ids-use").click()')
  await browser.waitFor(`[...document.querySelectorAll('dialog[open] .crop-form .form-chip')].some(c => c.textContent.includes('⿰⿱匕失⿱コ疋') && c.getAttribute('aria-pressed') === 'true')`)
  await browser.evaluate('document.querySelector(".save-character").click()')
  await browser.waitFor('document.querySelector("dialog[open]") === null', 30000)
  assert(JSON.stringify(claimed(id)) === JSON.stringify(['⿰⿱匕失⿱コ疋']), `the form was not claimed: ${claimed(id)}`)
  assert(units(config.directory)[id].unicode === 'U+7591', 'the form changed the crop\'s character')
  console.log('PASS ⿰⿱匕失⿱コ疋 is saved as the crop\'s written form, the character kept')

  assert(!errors.length, 'page errors: ' + errors.join('; '))
  console.log('PASS ids form')
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
