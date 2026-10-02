#!/usr/bin/env bun
/**
 * One crop review panel, in a real browser, against a disposable dataset.
 *
 * The inspector and Quick Review's review step judge a crop with the same panel: the problem cards
 * answer W, M, B and X, S skips, ← and → step through the inspector's list, and another character is
 * picked from the search rather than typed.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/crop-review-check.mjs
 */
import Browser from './browser.mjs'
import { boot, events, options, units } from './harness.mjs'

const config = options()
const service = await boot(config)
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const shown = 'document.querySelector("dialog[open] .record-id code")?.textContent'
const ready = 'document.querySelector("dialog[open] .crop-viewport")?.dataset.ready === "true" && !document.querySelector(".save-character")?.disabled'
const pressed = issue => `document.querySelector('dialog[open] [data-issue="${issue}"]')?.getAttribute('aria-pressed') === 'true'`

try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  await browser.writeAs('crop-review-check')
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })
  await browser.goto(`${service.base}/en`, { waitFor: 'document.querySelectorAll(".glyph-grid [data-unit]").length > 3', timeout: 90000 })
  const ids = await browser.evaluate('[...document.querySelectorAll(".glyph-grid [data-unit]")].map(t => t.dataset.unit)')
  const id = ids.find(each => units(config.directory)[each]?.unicode !== 'U+30AB')
  await browser.evaluate(`document.querySelector('.glyph-grid [data-unit="${id}"]').click()`)
  await browser.waitFor(ready, 60000)

  for (const [key, issue] of [['w', 'reading'], ['m', 'merged'], ['b', 'crop'], ['x', 'blank']]) {
    await browser.key(key)
    await browser.waitFor(pressed(issue))
  }
  assert(!await browser.evaluate('!!document.querySelector("dialog[open] .skip-character")'), 'the inspector keeps a second Skip in its footer')
  assert(await browser.evaluate('!!document.querySelector("dialog[open] .crop-review [data-issue=skip]")'), 'the inspector has no Skip card')
  console.log('PASS W M B X choose the problem cards')

  const position = await browser.evaluate(shown)
  await browser.key('ArrowRight')
  await browser.waitFor(`${shown} && ${shown} !== ${JSON.stringify(position)}`)
  await browser.key('ArrowLeft')
  await browser.waitFor(`${shown} === ${JSON.stringify(position)}`)
  await browser.waitFor(ready, 60000)
  console.log('PASS ← and → step through the list')

  // Another character is picked from the search; nothing typed is saved as it stands.
  await browser.key('w')
  await browser.waitFor('!!document.querySelector("dialog[open] .suggestion-pick input")')
  assert(!await browser.evaluate('!!document.querySelector("dialog[open] .typed-choice")'), 'a free text field is still offered')
  await browser.evaluate(`(() => { const i = document.querySelector('dialog[open] .suggestion-pick input'); i.focus(); i.value = 'カ'; i.dispatchEvent(new Event('input', { bubbles: true })) })()`)
  await browser.waitFor(`[...document.querySelectorAll('dialog[open] .suggestion-pick .candidate')].some(c => c.textContent.includes('U+30AB'))`, 20000)
  await browser.evaluate(`[...document.querySelectorAll('dialog[open] .suggestion-pick .candidate')].find(c => c.textContent.includes('U+30AB')).click()`)
  await browser.waitFor('document.querySelector("dialog[open] .suggestion-choice.picked")?.textContent.includes("カ")')
  await browser.evaluate('document.querySelector(".save-character").click()')
  await browser.waitFor('document.querySelector("dialog[open]") === null', 30000)
  assert(units(config.directory)[id].unicode === 'U+30AB', `the picked character was not saved: ${units(config.directory)[id].unicode}`)
  console.log('PASS another character is picked from the search and saved')

  // Under joined characters the characters are picked one after another.
  const other = ids.find(each => each !== id)
  await browser.evaluate(`document.querySelector('.glyph-grid [data-unit="${other}"]').click()`)
  await browser.waitFor(ready, 60000)
  await browser.key('m')
  await browser.waitFor('!!document.querySelector("dialog[open] .suggestion-pick input")')
  for (const [char, point] of [['ア', 'U+30A2'], ['カ', 'U+30AB']]) {
    await browser.evaluate(`(() => { const i = document.querySelector('dialog[open] .suggestion-pick input'); i.focus(); i.value = '${char}'; i.dispatchEvent(new Event('input', { bubbles: true })) })()`)
    await browser.waitFor(`[...document.querySelectorAll('dialog[open] .suggestion-pick .candidate')].some(c => c.textContent.includes('${point}'))`, 20000)
    await browser.evaluate(`[...document.querySelectorAll('dialog[open] .suggestion-pick .candidate')].find(c => c.textContent.includes('${point}')).click()`)
  }
  await browser.waitFor('document.querySelector("dialog[open] .suggestion-choice.picked")?.textContent.includes("アカ")')
  assert(await browser.evaluate(pressed('merged')), 'picking a character left joined characters')
  await browser.evaluate('document.querySelector("dialog[open] .close-inspector").click()')
  await browser.waitFor('document.querySelector("dialog[open]") === null')
  console.log('PASS joined characters are picked one after another')

  // The Skip card closes without writing.
  await browser.evaluate(`document.querySelector('.glyph-grid [data-unit="${other}"]').click()`)
  await browser.waitFor(ready, 60000)
  const skipMark = events(config.directory).length
  await browser.key('s')
  await browser.waitFor('document.querySelector("dialog[open]") === null')
  assert(events(config.directory).length === skipMark, 'Skip wrote something')
  console.log('PASS S skips without writing')

  // Quick Review's review step answers the same keys.
  await browser.goto(`${service.base}/en/review?grapheme=U%2B3042`, { waitFor: 'document.querySelectorAll(".quiz-tile img").length > 1 && !document.querySelector(".quiz-submit .primary")?.disabled', timeout: 90000 })
  await browser.waitFor('[...document.querySelectorAll(".quiz-tile img")].every(i => i.complete)', 30000)
  await browser.evaluate('document.querySelectorAll(".quiz-choice")[0].click()')
  await browser.evaluate('document.querySelectorAll(".quiz-choice")[1].click()')
  await browser.evaluate('document.querySelector(".review-selected").click()')
  await browser.waitFor('!!document.querySelector(".quiz-focus .crop-review")')
  await browser.key('m')
  await browser.waitFor(`document.querySelector('.quiz-focus [data-issue="merged"]')?.getAttribute('aria-pressed') === 'true'`)
  await browser.key('s')
  await browser.waitFor(`document.querySelector('.quiz-focus [data-issue="skip"]')?.getAttribute('aria-pressed') === 'true' || document.querySelector('.focus-thumb.skipped') !== null`)
  console.log('PASS Quick Review answers the same keys with the same panel')

  assert(!errors.length, 'page errors: ' + errors.join('; '))
  console.log('PASS crop review')
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
