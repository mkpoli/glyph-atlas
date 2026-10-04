#!/usr/bin/env bun
/**
 * The inspector's form bar, in a real browser, against a disposable dataset.
 *
 * The bar offers the crop's grapheme's forms with number keys. Choosing one changes the save to
 * "Save", choosing the crop's own form again changes it back, and the save marks the crop: a member of
 * its grapheme becomes its character, and a form picked from the add-form picker is claimed as its
 * form in the ledger while the crop keeps its character. Each marking is the reader's form claim.
 * Nothing typed is saved as it stands.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/crop-form-check.mjs
 */
import { Database } from 'bun:sqlite'
import { join } from 'node:path'
import { mkdirSync } from 'node:fs'
import Browser from './browser.mjs'
import { boot, events, options, units } from './harness.mjs'

const config = options()
const service = await boot(config)
const shotsAt = process.argv.indexOf('--shots')
const screenshots = shotsAt >= 0 ? process.argv[shotsAt + 1] : '/tmp/atlas-character-shots'
mkdirSync(screenshots, { recursive: true })
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const ready = 'document.querySelector("dialog[open] .crop-viewport")?.dataset.ready === "true" && !document.querySelector(".save-character")?.disabled'
const label = () => browser.evaluate('document.querySelector(".save-character").innerText')
const chips = 'document.querySelectorAll("dialog[open] .crop-form .form-chip")'
// A crop's standing form claims, oldest first, with the value each names.
const claimed = id => {
  const db = new Database(join(config.directory, 'review.sqlite'), { readonly: true })
  try {
    return db.query(`SELECT r.value FROM assertions a JOIN forms f ON f.id=a.object JOIN representations r ON r.id=f.anchor
      WHERE a.subject=? AND a.predicate='has_form' AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract')
      ORDER BY a.rowid`).all(id).map(row => row.value)
  } finally { db.close() }
}
const point = char => 'U+' + char.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')

