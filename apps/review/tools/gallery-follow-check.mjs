#!/usr/bin/env bun
/**
 * A link to the address a page was first loaded at shows that page again, after the view has moved on in
 * place and rewritten the address.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/gallery-follow-check.mjs
 */
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'

const config = options()
const service = await boot(config)
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
// The local review service serves no suggestions; the row it would answer with comes from here.
const suggestions = { items: [{ char: '龘', code_point: 'U+9F98', script: 'han', name: '龘', candidates: { imported: 0 } }] }

try {
  browser = await Browser.launch({ width: 1280, height: 720 })
  await browser.writeAs('gallery-follow-check', { anonymous: false })
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*\/layers\/suggest*' }] })
  browser.listeners.push(m => { if (m.method === 'Fetch.requestPaused') browser.send('Fetch.fulfillRequest', {
    requestId: m.params.requestId, responseCode: 200, responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
    body: Buffer.from(JSON.stringify(suggestions)).toString('base64'),
  }) })
  await browser.goto(`${service.base}/en`, { waitFor: `document.querySelectorAll('.glyph-tile').length > 0` })
  await browser.evaluate(`(() => {
    const field = document.querySelector('.collection-toolbar .character-search input')
    field.focus()
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(field, '龘')
    field.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: '龘' }))
  })()`)
  await browser.waitFor(`document.querySelectorAll('.collection-toolbar .candidate').length === 1`)
  await browser.evaluate(`document.querySelector('.collection-toolbar .candidate').click()`)
  await browser.waitFor(`location.pathname === '/en/character/U+9F98'`)
  await browser.evaluate(`document.querySelector('header a[href="/en"]').click()`)
  await browser.waitFor(`location.pathname === '/en'`)
  const shown = await browser.waitFor(`document.querySelector('.collection-toolbar .character-search input').value === '' && document.querySelectorAll('.glyph-tile').length > 0`, 8000).then(() => true, () => false)
  assert(shown, 'the collection’s address still shows the character')
  console.log('gallery-follow-check passed')
} finally {
  if (browser) await browser.close()
  await service.stop()
}
