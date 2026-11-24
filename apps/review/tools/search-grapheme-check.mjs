#!/usr/bin/env bun
/**
 * The heading over a grapheme's rows in the search list leads to the grapheme's page, by the pointer and
 * by the arrow keys, where rows open pages; a box that picks a character has no such link.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/search-grapheme-check.mjs
 */
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'

const config = options()
const service = await boot(config)
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }

// Two forms of one grapheme the collection holds none of, and a character that is its own grapheme.
const grapheme = { code_point: 'U+9F95', char: '龕', character_count: 2 }
const row = (code_point, char, more = {}) => ({ char, code_point, script: 'han', name: char, candidates: { imported: 0 }, ...more })
const suggestions = { items: [row('U+9F95', '龕', { grapheme }), row('U+9FA4', '龤', { grapheme }), row('U+9F98', '龘', { grapheme: { code_point: 'U+9F98', char: '龘', character_count: 1 } })] }
const BOX = '.collection-toolbar .character-search input', HEAD = '.collection-toolbar .candidate-grapheme-head'
const typed = selector => `(() => {
  const field = document.querySelector(${JSON.stringify(selector)})
  field.focus()
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(field, '龕')
  field.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: '龕' }))
})()`
const key = async (name, code) => { for (const type of ['keyDown', 'keyUp']) await browser.send('Input.dispatchKeyEvent', { type, key: name, code: name, windowsVirtualKeyCode: code, text: name === 'Enter' && type === 'keyDown' ? '\r' : undefined }) }

try {
  browser = await Browser.launch({ width: 1280, height: 800 })
  await browser.writeAs('search-grapheme-check', { anonymous: false })
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*\/layers\/suggest*' }] })
  browser.listeners.push(m => { if (m.method === 'Fetch.requestPaused') browser.send('Fetch.fulfillRequest', {
    requestId: m.params.requestId, responseCode: 200, responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
    body: Buffer.from(JSON.stringify(suggestions)).toString('base64'),
  }) })
  await browser.goto(`${service.base}/en`, { waitFor: `document.querySelector('.select-toggle') !== null` })
  // The local review service has no grapheme pages, so the link followed is what is read.
  await browser.evaluate(`addEventListener('click', e => { const a = e.target.closest('a.candidate-grapheme-head'); if (a) { e.preventDefault(); e.stopPropagation(); window.followed = a.getAttribute('href') } }, true)`)
  const opened = async () => {
    await browser.evaluate(`window.followed = null`)
    await browser.evaluate(typed(BOX))
    await browser.waitFor(`document.querySelectorAll('.collection-toolbar .candidate').length === 3 && document.querySelector(${JSON.stringify(BOX)}).getAttribute('aria-busy') === 'false'`)
  }
  const active = () => browser.evaluate(`document.querySelector(${JSON.stringify(BOX)}).getAttribute('aria-activedescendant')`)
  const follows = how => browser.waitFor(`window.followed === '/en/grapheme/U+9F95'`, 4000).catch(() => { throw new Error(`${how} does not follow the link to the grapheme's page`) })

  await opened()
  const heads = await browser.evaluate(`[...document.querySelectorAll(${JSON.stringify(HEAD)})].map(h => h.getAttribute('href'))`)
  assert(JSON.stringify(heads) === '["/en/grapheme/U+9F95"]', `the headings lead to ${JSON.stringify(heads)}`)
  assert((await active()).endsWith('-0'), 'the first candidate is not the one Enter opens')
  const point = await browser.centre(HEAD)
  await browser.send('Input.dispatchMouseEvent', { type: 'mousePressed', ...point, button: 'left', clickCount: 1 })
  // A slow press: the list stays while the button is down.
  await Bun.sleep(400)
  assert(await browser.evaluate(`document.activeElement === document.querySelector(${JSON.stringify(BOX)})`), 'pressing the heading takes focus from the box')
  await browser.send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...point, button: 'left', clickCount: 1 })
  await follows('a click on the heading')

  await opened()
  await key('ArrowUp', 38)
  assert((await active()).endsWith('-g0'), 'the arrows pass over the heading')
  await key('ArrowDown', 40)
  assert((await active()).endsWith('-0'), 'the arrows do not return to the first row')
  await key('ArrowUp', 38); await key('ArrowUp', 38)
  assert((await active()).endsWith('-2'), 'the arrows do not wrap to the last row')
  await key('ArrowDown', 40); await key('Enter', 13)
  await follows('Enter on the heading')

  await browser.evaluate(`document.querySelector('.select-toggle').click()`)
  await browser.waitFor(`document.querySelector('.bulk-bar .character-search input') !== null`)
  await browser.evaluate(typed('.bulk-bar .character-search input'))
  await browser.waitFor(`document.querySelectorAll('.bulk-bar .candidate').length === 3`)
  assert(await browser.evaluate(`document.querySelectorAll('.bulk-bar a.candidate-grapheme-head').length === 0`), 'the box that picks a character links to a grapheme')
  console.log('search-grapheme-check passed')
} finally {
  await browser?.close()
  await service.stop()
}
