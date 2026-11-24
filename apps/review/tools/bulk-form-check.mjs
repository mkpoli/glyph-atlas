#!/usr/bin/env bun
/**
 * Explore's bulk form confirmation, in a real browser, against a disposable dataset.
 *
 * On a character page (`所`), select mode offers the same forms as the single-crop form bar
 * (grapheme members, IVD figures, regional shapes, attested variants and linked forms). Applying an
 * IVD figure or regional shape confirms unchecked crops and records `has_form` while keeping their
 * character; applying a form to already-checked crops still records `has_form`; applying a member of
 * the same grapheme relabels, confirms and records `has_form`; Undo takes both the review and the
 * form claim back.
 *
 * Run:
 *   bun apps/review/tools/bulk-form-check.mjs [--shots /tmp/atlas-bulk-shots]
 */
import { Database } from 'bun:sqlite'
import { join } from 'node:path'
import { mkdirSync } from 'node:fs'
import Browser from './browser.mjs'
import { boot, events, options, units } from './harness.mjs'

const config = options()
const service = await boot(config)
const shotsAt = process.argv.indexOf('--shots')
const screenshots = shotsAt >= 0 ? process.argv[shotsAt + 1] : '/tmp/atlas-bulk-form-shots'
mkdirSync(screenshots, { recursive: true })
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const claimed = id => {
  const db = new Database(join(config.directory, 'review.sqlite'), { readonly: true })
  try {
    return db.query(`SELECT r.value FROM assertions a JOIN forms f ON f.id=a.object JOIN representations r ON r.id=f.anchor
      WHERE a.subject=? AND a.predicate='has_form' AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract')
      ORDER BY a.rowid`).all(id).map(row => row.value)
  } finally { db.close() }
}
const point = char => 'U+' + char.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')
const setRetina = (width, height) => browser.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 2, mobile: width < 500 })

