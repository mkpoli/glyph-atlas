#!/usr/bin/env bun
/**
 * A search that opens pages shows its candidates as grapheme cards wherever the box is, each heading a link
 * to its grapheme's page by the pointer and the keyboard; a box that picks a character lists rows.
 *
 * Run through devrun, against the last build:
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
const BOX = '.collection-toolbar .character-search input', HEAD = '.collection-toolbar .search-card a.card-head'
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
  for (const path of ['/en', '/en/flagged']) {
    await browser.goto(service.base + path, { waitFor: `document.querySelector(${JSON.stringify(BOX)}) !== null` })
    // The local review service has no grapheme pages, so the link followed is what is read.
    await browser.evaluate(`addEventListener('click', e => { const a = e.target.closest('a.card-head'); if (a) { e.preventDefault(); e.stopPropagation(); window.followed = a.getAttribute('href') } }, true)`)
    const opened = async () => {
      await browser.evaluate(`window.followed = null`)
      await browser.evaluate(typed(BOX))
      await browser.waitFor(`document.querySelectorAll(${JSON.stringify(HEAD)}).length === 2`)
    }
    const follows = how => browser.waitFor(`window.followed === '/en/grapheme/U+9F95'`, 4000).catch(() => { throw new Error(`${how} on ${path} does not follow the link to the grapheme's page`) })

    await opened()
    const list = await browser.evaluate(`({ heads: [...document.querySelectorAll(${JSON.stringify(HEAD)})].map(h => h.getAttribute('href')), forms: [...document.querySelectorAll('.collection-toolbar .search-card li')].length,
      rows: document.querySelectorAll('.collection-toolbar .candidate').length,
      wide: [...document.querySelectorAll('.collection-toolbar .candidate-list *')].some(e => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== 'visible') })`)
    assert(JSON.stringify(list.heads) === '["/en/grapheme/U+9F95","/en/grapheme/U+9F98"]', `${path}: the headings lead to ${JSON.stringify(list.heads)}`)
    assert(list.forms === 3 && list.rows === 0, `${path}: ${list.forms} forms in cards and ${list.rows} rows`)
    assert(!list.wide, `${path}: the list overflows`)
    const point = await browser.centre(HEAD)
    await browser.send('Input.dispatchMouseEvent', { type: 'mousePressed', ...point, button: 'left', clickCount: 1 })
    // A slow press: the list stays while the button is down.
    await Bun.sleep(400)
    assert(await browser.evaluate(`document.activeElement === document.querySelector(${JSON.stringify(BOX)})`), 'pressing the heading takes focus from the box')
    await browser.send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...point, button: 'left', clickCount: 1 })
    await follows('a click on the heading')

    await opened()
    await key('ArrowDown', 40); await key('Enter', 13)
    await follows('the arrow and Enter')
  }

  await browser.goto(`${service.base}/en`, { waitFor: `document.querySelector('.select-toggle') !== null` })
  await browser.evaluate(`document.querySelector('.select-toggle').click()`)
  await browser.waitFor(`document.querySelector('.bulk-bar .character-search input') !== null`)
  await browser.evaluate(typed('.bulk-bar .character-search input'))
  await browser.waitFor(`document.querySelectorAll('.bulk-bar .candidate').length === 3`)
  assert(await browser.evaluate(`document.querySelectorAll('.bulk-bar .candidate-list a').length === 0`), 'the box that picks a character links to a page')
  console.log('search-grapheme-check passed')
} finally {
  await browser?.close()
  await service.stop()
}
