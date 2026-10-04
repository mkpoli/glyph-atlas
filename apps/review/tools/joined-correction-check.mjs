#!/usr/bin/env bun
/**
 * What a joined crop reads, typed in full, in a real browser against a disposable dataset.
 *
 * A joined crop's characters are often none of the suggestions, and the search answers one character
 * per row. Enter on a typed run of characters takes the run as it stands; a row is still taken by
 * clicking it or reaching it with the arrows. The saved review carries the run as its correction.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/joined-correction-check.mjs
 */
import { join } from 'node:path'
import { mkdirSync } from 'node:fs'
import Browser from './browser.mjs'
import { boot, events, options } from './harness.mjs'

const config = options()
const service = await boot(config)
const screenshots = '/tmp/atlas-joined-correction'
mkdirSync(screenshots, { recursive: true })
const assert = (condition, message) => { if (!condition) throw new Error(message) }
let browser
try {
  browser = await Browser.launch({ width: 1280, height: 1000 })
  await browser.writeAs('joined-correction-check')
  const click = async selector => {
    await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`)
    await browser.waitFor(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); return el !== null && !el.disabled })()`)
    const p = await browser.centre(selector); await browser.click(p.x, p.y)
  }
  const type = async text => { await browser.send('Input.insertText', { text }); await Bun.sleep(60) }
  const picked = 'document.querySelector("dialog .suggestion-choice.picked")?.innerText.replace("×", "").trim() ?? ""'
  const search = 'dialog .suggestion-pick input'
  const settled = `!document.querySelector(${JSON.stringify(search)}).getAttribute('aria-busy')?.includes('true')`

  await browser.goto(service.base + '/en', { waitFor: 'document.querySelectorAll(".glyph-tile[data-unit]").length > 2' })
  const id = await browser.evaluate('document.querySelector(".glyph-grid [data-unit]").dataset.unit')
  await click(`.glyph-grid [data-unit="${id}"]`)
  await browser.waitFor('document.querySelector("dialog[open] .crop-viewport")?.dataset.ready === "true" && !document.querySelector(".save-character")?.disabled')
  await click('dialog .issue-card[data-issue="merged"]')
  await browser.waitFor(`document.querySelector(${JSON.stringify(search)}) !== null`)

  // The whole run, typed and committed with Enter.
  await click(search)
  await type('イ．アノ')
  await browser.waitFor(settled)
  await browser.key('Enter')
  await browser.waitFor(`${picked} !== ""`, 5000).catch(() => null)
  const whole = await browser.evaluate(picked)
  assert(whole === 'イ．アノ', `Enter took ${JSON.stringify(whole)}, not the typed run`)
  console.log('PASS Enter takes a typed run as it stands')

  // A further run goes on after it, as a picked character does.
  await click(search)
  await type('ヌ')
  await browser.waitFor(settled)
  await browser.key('ArrowDown')
  await browser.key('Enter')
  await browser.waitFor(`[...${picked}].length > 4`)
  const after = await browser.evaluate(picked)
  assert(after.startsWith('イ．アノ') && [...after].length === 5, `a row picked after the run gave ${JSON.stringify(after)}`)
  console.log('PASS a row picked with the arrows follows the typed run')

  await browser.screenshot(join(screenshots, 'joined-light.png'))
  await browser.setColorScheme('dark')
  await browser.screenshot(join(screenshots, 'joined-dark.png'))
  await browser.setColorScheme('light')

  await click('.save-character')
  await browser.waitFor('document.querySelector("dialog[open]") === null')
  const review = events(config.directory).filter(e => e.target_id === id && e.field === 'review').pop()
  const evidence = JSON.parse(review?.evidence ?? '{}')
  assert(evidence.issue === 'merged' && evidence.suggested_text === after, `the review saved ${JSON.stringify(evidence)}`)
  console.log('PASS the review carries the typed run as its correction')
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
