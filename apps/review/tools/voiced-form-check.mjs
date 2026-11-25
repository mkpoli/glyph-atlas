#!/usr/bin/env bun
/**
 * The inspector's form bar on a crop written as ば, in a real browser, against a disposable dataset.
 *
 * ば's grapheme holds ば, バ, and each hentaigana of は written with U+3099 after it. The bar shows
 * them all inline, wrapping onto further rows, and choosing a sequence saves it as the crop's
 * character, code point by code point.
 *
 * Run through devrun, after `bun run build`:
 *   devrun bun apps/review/tools/voiced-form-check.mjs
 */
import { join } from 'node:path'
import { mkdirSync } from 'node:fs'
import Browser from './browser.mjs'
import { boot, options, units } from './harness.mjs'

const config = options()
const service = await boot(config)
const screenshots = process.env.SHOTS ?? '/tmp/atlas-character-shots'
mkdirSync(screenshots, { recursive: true })
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const ready = 'document.querySelector("dialog[open] .crop-viewport")?.dataset.ready === "true" && !document.querySelector(".save-character")?.disabled'
const chips = 'document.querySelectorAll("dialog[open] .crop-form .form-chip")'
const HA = '\u{1B09E}゙'

try {
  // A crop written as あ is marked ば first, as a reviewer would mark it.
  const [id] = Object.entries(units(config.directory)).filter(([, unit]) => unit.active && unit.unicode === 'U+3042').map(([id]) => id)
  assert(id, 'the fixture has a crop written as あ')
  const crop = await (await fetch(`${service.api}/atlas/characters/${encodeURIComponent(id)}`)).json()
  const marked = await fetch(`${service.api}/layers/units/${encodeURIComponent(id)}`, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ id: crypto.randomUUID(), client_id: 'voiced-form-check', revision: crop.revision, image_sha256: crop.image_sha256,
      verdict: 'wrong', issue: 'character', character: 'ば' }) })
  assert(marked.ok, `marking the crop ば failed: ${marked.status} ${await marked.text()}`)
  assert(units(config.directory)[id].unicode === 'U+3070', 'the crop is not ば')

  browser = await Browser.launch({ width: 1440, height: 1000 })
  await browser.writeAs('voiced-form-check')
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })
  await browser.goto(`${service.base}/en/crop/${encodeURIComponent(id)}`, { waitFor: ready, timeout: 90000 })
  await browser.waitFor(`${chips}.length > 2`, 30000)
  // Every form of the grapheme shows on the bar, wrapping onto further rows.
  const forms = await browser.evaluate(`[...${chips}].map(c => c.querySelector('.script-text').textContent)`)
  assert(forms.length === 13 && new Set(forms).size === 13, `the bar shows ${forms.length} forms, not all 13: ${forms.join(' ')}`)
  assert(forms[0] === 'ば' && forms[1] === 'バ', `the bar does not lead with ば and バ: ${forms.join(' ')}`)
  assert(forms.includes(HA), `the bar lacks 𛂞 + U+3099: ${forms.join(' ')}`)
  const parts = await browser.evaluate(`[...${chips}].find(c => c.textContent.includes(${JSON.stringify(HA)})).querySelectorAll('.script-char').length`)
  assert(parts === 1, `𛂞 + U+3099 is coloured as ${parts} parts`)
  assert(await browser.evaluate(`(() => { const row = document.querySelector('dialog[open] .form-chips'); return row.scrollWidth <= row.clientWidth + 1 })()`), 'the bar overflows its width')
  const overflow = await browser.evaluate('document.querySelector("dialog[open] .form-add").textContent.trim()')
  assert(overflow === '+⌕', `the add button says ${overflow}, expected +⌕`)
  for (const scheme of ['light', 'dark']) {
    await browser.setColorScheme(scheme)
    await Bun.sleep(300)
    await browser.screenshot(join(screenshots, `voiced-form-bar-${scheme}.png`))
  }
  await browser.setColorScheme('light')
  console.log(`PASS the bar shows all ${forms.length} forms: ${forms.join(' ')}`)

  await browser.evaluate(`[...${chips}].find(c => c.textContent.includes(${JSON.stringify(HA)})).click()`)
  await browser.waitFor(`[...${chips}].some(c => c.textContent.includes(${JSON.stringify(HA)}) && c.getAttribute('aria-pressed') === 'true')`)
  await browser.evaluate('document.querySelector(".save-character").click()')
  await browser.waitFor('document.querySelector("dialog[open]") === null', 30000)
  const saved = units(config.directory)[id]
  assert(saved.unicode === 'U+1B09E U+3099', `the crop was not saved as 𛂞 + U+3099: ${saved.unicode}`)
  console.log('PASS choosing 𛂞 + U+3099 saves it as the crop\'s character')

  // The crop, now written as the sequence, opens with ば's forms and the sequence current.
  await browser.goto(`${service.base}/en/crop/${encodeURIComponent(id)}`, { waitFor: ready, timeout: 90000 })
  await browser.waitFor(`${chips}.length > 2`, 30000)
  assert(await browser.evaluate(`[...${chips}].some(c => c.textContent.includes(${JSON.stringify(HA)}) && c.getAttribute('aria-pressed') === 'true')`), 'the saved sequence is not current')
  assert(await browser.evaluate(`[...${chips}][0].textContent.includes('ば')`), 'a crop written as the sequence does not offer ば\'s forms')
  console.log('PASS a crop written as 𛂞 + U+3099 offers ば\'s forms')
  assert(!errors.length, 'page errors: ' + errors.join('; '))
  console.log('PASS voiced forms')
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
