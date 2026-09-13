#!/usr/bin/env bun
// Run through devrun so the service and browser share one bounded lifetime.
import { join } from 'node:path'
import { mkdirSync } from 'node:fs'
import Browser from './browser.mjs'
import { boot, options, events } from './harness.mjs'

const config = options()
const service = await boot(config)
let browser
const assert = (value, message) => { if (!value) throw new Error(message) }
const screenshots = '/tmp/atlas-workspace-shots'
mkdirSync(screenshots, { recursive: true })
try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  const errors = []
  browser.listeners.push(message => {
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails?.text)
  })
  const route = async (hash, ready) => {
    await browser.evaluate(`location.hash = ${JSON.stringify(hash)}`)
    await browser.waitFor(ready)
  }
  const fill = async (selector, value) => browser.evaluate(`(() => {
    const input = document.querySelector(${JSON.stringify(selector)});
    input.value = ${JSON.stringify(value)}; input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`)
  const click = async (selector) => {
    await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block: 'center'})`)
    const at = await browser.centre(selector); await browser.click(at.x, at.y)
  }
  await browser.goto(service.base + '/#/project', { waitFor: 'document.querySelector(".metrics strong") !== null' })
  assert(await browser.evaluate('document.querySelectorAll(".source-table tbody tr").length === 2'), 'overview has both volumes')
  await browser.screenshot(join(screenshots, 'overview-light.png'))
  console.log('PASS overview loads real project counts')

  await route('#/pages', 'document.querySelectorAll(".page-card").length === 6')
  assert(await browser.evaluate('document.body.innerText.includes("Needs character boxes")'), 'text-only pages appear in catalogue')
  await browser.screenshot(join(screenshots, 'catalogue-light.png'))
  await route('#/page/doc-2%3Ap2', 'document.querySelectorAll(".transcription-lines button").length === 2')
  assert(await browser.evaluate('document.querySelectorAll(".scan-scroll .box").length === 0'), 'reader opens page with no boxes')
  await browser.screenshot(join(screenshots, 'reader-light.png'))
  await fill('input[aria-label="As transcribed"]', 'アイヌ')
  await fill('input[aria-label="Proposed reading"]', 'アイノ')
  await fill('.correction-form-section textarea', 'Synthetic browser test')
  await click('.correction-form-section button.primary')
  await browser.waitFor('document.querySelectorAll(".correction-record").length === 1')
  assert(events(config.directory).some(e => e.field === 'correction'), 'correction persisted in the journal')
  await browser.send('Page.reload')
  await browser.waitFor('document.querySelectorAll(".correction-record").length === 1')
  console.log('PASS correction saves and survives browser reload')
  await click('.correction-record .revise')
  await fill('.correction-form-section textarea', 'Reviewed again in the browser fixture')
  await click('.correction-form-section button.primary')
  await browser.waitFor('document.querySelector(".correction-record")?.innerText.includes("Reviewed again")')
  const edits = events(config.directory).filter(e => e.field === 'correction')
  assert(edits.length === 2 && edits[0].new.id === edits[1].new.id, 're-review retains correction identity')
  console.log('PASS re-review updates the saved correction without duplicating it')

  await fill('.page-notes textarea', 'Review the missing character boxes.')
  await click('.page-notes form button')
  await browser.waitFor('document.querySelectorAll(".page-note").length === 1')
  await browser.send('Page.reload')
  await browser.waitFor('document.querySelector(".page-note")?.innerText.includes("missing character boxes")')
  console.log('PASS page notes survive reload')

  await click('.source-update > .section-heading button')
  await browser.waitFor('document.querySelector(".source-update .notice") !== null')
  assert(await browser.evaluate('document.querySelector(".source-update").innerText.includes("no source checkout")'), 'unconfigured source is reported')
  await click('.correction-record .withdraw')
  await browser.waitFor('document.querySelectorAll(".correction-record").length === 0')
  await browser.send('Page.reload')
  await browser.waitFor('document.querySelector(".saved-corrections") && document.querySelectorAll(".correction-record").length === 0')
  console.log('PASS source availability and durable correction undo')

  await fill('input[aria-label="As transcribed"]', 'アイヌ')
  await fill('input[aria-label="Proposed reading"]', 'アイノ')
  await fill('.correction-form-section textarea', 'Preserve this draft through a conflict')
  await fetch(service.base + '/reviews', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ target_type: 'page', target_id: 'doc-2:p2', field: 'note', new: 'Another reviewer was here', client_id: 'second-reviewer' }) })
  await click('.correction-form-section button.primary')
  await browser.waitFor('document.querySelector(".feedback-pane > .notice.error")?.innerText.includes("draft is preserved")')
  assert(await browser.evaluate(`document.querySelector('input[aria-label="Proposed reading"]').value === "アイノ"`), 'conflict preserves draft')
  console.log('PASS concurrent edit preserves typed feedback')

  for (const theme of ['light', 'dark']) {
    await browser.setViewport(390, 844)
    await browser.evaluate(`document.documentElement.dataset.theme = '${theme}'`)
    await browser.evaluate('document.querySelector(".pane").scrollTop = 0')
    await browser.screenshot(join(screenshots, `reader-mobile-${theme}.png`))
    assert(await browser.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), `${theme} mobile has no page overflow`)
    const width = await browser.evaluate('document.querySelector(".feedback-pane").getBoundingClientRect().width')
    assert(width <= 390, 'feedback fits narrow screen')
  }
  assert(errors.length === 0, `browser errors: ${errors.join(', ')}`)
  console.log('PASS light/dark mobile layouts, no browser exceptions')
} finally {
  await browser?.close()
  service.stop()
}