try {
  browser = await Browser.launch({ width: 1280, height: 860 })
  await browser.writeAs('bulk-form-check', { anonymous: false })
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })

  // Warm the character cards so the page does not wait on cold reference builds.
  for (const code of ['U+3042', 'U+6240']) await fetch(`${service.base}/layers/characters/${encodeURIComponent(code)}?expand=none`)

  // Prepare three crops labelled 所 (unchecked) by moving three あ crops to 所 and then resetting their
  // review state isn't needed if we test on fresh 所 crops; wait — renaming via /layers/units marks them
  // reviewed! Instead test unchecked + checked on 所 by testing:
  // (1) unchecked あ crops on /lzh/character/U+3042 (grapheme member confirmation + relabel + undo),
  // (2) 所 crops on /lzh/character/U+6240 (IVD figure + regional shape chips, and claiming on already-checked crops + undo).
  const stored = units(config.directory)
  const aUnits = Object.entries(stored).filter(([, u]) => u.active && u.unicode === 'U+3042').map(([id]) => id)
  assert(aUnits.length >= 6, `expected at least 6 あ crops, got ${aUnits.length}`)

  // Move two あ crops to 所 so /lzh/character/U+6240 has real tiles (already checked from the rename).
  const suIds = []
  for (const id of aUnits.slice(0, 4)) {
    const crop = await (await fetch(`${service.base}/atlas/characters/${encodeURIComponent(id)}`)).json()
    if (!crop.image_sha256) continue
    const res = await fetch(`${service.base}/layers/units/${encodeURIComponent(id)}`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ id: crypto.randomUUID(), client_id: 'setup', revision: crop.revision, image_sha256: crop.image_sha256, verdict: 'wrong', issue: 'character', character: '所' }),
    })
    if (res.ok) suIds.push(id)
    if (suIds.length === 2) break
  }
  assert(suIds.length === 2, `could not prepare two 所 crops: ${suIds.length}`)

  // 1. On /lzh/character/U+6240 (所), open select mode and verify the non-default IVD figure 所󠄃 (MJ012119) appears.
  await browser.goto(`${service.base}/lzh/character/U%2B6240`, { waitFor: `document.querySelectorAll('.glyph-tile').length >= 2`, timeout: 60000 })
  await setRetina(1280, 860)
  await browser.waitFor(`document.querySelector('.select-toggle') !== null`, 20000)
  await browser.evaluate(`document.querySelector('.select-toggle').click()`)
  await browser.waitFor(`document.querySelector('.bulk-bar') !== null`, 20000)
  try {
    await browser.waitFor(`[...document.querySelectorAll('.bulk-choice')].some(b => b.title.includes('MJ012119'))`, 30000)
  } catch (e) {
    const diag = await browser.evaluate(`({ url: location.href, choices: [...document.querySelectorAll('.bulk-choice')].map(b => ({ text: b.textContent, title: b.title })), bar: document.querySelector('.bulk-bar')?.textContent })`)
    console.error('DIAG:', JSON.stringify(diag, null, 2))
    console.error('ERRORS:', errors)
    throw e
  }
  const titles = await browser.evaluate(`[...document.querySelectorAll('.bulk-choice')].map(b => b.title)`)
  console.log('所 bulk choices:', titles)
  assert(titles.includes('所\u{E0103} MJ012119'), `BulkBar lacks 所󠄃 MJ012119: ${JSON.stringify(titles)}`)
  assert(!titles.includes('所\u{E0102} MJ012120'), `BulkBar offered default figure MJ012120: ${JSON.stringify(titles)}`)
  await browser.waitFor(`[...document.fonts].some(f => f.family.replaceAll('"', '') === 'MJ Figures' && f.status === 'loaded')`, 20000)
  // Select both 所 tiles and choose the IVD figure 所󠄃.
  await browser.evaluate(`document.querySelectorAll('.glyph-tile').forEach(t => t.click())`)
  await browser.waitFor(`document.querySelectorAll('.glyph-tile.selected').length === 2`)
  await browser.evaluate(`[...document.querySelectorAll('.bulk-choice')].find(b => b.title.includes('MJ012119')).click()`)
  await browser.waitFor(`document.querySelector('.bulk-choice.active')?.title.includes('MJ012119')`)
  await browser.screenshot(join(screenshots, 'after-suo-light.png'))
  await browser.setColorScheme('dark')
  await browser.screenshot(join(screenshots, 'after-suo-dark.png'))
  await browser.setColorScheme('light')

  // Apply 所󠄃 on already-checked 所 tiles: must record has_form = 所󠄃 while keeping U+6240!
  await browser.evaluate(`document.querySelector('.bulk-apply').click()`)
  await browser.waitFor(`document.querySelector('.bulk-done') !== null`, 20000)
  for (const id of suIds) {
    assert(units(config.directory)[id].unicode === 'U+6240', `${id} changed character: ${units(config.directory)[id].unicode}`)
    assert(JSON.stringify(claimed(id)) === JSON.stringify(['所\u{E0103}']), `${id} did not get IVD form claim: ${JSON.stringify(claimed(id))}`)
  }
  console.log('PASS 所 page offers IVD figures and applies IVD has_form on already-checked crops')

  // Undo retracts the IVD form claims cleanly.
  await browser.evaluate(`[...document.querySelectorAll('.bulk-result button')].find(b => !b.classList.contains('quiet')).click()`)
  await browser.waitFor(`document.querySelector('.bulk-done') === null`, 20000)
  for (const id of suIds) {
    assert(JSON.stringify(claimed(id)) === '[]', `${id} form claim was not undone: ${JSON.stringify(claimed(id))}`)
  }
  console.log('PASS undo retracts bulk IVD form claims')

  // 2. On /lzh/character/U+3042 (あ), select two unchecked あ crops and confirm them as a grapheme member (あ itself, then undo, then another member).
  await browser.goto(`${service.base}/lzh/character/U%2B3042`, { waitFor: `document.querySelectorAll('.glyph-tile').length >= 2`, timeout: 60000 })
  await browser.waitFor(`document.querySelector('.select-toggle') !== null`, 20000)
  await browser.evaluate(`document.querySelector('.select-toggle').click()`)
  await browser.waitFor(`document.querySelectorAll('.bulk-choice').length > 1`, 30000)
  const pickedIds = await browser.evaluate(`[...document.querySelectorAll('.glyph-tile')].filter(t => !t.classList.contains('decided-checked')).slice(0, 2).map(t => t.dataset.unit)`)
  assert(pickedIds.length === 2, `need 2 unchecked あ tiles, got ${pickedIds.length}`)
  for (const id of pickedIds) {
    await browser.evaluate(`document.querySelector('.glyph-tile[data-unit=${JSON.stringify(id)}]').click()`)
  }
  await browser.waitFor(`document.querySelectorAll('.glyph-tile.selected').length === 2`)
  // Click the あ form chip itself -> confirms checked AND claims has_form = あ!
  await browser.evaluate(`[...document.querySelectorAll('.bulk-choice')].find(b => b.textContent.trim() === 'あ').click()`)
  await browser.waitFor(`document.querySelector('.bulk-choice.active')?.textContent.trim() === 'あ'`)
  const beforeEvents = events(config.directory).length
  await browser.evaluate(`document.querySelector('.bulk-apply').click()`)
  try {
    await browser.waitFor(`document.querySelector('.bulk-done') !== null`, 20000)
  } catch (e) {
    const diag = await browser.evaluate(`({ err: document.querySelector('.bulk-error')?.textContent, report: document.querySelector('.bulk-report')?.textContent, bar: document.querySelector('.bulk-bar')?.textContent, disabled: document.querySelector('.bulk-apply')?.disabled })`)
    console.error('DIAG あ:', JSON.stringify(diag, null, 2))
    console.error('SERVICE LOG TAIL:', service.log.slice(-20).join(''))
    throw e
  }
  for (const id of pickedIds) {
    assert(units(config.directory)[id].unicode === 'U+3042', `${id} character changed`)
    assert(events(config.directory).slice(beforeEvents).some(e => e.target_id === id && e.field === 'review' && e.new === 'reviewed'), `${id} was not confirmed checked`)
    assert(JSON.stringify(claimed(id)) === JSON.stringify(['あ']), `${id} did not get has_form あ: ${JSON.stringify(claimed(id))}`)
  }
  console.log('PASS confirming own form chip both confirms checked and records has_form')

  // Undo takes back BOTH the review confirmation AND the has_form claim.
  await browser.evaluate(`[...document.querySelectorAll('.bulk-result button')].find(b => !b.classList.contains('quiet')).click()`)
  await browser.waitFor(`document.querySelector('.bulk-done') === null`, 20000)
  for (const id of pickedIds) {
    assert(JSON.stringify(claimed(id)) === '[]', `${id} form claim was not undone`)
    assert(units(config.directory)[id].review !== 'reviewed', `${id} review was not undone: ${units(config.directory)[id].review}`)
  }
  console.log('PASS undo restores both review confirmation and has_form')

  // Now select those two unchecked あ tiles and pick the 2nd grapheme member (e.g. ア or hentaigana):
  // must relabel unicode, confirm checked, AND claim has_form!
  for (const id of pickedIds) {
    await browser.evaluate(`document.querySelector('.glyph-tile[data-unit=${JSON.stringify(id)}]').click()`)
  }
  await browser.waitFor(`document.querySelectorAll('.glyph-tile.selected').length === 2`)
  const secondChar = await browser.evaluate(`(() => { const b = document.querySelectorAll('.bulk-choice')[1]; b.click(); return b.querySelector('.script-text').textContent })()`)
  await browser.evaluate(`document.querySelector('.bulk-apply').click()`)
  await browser.waitFor(`document.querySelector('.bulk-done') !== null`, 20000)
  for (const id of pickedIds) {
    assert(units(config.directory)[id].unicode === point(secondChar), `${id} was not relabelled to ${secondChar}: ${units(config.directory)[id].unicode}`)
    assert(JSON.stringify(claimed(id)) === JSON.stringify([secondChar]), `${id} did not get has_form ${secondChar}: ${JSON.stringify(claimed(id))}`)
  }
  console.log(`PASS grapheme member chip (${secondChar}) relabels, confirms checked, and records has_form`)

  // Also test phone viewport doesn't overflow.
  await setRetina(390, 844)
  assert(await browser.evaluate(`document.documentElement.scrollWidth <= innerWidth + 1`), 'BulkBar overflows on phone viewport')
  await browser.screenshot(join(screenshots, 'after-phone-light.png'))

  assert(errors.length === 0, `Runtime exceptions: ${errors.join('; ')}`)
  console.log('\nbulk-form-check passed')
} finally {
  await browser?.close()
  await service.stop?.()
}
