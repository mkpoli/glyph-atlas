#!/usr/bin/env bun
/**
 * Characters of another script a grapheme is also written with (data/vocab/written-with.yaml), in a
 * real browser, against a disposable dataset and the repository's own table.
 *
 * 事's page lists ヿ and the hiragana digraph こと among its 字体, each a link to its own page, marked
 * as shared and coloured as katakana and hiragana. A crop named 事 offers both in its form picker in
 * the same colours; ヿ is chosen and saved, and the crop keeps its character with ヿ as its form.
 *
 * Run through devrun, after `bun run build` in apps/review:
 *   devrun bun apps/review/tools/written-with-check.mjs [--shots DIR]
 */
import { Database } from 'bun:sqlite'
import { join } from 'node:path'
import { mkdirSync } from 'node:fs'
import Browser from './browser.mjs'
import { boot, options, units } from './harness.mjs'
import { WRITERS } from '../../cloudflare/src/writtenWithData.ts'

const config = options()
const service = await boot(config)
const shotsAt = process.argv.indexOf('--shots')
const screenshots = shotsAt >= 0 ? process.argv[shotsAt + 1] : '/tmp/atlas-written-with-shots'
mkdirSync(screenshots, { recursive: true })
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const ready = 'document.querySelector("dialog[open] .crop-viewport")?.dataset.ready === "true" && !document.querySelector(".save-character")?.disabled'
const lent = WRITERS.filter(writer => writer.grapheme === '事').map(writer => writer.char)
const SCRIPTS = { '\u30ff': 'katakana', '\u{1B123}': 'hiragana' }
const claimed = id => {
  const db = new Database(join(config.directory, 'review.sqlite'), { readonly: true })
  try {
    return db.query(`SELECT r.scheme,r.value FROM assertions a JOIN forms f ON f.id=a.object JOIN representations r ON r.id=f.anchor
      WHERE a.subject=? AND a.predicate='has_form' AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract')
      ORDER BY a.rowid`).all(id)
  } finally { db.close() }
}
// The script each of the table's characters is coloured as inside `scope`, null where one is missing.
const coloured = scope => browser.evaluate(`JSON.stringify(${JSON.stringify(lent)}.map(char =>
  [...document.querySelectorAll(${JSON.stringify(scope)} + ' .script-char')].find(e => e.textContent === char)?.dataset.script ?? null))`).then(JSON.parse)
