#!/usr/bin/env bun
/**
 * The crop inspector's copy buttons, in a real browser, against a disposable dataset.
 *
 * The title's character and the crop's form each have a button that shows while the pointer is over
 * them and copies the value shown; stepping to the next crop clears the copied mark.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/copy-value-check.mjs
 */
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'

const config = options()
const service = await boot(config)
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const opacity = selector => browser.evaluate(`getComputedStyle(document.querySelector(${JSON.stringify(selector)})).opacity`)
const clipboard = () => browser.evaluate('navigator.clipboard.readText()')

try {
  browser = await Browser.launch({ width: 1280, height: 900 })
  await browser.send('Browser.grantPermissions', { permissions: ['clipboardReadWrite', 'clipboardSanitizedWrite'], origin: service.base })
  await browser.goto(`${service.base}/en`, { waitFor: 'document.querySelectorAll(".glyph-grid [data-unit]").length > 1', timeout: 90000 })
  const id = await browser.evaluate('document.querySelector(".glyph-grid [data-unit]").dataset.unit')
  // Opened from the collection, so the inspector has a next crop to step to.
  await browser.waitFor('document.documentElement.dataset.hydrated !== undefined', 60000)
  await browser.evaluate(`document.querySelector('.glyph-grid [data-unit="${id}"]').click()`)
  await browser.waitFor('document.querySelector("dialog[open] .crop-form .form-chip") !== null', 60000)

  const title = 'dialog[open] .crop-title .copy-value'
  let at = await browser.centre('dialog[open] .crop-title .reference-glyph')
  await browser.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: at.x, y: at.y })
  await browser.waitFor(`getComputedStyle(document.querySelector(${JSON.stringify(title)})).opacity === "1"`)
  at = await browser.centre(title)
  await browser.click(at.x, at.y)
  const char = await browser.evaluate('document.querySelector("dialog[open] .crop-title .reference-glyph").textContent.trim()')
  await browser.waitFor(`navigator.clipboard.readText().then(text => text === ${JSON.stringify(char)})`)
  assert(await opacity(title) === '1', 'the title button stays shown while it says it copied')
  assert(await browser.evaluate('location.pathname').then(path => path.startsWith('/en/crop/')), 'copying must not follow the character link')
  console.log('PASS the title copies its character')

  // The next crop's button has copied nothing yet.
  await browser.evaluate('document.querySelector("dialog[open] .next-character").click()')
  await browser.waitFor(`document.querySelector("dialog[open] .record-id code")?.textContent !== ${JSON.stringify(id)}`)
  await browser.waitFor('document.querySelector("dialog[open] .crop-form .form-chip") !== null')
  assert(await browser.evaluate('document.querySelector("dialog[open] .copy-value.copied") === null'), 'the next crop must not show a copy it did not make')
  console.log('PASS stepping clears the copied mark')

  const form = 'dialog[open] .crop-form .copy-value'
  at = await browser.centre('dialog[open] .crop-form .form-chip')
  await browser.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: at.x, y: at.y })
  await browser.waitFor(`getComputedStyle(document.querySelector(${JSON.stringify(form)})).opacity === "1"`)
  at = await browser.centre(form)
  await browser.click(at.x, at.y)
  // The chip's text without its number key.
  const pressed = await browser.evaluate('(() => { const chip = document.querySelector("dialog[open] .crop-form .form-chip[aria-pressed=true]").cloneNode(true); chip.querySelector("kbd")?.remove(); return chip.textContent.trim() })()')
  await browser.waitFor(`navigator.clipboard.readText().then(text => text === ${JSON.stringify(pressed)})`)
  assert(await browser.evaluate('document.querySelector("dialog[open] .crop-form .form-chip[aria-pressed=true]") !== null'), 'copying the form must not change it')
  console.log(`PASS the form copies ${await clipboard()}`)
  console.log('PASS copy values')
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
