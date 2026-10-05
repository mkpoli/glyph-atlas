#!/usr/bin/env bun
/**
 * The Cite control, in a real browser at phone width, against a disposable dataset.
 *
 * A crop's inspector and a character's page each open a citation of what they show: a sentence in the
 * page's language that reads as a citation (what it shows, its source, the site, the day, one short
 * address), and BibTeX, CSL-JSON and Hayagriva, one at a time under tabs with one copy button, all in a
 * short dialog inside a 390 px screen. Escape closes the citation and leaves the inspector open.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/cite-check.mjs
 */
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'

const config = options()
const service = await boot(config)
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const unit = service.fixture?.unit ?? 'doc-1:p1:line0:u0'
const shown = 'document.querySelector("dialog.character-dialog[open] .record-id code")?.textContent'
const cite = '.cite-dialog[open]'
const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const read = () => browser.evaluate(`(() => { const d = document.querySelector('${cite}'); if (!d) return null; const r = d.getBoundingClientRect()
  return { text: d.querySelector('.cite-text')?.textContent ?? null, pre: d.querySelector('pre')?.textContent ?? null,
    tabs: [...d.querySelectorAll('[role=tab]')].map(tab => tab.textContent), copies: d.querySelectorAll('.cite-copy').length,
    right: r.right, height: r.height, overflow: d.scrollWidth - d.clientWidth } })()`)
async function tab(name) {
  await browser.evaluate(`[...document.querySelectorAll('${cite} [role=tab]')].find(tab => tab.textContent === ${JSON.stringify(name)}).click()`)
  await browser.waitFor(`document.querySelector('${cite} [role=tab][aria-selected=true]')?.textContent === ${JSON.stringify(name)}`)
  return read()
}
// A real click: a dialog opened without the reader's own gesture is closed together with the one under it.
async function openCite(selector) {
  await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({ block: 'center' })`)
  const at = await browser.centre(selector)
  await browser.click(at.x, at.y)
  await browser.waitFor(`document.querySelector('${cite}') !== null`)
  return read()
}

try {
  browser = await Browser.launch({ width: 390, height: 844 })
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })
  const address = `/crop/${unit}`
  await browser.goto(`${service.base}/en${address}`, { waitFor: `${shown} === ${JSON.stringify(unit)}`, timeout: 90000 })
  await browser.waitFor('document.querySelector("dialog.character-dialog[open] [data-cite]") !== null')
  const crop = await openCite('dialog.character-dialog[open] [data-cite]')
  // What the crop shows and where it comes from first, then the site, the day and one short address.
  const sentence = new RegExp(`^Crop “あ”, Calibration book A, p\\. 1\\. Glyph Atlas, accessed \\d{1,2} [A-Z][a-z]+ \\d{4}, ${escape(service.base + address)}\\?v=[0-9a-z]{6}$`)
  assert(sentence.test(crop.text), `the citation reads as one: ${crop.text}`)
  assert(crop.tabs.join() === 'Plain text,BibTeX,CSL-JSON,Hayagriva' && crop.copies === 1 && crop.pre === null, `one format at a time: ${crop.tabs}`)
  assert(crop.right <= 390 && crop.overflow <= 0 && crop.height < 420, `the dialog is short at phone width (right ${crop.right}, height ${crop.height})`)
  const url = crop.text.slice(crop.text.lastIndexOf(' ') + 1), key = `glyphatlas-あ-${url.slice(-6)}`
  const bib = await tab('BibTeX')
  assert(bib.pre.startsWith(`@misc{${key},`) && bib.pre.includes(`url = {${url}}`), `BibTeX: ${bib.pre}`)
  const [item] = JSON.parse((await tab('CSL-JSON')).pre)
  assert(item.URL === url && item.id === key && item.accessed['date-parts'][0].length === 3, `CSL-JSON: ${JSON.stringify(item)}`)
  const yml = await tab('Hayagriva')
  assert(yml.pre.startsWith(`${key}:\n  type: "entry"`) && yml.pre.includes(`value: "${url}"`), `Hayagriva: ${yml.pre}`)
  console.log('PASS a crop is cited as a sentence and in three formats, one at a time, in a short dialog')

  await browser.key('Escape')
  await browser.waitFor(`document.querySelector('${cite}') === null`)
  assert(await browser.evaluate(shown) === unit, 'Escape closes the citation and keeps the inspector')
  console.log('PASS Escape closes the citation alone')

  await browser.goto(`${service.base}/ja${address}`, { waitFor: `${shown} === ${JSON.stringify(unit)}`, timeout: 90000 })
  const japanese = await openCite('dialog.character-dialog[open] [data-cite]')
  assert(/^「あ」字形、『Calibration book A』1頁。『字形大図譜（Glyph Atlas）』、\d{4}年\d{1,2}月\d{1,2}日閲覧、http\S+\?v=[0-9a-z]{6}$/.test(japanese.text), `the Japanese citation: ${japanese.text}`)
  console.log('PASS the citation follows the page\'s language')

  const codePoint = 'U+' + item.title.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')
  // The fixture service reads its character layers on first use, which can outlast the page's own wait.
  for (let attempt = 0; attempt < 3; attempt++) {
    await browser.goto(`${service.base}/en/character/${codePoint}?scope=exact`, { timeout: 90000 })
    if (await browser.evaluate('document.querySelector(".forms-row [data-cite]") !== null')) break
  }
  const form = await openCite('.forms-row [data-cite]')
  assert(form.text.startsWith(`Form “${item.title}” (${codePoint}). Glyph Atlas, accessed `) && form.text.endsWith(`, ${service.base}/character/${codePoint}?scope=exact`), `the form's citation: ${form.text}`)
  assert(form.right <= 390 && form.overflow <= 0, 'the form\'s citation fits the phone')
  console.log('PASS a character page cites the form it shows')

  assert(!errors.length, 'page errors: ' + errors.join('; '))
  console.log('PASS cite')
} catch (error) {
  console.log('at', await browser?.evaluate('location.href'))
  throw error
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
