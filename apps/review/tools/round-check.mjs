#!/usr/bin/env bun
/**
 * A Quick Review round, in a real browser, against a disposable dataset.
 *
 * A round carries its grapheme through a material with nothing to deal and a step back through the
 * history, and a round the service refuses says so in the reader's language, never in the service's
 * English. The local service has no accounts, so the browser's anonymous sign-in is answered here, and
 * every round it posts is refused here: nothing is written.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/round-check.mjs
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'

const config = options()
const service = await boot(config)
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const koKore = JSON.parse(readFileSync(join(import.meta.dir, '../src/locales/ko-Kore.json'), 'utf8'))
const json = (requestId, status, value) => browser.send('Fetch.fulfillRequest', { requestId, responseCode: status,
  responseHeaders: [{ name: 'Content-Type', value: 'application/json' }], body: Buffer.from(JSON.stringify(value)).toString('base64') })

try {
  browser = await Browser.launch({ width: 1280, height: 1000 })
  const posted = [], errors = []
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*/api/auth/sign-in/anonymous' }, { urlPattern: '*/atlas/rounds' }, { urlPattern: '*/suggestions?*' }] })
  browser.listeners.push(m => {
    if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text)
    if (m.method !== 'Fetch.requestPaused') return
    const { requestId, request } = m.params
    if (request.url.includes('/api/auth/')) json(requestId, 200, { token: 'round-check', user: { id: 'round-check', name: 'Anonymous', isAnonymous: true } })
    else if (request.url.includes('/atlas/rounds')) { posted.push(JSON.parse(request.postData || '{}')); json(requestId, 422, { detail: 'A round names its grapheme.' }) }
    else json(requestId, 200, { status: 'ready', candidates: [] })
  })
  const material = value => browser.evaluate(`(() => { const s = document.querySelector('.review-material select'); s.value = ${JSON.stringify(value)}; s.dispatchEvent(new Event('change', { bubbles: true })) })()`)
  const settled = '!document.querySelector(".review-material select").disabled && document.querySelector(".quiz-grid")?.getAttribute("aria-busy") !== "true"'
  const target = () => browser.evaluate('document.querySelector(".target-character").innerText')
  const click = async selector => {
    await browser.waitFor(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); return el !== null && !el.disabled })()`)
    await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({ block: 'center' })`)
    const p = await browser.centre(selector); await browser.click(p.x, p.y)
  }

  await browser.goto(`${service.base}/ko-Kore/review?grapheme=U%2B3042`, { waitFor: settled + ' && document.querySelectorAll(".quiz-tile").length > 0', timeout: 60000 })
  assert(await target() === 'あ', 'the address deals あ')
  // A material with nothing to deal empties the round; the next material deals a new one.
  await material('inscribed')
  await browser.waitFor(settled + ' && document.querySelectorAll(".quiz-tile").length === 0')
  await material('not:printed/type')
  await browser.waitFor(settled + ' && document.querySelectorAll(".quiz-tile").length > 0')
  // Back through the history to the first round.
  await browser.evaluate(`[...document.querySelectorAll('.history-character')].find(b => b.innerText === 'あ' && !b.classList.contains('current'))?.click()`)
  await browser.waitFor(settled + ` && document.querySelector(".target-character").innerText === "あ"`)
  console.log('ok   a material with nothing to deal, then back to あ through the history')

  await browser.waitFor('[...document.querySelectorAll(".quiz-tile img")].length > 0 && [...document.querySelectorAll(".quiz-tile img")].every(i => i.complete)', 20000)
  await click('.quiz-tile:not(.unavailable):not(.recorded) .quiz-choice')
  await click('.review-selected')
  await browser.waitFor('!!document.querySelector(".issue-picker")')
  await click('.quiz-workspace [data-issue="blank"]')
  await click('.save-round')
  await browser.waitFor('!!document.querySelector(".quiz-workspace .error-message")', 20000)
  assert(posted.length === 1 && posted[0].grapheme === 'U+3042', `the round names its grapheme (${JSON.stringify(posted.map(p => p.grapheme))})`)
  console.log('ok   the saved round names U+3042')
  const refusal = await browser.evaluate('document.querySelector(".quiz-workspace .error-message span").innerText')
  assert(refusal === koKore['quiz.roundNotSaved'], `a refused round is reported in the reader's language, got "${refusal}"`)
  assert(await browser.evaluate('document.querySelector(".quiz-workspace [data-issue=blank]")?.getAttribute("aria-pressed") === "true"'), 'a refused round keeps its choices')
  console.log(`ok   a refused round reads "${refusal}" and keeps its choices`)
  assert(errors.length === 0, 'browser exceptions: ' + errors.join(', '))
  console.log('\nround-check passed')
} catch (error) {
  if (browser) await browser.screenshot('/tmp/round-check-failure.png').catch(() => {})
  console.log('Service log:', service.log.slice(-6).join('').slice(-4000))
  throw error
} finally { await browser?.close(); await service.stop() }