try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  await browser.writeAs('crop-form-check')
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })
  const open = async id => {
    await browser.goto(`${service.base}/en/crop/${encodeURIComponent(id)}`, { waitFor: ready, timeout: 90000 })
    await browser.waitFor(`${chips}.length > 1`, 30000)
  }
  // Two crops written as あ: one is marked as another member of its grapheme, one as a picked form.
  const stored = units(config.directory)
  const [first, second] = Object.entries(stored).filter(([, unit]) => unit.active && unit.unicode === 'U+3042').map(([id]) => id)
  assert(first && second, 'the fixture has two crops written as あ')

  await open(first)
  const forms = await browser.evaluate(`[...${chips}].map(c => c.querySelector('.script-text').textContent)`)
  assert(forms[0] === 'あ' && forms.length > 1, `the bar does not lead with the grapheme's forms: ${forms.join(' ')}`)
  assert(await browser.evaluate(`${chips}[0].getAttribute('aria-pressed')`) === 'true', 'the crop\'s own form is not shown as current')
  assert(!await browser.evaluate('!!document.querySelector("dialog[open] .form-input, dialog[open] .form-field")'), 'a typed form field remains')
  assert((await label()).includes('Looks right'), 'an untouched crop does not offer Looks right')
  await browser.key('2')
  await browser.waitFor(`${chips}[1].getAttribute('aria-pressed') === 'true'`)
  assert((await label()).startsWith('Save'), `choosing a form does not turn the button into Save: ${await label()}`)
  await browser.screenshot(join(screenshots, 'form-bar-chosen-light.png'))
  await browser.key('1')
  await browser.waitFor(`${chips}[1].getAttribute('aria-pressed') !== 'true'`)
  assert((await label()).includes('Looks right'), 'choosing the crop\'s own form again does not undo the change')
  await browser.key('2')
  const mark = events(config.directory).length
  await browser.evaluate('document.querySelector(".save-character").click()')
  await browser.waitFor('document.querySelector("dialog[open]") === null', 30000)
  const marked = units(config.directory)[first]
  assert(marked.unicode === point(forms[1]), `the crop was not marked as ${forms[1]}: ${marked.unicode}`)
  assert(events(config.directory).slice(mark).some(e => e.target_id === first && e.field === 'review'), 'the save recorded no review')
  assert(JSON.stringify(claimed(first)) === JSON.stringify([forms[1]]), `the form was not claimed: ${claimed(first)}`)
  console.log(`PASS a member chosen with its number key marks the crop as ${forms[1]}`)

  // The crop, now checked, can be marked again: back to あ, in one review.
  await open(first)
  const again = events(config.directory).length
  await browser.evaluate(`[...${chips}].find(c => c.textContent.includes('あ')).click()`)
  assert((await label()).startsWith('Save'), 'choosing あ on a crop marked otherwise does not offer Save')
  await browser.evaluate('document.querySelector(".save-character").click()')
  await browser.waitFor('document.querySelector("dialog[open]") === null', 30000)
  assert(units(config.directory)[first].unicode === 'U+3042', `a checked crop could not be marked again: ${units(config.directory)[first].unicode}`)
  const reviews = events(config.directory).slice(again).filter(e => e.target_id === first && e.field === 'review')
  assert(reviews.length === 1, `marking a form wrote ${reviews.length} reviews`)
  assert(JSON.stringify(claimed(first)) === JSON.stringify(['あ']), `the new claim did not replace the reader's own: ${claimed(first)}`)
  console.log('PASS a checked crop is marked again, with one review')

  // Another form comes from the picker: its sections and the character search. It is claimed as the
  // crop's form; the crop keeps its character.
  await open(second)
  await browser.evaluate('document.querySelector("dialog[open] .form-add").click()')
  await browser.waitFor('!!document.querySelector("dialog[open] .form-picker .character-search input")')
  await browser.screenshot(join(screenshots, 'form-picker-light.png'))
  await browser.key('Escape')
  await browser.waitFor('!document.querySelector("dialog[open] .form-picker")')
  assert(await browser.evaluate('!!document.querySelector("dialog[open]")'), 'Escape in the picker closed the inspector')
  await browser.evaluate('document.querySelector("dialog[open] .form-add").click()')
  await browser.waitFor('!!document.querySelector("dialog[open] .form-picker .character-search input")')
  await browser.evaluate(`(() => { const i = document.querySelector('dialog[open] .form-picker input'); i.focus(); i.value = 'ゑ'; i.dispatchEvent(new Event('input', { bubbles: true })) })()`)
  await browser.waitFor(`[...document.querySelectorAll('dialog[open] .form-picker .candidate')].some(c => c.textContent.includes('U+3091'))`, 20000)
  await browser.evaluate(`[...document.querySelectorAll('dialog[open] .form-picker .candidate')].find(c => c.textContent.includes('U+3091')).click()`)
  await browser.waitFor(`[...${chips}].some(c => c.textContent.includes('ゑ') && c.getAttribute('aria-pressed') === 'true')`)
  assert(!await browser.evaluate('!!document.querySelector("dialog[open] .form-picker")'), 'the picker stays open after a pick')
  assert((await label()).startsWith('Save'), 'a picked form does not turn the button into Save')
  await browser.evaluate('document.querySelector(".save-character").click()')
  await browser.waitFor('document.querySelector("dialog[open]") === null', 30000)
  const formed = units(config.directory)[second]
  assert(JSON.stringify(claimed(second)) === JSON.stringify(['ゑ']) && formed.unicode === 'U+3042', `the picked form was not claimed alone: ${claimed(second)} ${formed.unicode}`)
  console.log('PASS a form picked from the picker is claimed as the crop\'s form, the character kept')

  // The crop's claimed form leads the bar's own forms when it is none of them, and is current.
  await open(second)
  assert(await browser.evaluate(`[...${chips}].some(c => c.textContent.includes('ゑ') && c.getAttribute('aria-pressed') === 'true')`), 'the recorded form is not shown as current')
  await browser.setViewport(390, 844)
  assert(await browser.evaluate('document.querySelector("dialog").scrollWidth <= innerWidth + 1'), 'the form bar overflows on a phone')
  await browser.screenshot(join(screenshots, 'form-bar-phone-light.png'))
  await browser.setColorScheme('dark')
  await browser.screenshot(join(screenshots, 'form-bar-phone-dark.png'))
  await browser.setColorScheme('light')
  console.log('PASS the claimed form shows as current, and the bar fits a phone')

  // A shape Unicode lacks is written as a description: typed, checked as typed, a part chosen in its
  // structure and replaced through the component search, then claimed as the crop's form.
  await browser.setViewport(1440, 1000)
  await open(second)
  await browser.evaluate('document.querySelector("dialog[open] .form-add").click()')
  await browser.waitFor('!!document.querySelector("dialog[open] .form-compose")')
  await browser.evaluate('document.querySelector("dialog[open] .form-compose").click()')
  await browser.waitFor('!!document.querySelector("dialog[open] .ids-field input")')
  const typeIds = text => browser.evaluate(`(() => { const i = document.querySelector('dialog[open] .ids-field input'); i.focus(); i.value = ${JSON.stringify(text)}; i.dispatchEvent(new Event('input', { bubbles: true })) })()`)
  await typeIds('⿰⿱匕矢')
  await browser.waitFor('!!document.querySelector("dialog[open] .ids-problem")')
  assert(await browser.evaluate('document.querySelector("dialog[open] .ids-use").disabled'), 'an unfinished description can be used')
  await typeIds('⿰⿱匕矢⿱コ疋')
  await browser.waitFor('!document.querySelector("dialog[open] .ids-problem") && !!document.querySelector("dialog[open] .ids-tree")')
  await browser.evaluate(`[...document.querySelectorAll('dialog[open] .ids-part')].find(b => b.textContent === '矢').click()`)
  await browser.waitFor(`document.querySelector('dialog[open] .ids-part.chosen')?.textContent === '矢'`)
  await browser.evaluate(`(() => { const i = document.querySelector('dialog[open] .ids-search input'); i.focus(); i.value = '失'; i.dispatchEvent(new Event('input', { bubbles: true })) })()`)
  await browser.waitFor(`[...document.querySelectorAll('dialog[open] .ids-search .candidate')].some(c => c.textContent.includes('U+5931'))`, 20000)
  await browser.evaluate(`[...document.querySelectorAll('dialog[open] .ids-search .candidate')].find(c => c.textContent.includes('U+5931')).click()`)
  await browser.waitFor(`document.querySelector('dialog[open] .ids-field input').value === '⿰⿱匕失⿱コ疋'`)
  await browser.screenshot(join(screenshots, 'ids-composer-light.png'))
  await browser.setColorScheme('dark')
  await browser.screenshot(join(screenshots, 'ids-composer-dark.png'))
  await browser.setColorScheme('light')
  await browser.evaluate('document.querySelector("dialog[open] .ids-use").click()')
  await browser.waitFor(`[...${chips}].some(c => c.textContent.includes('⿰⿱匕失⿱コ疋') && c.querySelector('.ids-tag') && c.getAttribute('aria-pressed') === 'true')`)
  await browser.evaluate('document.querySelector(".save-character").click()')
  await browser.waitFor('document.querySelector("dialog[open]") === null', 30000)
  assert(JSON.stringify(claimed(second)) === JSON.stringify(['⿰⿱匕失⿱コ疋']), `the description was not claimed: ${claimed(second)}`)
  assert(units(config.directory)[second].unicode === 'U+3042', 'a description changed the crop\'s character')
  console.log('PASS a form written as a description is composed, checked and claimed')

  assert(!errors.length, 'page errors: ' + errors.join('; '))
  console.log('PASS crop form')
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
