#!/usr/bin/env bun
/**
 * The drawn forms of a character (docs/decisions/0014-drawn-forms.md), in a real browser, against a
 * disposable dataset and the repository's own table.
 *
 * A crop named 事 offers 事's three drawn forms on its form bar, each set in the site's font, the only
 * one with a glyph at its code point, and in the colour of Han. One is chosen and saved: a drawn form
 * is a member of 事's family, so the crop is now written as it and its form claim names the
 * private-use code point in the table's namespace. 事's page lists all three among its 字体, the saved
 * one with its crop, and the form's own page shows that crop under 事.
 *
 * Run through devrun, after `bun run build` in apps/review:
 *   devrun bun apps/review/tools/drawn-forms-check.mjs [--shots DIR]
 */
import { Database } from 'bun:sqlite'
import { join } from 'node:path'
import { mkdirSync } from 'node:fs'
import Browser from './browser.mjs'
import { boot, options, units } from './harness.mjs'
import { DRAWN, DRAWN_FAMILY, DRAWN_NAMESPACE } from '../../cloudflare/src/drawnFormsData.ts'

const config = options()
const service = await boot(config)
const shotsAt = process.argv.indexOf('--shots')
const screenshots = shotsAt >= 0 ? process.argv[shotsAt + 1] : '/tmp/atlas-drawn-forms-shots'
mkdirSync(screenshots, { recursive: true })
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const ready = 'document.querySelector("dialog[open] .crop-viewport")?.dataset.ready === "true" && !document.querySelector(".save-character")?.disabled'
const forms = Object.keys(DRAWN).filter(char => DRAWN[char].for === '事').sort()
const claimed = id => {
  const db = new Database(join(config.directory, 'review.sqlite'), { readonly: true })
  try {
    return db.query(`SELECT r.scheme,r.value,r.namespace FROM assertions a JOIN forms f ON f.id=a.object JOIN representations r ON r.id=f.anchor
      WHERE a.subject=? AND a.predicate='has_form' AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract')
      ORDER BY a.rowid`).all(id)
  } finally { db.close() }
}
// How a drawn form's chip is set: the font it asks for, whether that font has loaded with a glyph for
// it, and the script it is coloured as.
const set = (scope, char) => browser.evaluate(`(async () => {
  const el = [...document.querySelectorAll(${JSON.stringify(scope)} + ' .script-char')].find(e => e.textContent === ${JSON.stringify(char)})
  if (!el) return null
  await document.fonts.load('20px "${DRAWN_FAMILY}"', ${JSON.stringify(char)})
  const box = el.getBoundingClientRect()
  return { family: getComputedStyle(el).fontFamily, drawn: document.fonts.check('20px "${DRAWN_FAMILY}"', ${JSON.stringify(char)}), script: el.dataset.script, width: box.width }
})()`)
// A part of the page at the device's pixel ratio, in both colour schemes. The part is measured again
// for each: a row that loads late moves what is under it.
async function shoot(name, selector) {
  await browser.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 2, mobile: false })
  for (const scheme of ['light', 'dark']) {
    await browser.setColorScheme(scheme)
    await Bun.sleep(600)
    const box = await browser.evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); el.scrollIntoView({ block: 'center' })
      const r = el.getBoundingClientRect(); return { x: Math.max(0, r.x - 12), y: Math.max(0, r.y - 12), width: r.width + 24, height: r.height + 24 } })()`)
    const shot = await browser.send('Page.captureScreenshot', { format: 'png', clip: { ...box, scale: 1 }, captureBeyondViewport: true })
    await Bun.write(join(screenshots, `${name}-${scheme}.png`), Buffer.from(shot.data, 'base64'))
  }
  await browser.setColorScheme('light')
  await browser.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false })
}

try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  await browser.writeAs('drawn-forms-check')
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })
  assert(forms.length === 3, `the table gives 事 ${forms.length} drawn forms, not 3`)
  const id = Object.entries(units(config.directory)).find(([, unit]) => unit.active && unit.unicode === 'U+3042')[0]
  await browser.goto(`${service.base}/en/crop/${encodeURIComponent(id)}`, { waitFor: ready, timeout: 90000 })
  // The crop is named 事 first, as a review of it.
  const renamed = await browser.evaluate(`(async () => {
    const crop = await (await fetch('/atlas/characters/${encodeURIComponent(id)}')).json()
    const response = await fetch('/layers/units/${encodeURIComponent(id)}', { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ id: crypto.randomUUID(), revision: crop.revision, image_sha256: crop.image_sha256, verdict: 'wrong', issue: 'character', character: '事' }) })
    return response.status
  })()`)
  assert(renamed === 200, `the crop could not be named 事: ${renamed}`)
  // The local service derives a character's forms on its first card read; the check pays that here.
  const card = await (await fetch(`${service.base}/layers/characters/U%2B4E8B?expand=none`)).json()
  assert(JSON.stringify(card.grapheme.members.slice(-3).map(member => member.char)) === JSON.stringify(forms),
    `事's card does not list its drawn forms as members: ${JSON.stringify(card.grapheme.members.map(member => member.code_point))}`)
  await browser.goto(`${service.base}/en/crop/${encodeURIComponent(id)}`, { waitFor: ready, timeout: 90000 })

  // The bar holds them all: a drawn form has no 構形, so they stand together after the 事 forms that
  // have one, and before 事's related characters (仕).
  const chips = '[...document.querySelectorAll("dialog[open] .crop-form .form-chip")]'
  await browser.waitFor(`${chips}.some(c => c.textContent.includes(${JSON.stringify(forms[0])}))`, 30000)
  const bar = await browser.evaluate(`${chips}.map(c => c.querySelector('.script-char')?.textContent ?? '')`)
  const at = bar.indexOf(forms[0])
  assert(bar[0] === '事' && at > 0 && JSON.stringify(bar.slice(at, at + forms.length)) === JSON.stringify(forms) && bar.indexOf('仕') >= at + forms.length,
    `the bar does not offer 事's drawn forms before its related characters: ${JSON.stringify(bar.map(c => c.codePointAt(0).toString(16)))}`)
  for (const char of forms) {
    const how = await set('dialog[open] .crop-form', char)
    assert(how?.family.includes(DRAWN_FAMILY) && how.drawn && how.script === 'kanji' && how.width > 4,
      `U+${char.codePointAt(0).toString(16).toUpperCase()} is not drawn on the bar: ${JSON.stringify(how)}`)
  }
  await shoot('form-bar', 'dialog[open] .crop-form')
  console.log('PASS the form bar offers 事\'s drawn forms, in the site\'s font and the colour of Han')

  // Choosing one and saving names the crop as it, a member of 事's family, and claims the form.
  await browser.evaluate(`${chips}.find(c => c.textContent.includes(${JSON.stringify(forms[0])})).click()`)
  await browser.waitFor(`${chips}.some(c => c.textContent.includes(${JSON.stringify(forms[0])}) && c.getAttribute('aria-pressed') === 'true')`)
  await browser.evaluate('document.querySelector(".save-character").click()')
  await browser.waitFor('document.querySelector("dialog[open]") === null', 30000)
  assert(JSON.stringify(claimed(id)) === JSON.stringify([{ scheme: 'pua', value: forms[0], namespace: DRAWN_NAMESPACE }]), `the form was not claimed: ${JSON.stringify(claimed(id))}`)
  assert(units(config.directory)[id].unicode === 'U+F0000', `the crop is not written as the drawn form: ${units(config.directory)[id].unicode}`)
  await browser.goto(`${service.base}/en/crop/${encodeURIComponent(id)}`, { waitFor: ready, timeout: 90000 })
  await browser.waitFor(`${chips}.some(c => c.getAttribute('aria-pressed') === 'true' && c.querySelector('.script-char')?.textContent === ${JSON.stringify(forms[0])})`, 30000)
  await shoot('saved-form', 'dialog[open]')
  console.log('PASS the saved drawn form is selected when the crop is reopened')

  // 事's page lists all three among its 字体, and the family's crops hold the one just named.
  await browser.goto(`${service.base}/en/character/U+4E8B`, { waitFor: '!!document.querySelector(".character-members")', timeout: 90000 })
  await browser.waitFor(`document.querySelectorAll('.character-members .member .script-char').length >= 3`, 30000)
  for (const char of forms) {
    const how = await set('.character-members', char)
    assert(how?.family.includes(DRAWN_FAMILY) && how.drawn && how.script === 'kanji', `U+${char.codePointAt(0).toString(16).toUpperCase()} is not drawn on 事's page: ${JSON.stringify(how)}`)
  }
  await shoot('character-forms', '.character-members:has(.member)')
  console.log('PASS 事\'s page lists its drawn forms among its 字体')

  // The form's own page: under 事, with the crop, and no link to a dictionary of encoded characters.
  await browser.goto(`${service.base}/en/character/U+F0000`, { waitFor: '!!document.querySelector(".character-members")', timeout: 90000 })
  await browser.waitFor(`!!document.querySelector('.character-members .member[aria-current]')`, 30000)
  const own = await browser.evaluate(`({ active: document.querySelector('.character-members .member[aria-current] .script-char')?.textContent,
    zi: !!document.querySelector('.character-members .zi-link') })`)
  assert(own.active === forms[0] && !own.zi, `the drawn form's page is not its own: ${JSON.stringify(own)}`)
  await browser.waitFor(`[...document.querySelectorAll('.glyph-tile')].some(tile => tile.dataset.unit === ${JSON.stringify(id)})`, 30000)
    .catch(async () => { throw new Error('the drawn form\'s page does not list the crop named as it: '
      + await browser.evaluate(`JSON.stringify([...document.querySelectorAll('.glyph-tile')].map(tile => tile.dataset.unit ?? tile.dataset.corpus))`)) })
  await shoot('form-page', '.character-members:has(.member)')
  console.log('PASS the drawn form has a page under 事 with its crop')

  assert(!errors.length, 'page errors: ' + errors.join('; '))
  console.log('PASS drawn forms')
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
