#!/usr/bin/env bun
/**
 * The counts of an open gallery through a save, in a real browser, against a disposable fixture: a
 * confirmed crop moves none of them, and a crop relabelled out of the gallery takes one from the header,
 * the chip and the line above the gallery, which then agree again.
 *
 *   devrun bun apps/review/tools/gallery-count-check.mjs
 */
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'

const config = options(), service = await boot(config)
const assert = (condition, message) => { if (!condition) throw new Error(message) }
let browser
const numbers = `(() => { const n = text => Number((/[\\d,]+/.exec(text ?? '') ?? ['0'])[0].replace(/,/g, ''))
  return JSON.stringify([n(document.querySelector('.collection-meta')?.innerText), n(document.querySelector('.member[aria-current] .use')?.innerText), n(document.querySelector('.find-count')?.innerText)]) })()`
const counts = async () => JSON.parse(await browser.evaluate(numbers))
const ready = `document.querySelector('dialog[open] .crop-viewport')?.dataset.ready === 'true' && !document.querySelector('.save-character')?.disabled`
const closed = `document.querySelector('dialog[open]') === null`
try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  await browser.writeAs('gallery-count-check')
  for (let attempt = 0; attempt < 4; attempt++) {
    // The fixture's service reads its character layers on first use.
    await browser.goto(`${service.base}/en/character/U+3042`, { timeout: 90000 })
    if (await browser.evaluate(`document.querySelectorAll('.glyph-tile[data-unit]').length > 1 && !!document.querySelector('.member[aria-current] .use')`)) break
  }
  await browser.waitFor(`document.documentElement.dataset.hydrated !== undefined && document.querySelectorAll('.glyph-tile[data-unit]').length > 1`, 90000)
  const before = await counts()
  assert(before[0] > 2 && before.every(n => n === before[0]), `the gallery opens on ${before}`)
  const open = async () => {
    await browser.evaluate(`document.querySelector('.glyph-tile[data-unit]').click()`)
    await browser.waitFor(ready, 30000)
  }
  await open()
  await browser.evaluate(`document.querySelector('.save-character').click()`)
  await browser.waitFor(closed, 30000)
  await Bun.sleep(4000)
  assert(JSON.stringify(await counts()) === JSON.stringify(before), `a confirmed crop left ${await counts()}, from ${before}`)
  console.log('PASS a confirmed crop moves no count')

  await open()
  await browser.evaluate(`document.querySelector('dialog[open] .issue-card[data-issue="character"]').click()`)
  await browser.waitFor(`!!document.querySelector('dialog[open] .suggestion-pick input')`, 20000)
  await browser.evaluate(`(() => { const input = document.querySelector('dialog[open] .suggestion-pick input'); input.focus(); input.value = 'ヌ'; input.dispatchEvent(new Event('input', { bubbles: true })) })()`)
  const pick = `[...document.querySelectorAll('dialog[open] .suggestion-pick .candidate')].find(c => c.textContent.includes('U+30CC'))`
  await browser.waitFor(`!!${pick}`, 30000)
  await browser.evaluate(`${pick}.click()`)
  await browser.waitFor(`!!document.querySelector('dialog[open] .suggestion-choice.picked')`)
  await browser.evaluate(`document.querySelector('.save-character').click()`)
  await browser.waitFor(closed, 30000)
  const after = before.map(n => n - 1)
  await browser.waitFor(`${numbers} === ${JSON.stringify(JSON.stringify(after))}`, 30000)
    .catch(async () => { throw new Error(`a relabelled crop left ${await counts()}, wanted ${after}`) })
  console.log(`PASS a relabelled crop takes one from each count (${before[0]} → ${after[0]})`)
  console.log('PASS gallery counts')
} finally { await browser?.close(); await service.stop({ keep: config.keep }) }