async function shoot(name, selector) {
  await browser.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 2, mobile: false })
  const box = await browser.evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); el.scrollIntoView({ block: 'center' })
    const r = el.getBoundingClientRect(); return { x: Math.max(0, r.x - 12), y: Math.max(0, r.y - 12), width: r.width + 24, height: r.height + 24 } })()`)
  for (const scheme of ['light', 'dark']) {
    await browser.setColorScheme(scheme)
    await Bun.sleep(250)
    const shot = await browser.send('Page.captureScreenshot', { format: 'png', clip: { ...box, scale: 1 }, captureBeyondViewport: true })
    await Bun.write(join(screenshots, `${name}-${scheme}.png`), Buffer.from(shot.data, 'base64'))
  }
  await browser.setColorScheme('light')
  await browser.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false })
}

try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  await browser.writeAs('written-with-check')
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })
  const want = lent.map(char => SCRIPTS[char])
  assert(lent.length === 2 && want.every(Boolean), `the table gives 事 ${JSON.stringify(lent)}`)
  const id = Object.entries(units(config.directory)).find(([, unit]) => unit.active && unit.unicode === 'U+3042')[0]
  await browser.goto(`${service.base}/en/crop/${encodeURIComponent(id)}`, { waitFor: ready, timeout: 90000 })
  const renamed = await browser.evaluate(`(async () => {
    const crop = await (await fetch('/atlas/characters/${encodeURIComponent(id)}')).json()
    const response = await fetch('/layers/units/${encodeURIComponent(id)}', { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ id: crypto.randomUUID(), revision: crop.revision, image_sha256: crop.image_sha256, verdict: 'wrong', issue: 'character', character: '事' }) })
    return response.status
  })()`)
  assert(renamed === 200, `the crop could not be named 事: ${renamed}`)
  // The local service derives a character's forms on its first card read; the check pays that here.
  const card = await (await fetch(`${service.base}/layers/characters/U%2B4E8B?expand=none`)).json()
  assert(lent.every(char => card.variants.writers.some(writer => writer.char === char && writer.shared)), `事's card does not name them as its writers: ${JSON.stringify(card.variants.writers)}`)

  // 事's page: both among the 字体, in their own colours, shared, each a link to its own page.
  await browser.goto(`${service.base}/en/character/U+4E8B`, { waitFor: '!!document.querySelector(".character-members")', timeout: 90000 })
  await browser.waitFor(`document.querySelectorAll('.character-members a.variant').length >= 2`, 30000)
  assert(JSON.stringify(await coloured('.character-members a.variant')) === JSON.stringify(want), `事's page colours them ${JSON.stringify(await coloured('.character-members a.variant'))}`)
  const links = await browser.evaluate(`JSON.stringify(${JSON.stringify(lent)}.map(char => { const a = [...document.querySelectorAll('.character-members a.variant')].find(a => a.querySelector('.script-char')?.textContent === char)
    return [decodeURIComponent(a?.getAttribute('href') ?? ''), !!a?.querySelector('.share')] }))`).then(JSON.parse)
  assert(links[0][0].endsWith('/character/U+30FF') && links[1][0].endsWith('/character/U+1B123') && links.every(link => link[1]), `the chips are ${JSON.stringify(links)}`)
  await shoot('character-forms', '.character-members:has(a.variant)')
  console.log('PASS 事\'s page lists the two digraphs among its 字体, shared, as katakana and hiragana')

  // The crop's form picker offers both; ヿ is chosen and saved as the form, the character kept.
  await browser.goto(`${service.base}/en/crop/${encodeURIComponent(id)}`, { waitFor: ready, timeout: 90000 })
  await browser.waitFor(`!!document.querySelector('dialog[open] .form-add') && !document.querySelector('dialog[open] .form-add').disabled`, 30000)
  const onBar = await coloured('dialog[open] .form-chip')
  if (onBar.some(script => script === null)) {
    await browser.evaluate('document.querySelector("dialog[open] .form-add").click()')
    await browser.waitFor(`!!document.querySelector('dialog[open] .form-picker')`)
  }
  await browser.waitFor(`[...document.querySelectorAll('dialog[open] .form-chip .script-char, dialog[open] .form-option .script-char')].some(e => e.textContent === ${JSON.stringify(lent[0])})`, 30000)
    .catch(async () => { throw new Error('the form picker does not offer ヿ: ' + await browser.evaluate(`JSON.stringify([...document.querySelectorAll('dialog[open] .form-chip, dialog[open] .form-option')].map(b => b.textContent.trim().slice(0, 12)))`)) })
  const offered = await coloured('dialog[open] :is(.form-chip, .form-option)')
  assert(JSON.stringify(offered) === JSON.stringify(want), `the form picker colours them ${JSON.stringify(offered)}`)
  await shoot('form-picker', 'dialog[open] .crop-form')
  await browser.evaluate(`[...document.querySelectorAll('dialog[open] .form-chip, dialog[open] .form-option')].find(b => b.querySelector('.script-char')?.textContent === ${JSON.stringify(lent[0])}).click()`)
  await browser.waitFor(`[...document.querySelectorAll('dialog[open] .crop-form .form-chip')].some(c => c.querySelector('.script-char')?.textContent === ${JSON.stringify(lent[0])} && c.getAttribute('aria-pressed') === 'true')`)
  await browser.evaluate('document.querySelector(".save-character").click()')
  await browser.waitFor('document.querySelector("dialog[open]") === null', 30000)
  assert(JSON.stringify(claimed(id)) === JSON.stringify([{ scheme: 'unicode', value: lent[0] }]), `the form was not claimed: ${JSON.stringify(claimed(id))}`)
  assert(units(config.directory)[id].unicode === 'U+4E8B', 'the form changed the crop\'s character')
  console.log('PASS the form picker offers both, and ヿ is saved as the form of a crop that stays 事')

  assert(!errors.length, 'page errors: ' + errors.join('; '))
  console.log('PASS written with')
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
