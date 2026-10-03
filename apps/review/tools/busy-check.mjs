#!/usr/bin/env bun
// What a reader sees while the site's database is busy (an import holds it): the hosted Worker answers
// 503 with `code: 'busy'`. The fixture service never does, so the browser's requests are answered busy
// here, over the DevTools protocol. Run through devrun: `devrun bun apps/review/tools/busy-check.mjs`.
import { join } from 'node:path'
import { mkdirSync } from 'node:fs'
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'

const config = options(), service = await boot(config)
const screenshots = '/tmp/atlas-busy-shots'
mkdirSync(screenshots, { recursive: true })
const assert = (condition, message) => { if (!condition) throw new Error(message) }
let browser
try {
  browser = await Browser.launch({ width: 1280, height: 900 })
  await browser.writeAs('busy-check')
  // `busy(test)` answers each request `test` picks with a busy 503 until it returns false; every
  // other request goes on to the service. `seen` counts the requests each path received.
  let busy = () => false
  const seen = new Map()
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*/atlas/*' }, { urlPattern: '*/layers/*' }] })
  browser.listeners.push(m => {
    if (m.method !== 'Fetch.requestPaused') return
    const { requestId, request } = m.params, url = new URL(request.url)
    seen.set(url.pathname, (seen.get(url.pathname) ?? 0) + 1)
    if (busy(request, url)) browser.send('Fetch.fulfillRequest', { requestId, responseCode: 503, responseHeaders: [
      { name: 'Content-Type', value: 'application/json' }, { name: 'Retry-After', value: '1' }],
      body: Buffer.from(JSON.stringify({ detail: 'The database is updating. Try again in a moment.', code: 'busy' })).toString('base64') })
    else browser.send('Fetch.continueRequest', { requestId })
  })
  const shown = 'document.querySelector(".database-status")?.matches(":popover-open") === true'
  async function click(selector) {
    await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`)
    const p = await browser.centre(selector); await browser.click(p.x, p.y)
  }

  await browser.goto(service.base + '/en', { waitFor: 'document.querySelectorAll(".glyph-tile").length > 0' })
  const order = 'Array.from(document.querySelectorAll(".glyph-grid [data-unit]")).slice(0, 12).map(i => i.dataset.unit).join()'
  const before = await browser.evaluate(order)

  // A crop read while the database is busy: the inspector shows the list's row, the note says the
  // read is being tried again, and the crop arrives once the database answers.
  const id = await browser.evaluate('document.querySelectorAll(".glyph-grid [data-unit]")[4].dataset.unit')
  const record = '/atlas/characters/' + encodeURIComponent(id)
  let refused = 0
  busy = (request, url) => request.method === 'GET' && url.pathname === record && refused++ < 3
  await click(`.glyph-grid [data-unit="${id}"]`)
  await browser.waitFor(shown, 10000)
  assert((await browser.evaluate('document.querySelector(".database-status").innerText')).includes('The database is updating'), 'the busy note has its words')
  await browser.screenshot(join(screenshots, 'read-busy-light.png'))
  await browser.setColorScheme('dark')
  await browser.screenshot(join(screenshots, 'read-busy-dark.png'))
  await browser.setColorScheme('light')
  await browser.waitFor('document.querySelector("dialog[open] .crop-viewport")?.dataset.ready === "true"', 30000)
  await browser.waitFor(`!(${shown})`, 5000)
  assert(refused >= 3, `the read was refused ${refused} times`)
  assert(!await browser.evaluate('!!document.querySelector("dialog[open] .error-message")'), 'the inspector shows no error')
  assert(await browser.evaluate('document.querySelector("dialog[open] .record-id code")?.textContent') === id, 'the inspector shows the crop')
  assert(await browser.evaluate(order) === before, 'the collection behind it is as it was')
  console.log(`PASS a busy read is tried again (${refused} busy answers), and the crop arrives with no error`)
  await browser.key('Escape')
  busy = () => false
  console.log(`screenshots: ${screenshots}`)
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
