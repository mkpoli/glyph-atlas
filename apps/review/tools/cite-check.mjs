#!/usr/bin/env bun
/**
 * The Cite control, in a real browser at phone width, against a disposable dataset.
 *
 * A crop's inspector and a character's page each open a citation of what they show: a plain-text
 * citation in the page's language, BibTeX and CSL-JSON, each with its own copy button, all inside a
 * 390 px screen. Escape closes the citation and leaves the inspector open.
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
const read = () => browser.evaluate(`(() => { const d = document.querySelector('${cite}'); return d && { text: d.querySelector('.cite-text').textContent,
  formats: [...d.querySelectorAll('pre')].map(p => p.textContent), copies: d.querySelectorAll('.cite-copy').length,
  right: d.getBoundingClientRect().right, overflow: d.scrollWidth - d.clientWidth } })()`)

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
  const address = `/crop/${encodeURIComponent(unit)}`
  await browser.goto(`${service.base}/en${address}`, { waitFor: `${shown} === ${JSON.stringify(unit)}`, timeout: 90000 })
  await browser.waitFor('document.documentElement.dataset.hydrated !== undefined', 60000)
  await browser.waitFor('document.querySelector("dialog.character-dialog[open] [data-cite]") !== null')
  const crop = await openCite('dialog.character-dialog[open] [data-cite]')
  assert(crop.text.includes(`crop ${unit}`) && crop.text.includes(`${service.base}${address}`), `the citation names the crop and its address: ${crop.text}`)
  assert(/Glyph Atlas\. .* \(accessed \d{1,2} [A-Z][a-z]+ \d{4}\)\./.test(crop.text), `the citation is dated the British way: ${crop.text}`)
  assert(crop.formats[0].startsWith('@misc{glyphatlas:crop:'), 'BibTeX follows')
  const [item] = JSON.parse(crop.formats[1])
  // A crop with an evidence version is cited at it, and the address names the version.
  const cited = item.version ? `${service.base}${address}?v=${item.version.split('@')[1].slice(0, 12)}-${item.version.split('@')[2].split(',').join('-')}` : `${service.base}${address}`
  assert(item.URL === cited && item['container-title'] === 'Glyph Atlas' && item.accessed['date-parts'][0].length === 3, `CSL-JSON follows: ${crop.formats[1]}`)
  assert(crop.text.includes(cited), `the text cites the same address: ${crop.text}`)
  assert(crop.copies === 3, `each format has its copy button (${crop.copies})`)
  assert(crop.right <= 390 && crop.overflow <= 0, `the citation fits the phone (right ${crop.right}, overflow ${crop.overflow})`)
  console.log('PASS a crop is cited in three formats at phone width')

  await browser.key('Escape')
  await browser.waitFor(`document.querySelector('${cite}') === null`)
  assert(await browser.evaluate(shown) === unit, 'Escape closes the citation and keeps the inspector')
  console.log('PASS Escape closes the citation alone')

  await browser.goto(`${service.base}/ja${address}`, { waitFor: `${shown} === ${JSON.stringify(unit)}`, timeout: 90000 })
  await browser.waitFor('document.documentElement.dataset.hydrated !== undefined', 60000)
  const japanese = await openCite('dialog.character-dialog[open] [data-cite]')
  assert(japanese.text.includes(`切り出し${unit}`) && /（\d{4}年\d{1,2}月\d{1,2}日閲覧）。/.test(japanese.text), `the Japanese citation: ${japanese.text}`)
  console.log('PASS the citation follows the page\'s language')

  const codePoint = 'U+' + item.title.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')
  // The fixture service reads its character layers on first use, which can outlast the page's own wait.
  for (let attempt = 0; attempt < 3; attempt++) {
    await browser.goto(`${service.base}/en/character/${codePoint}?scope=exact`, { timeout: 90000 })
    if (await browser.evaluate('document.querySelector(".forms-row [data-cite]") !== null')) break
  }
  await browser.waitFor('document.documentElement.dataset.hydrated !== undefined', 60000)
  const form = await openCite('.forms-row [data-cite]')
  assert(form.text.includes(`(${codePoint}), form. Glyph Atlas. ${service.base}/character/${codePoint}?scope=exact`), `the form's citation: ${form.text}`)
  assert(form.right <= 390 && form.overflow <= 0, 'the form\'s citation fits the phone')
  console.log('PASS a character page cites the form it shows')

  assert(!errors.length, 'page errors: ' + errors.join('; '))
  console.log('PASS cite')
} catch (error) {
  console.log('at', await browser?.evaluate('location.href + " " + (document.querySelector("main")?.textContent ?? "").slice(0, 300)'))
  throw error
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
