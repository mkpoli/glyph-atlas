#!/usr/bin/env bun
/**
 * The character menu near the bottom of the window. The box under a selection sits at the bottom edge
 * of the window, so a list opened below it runs out of it; the list takes the side that has room for
 * it. The box in the toolbar keeps its list below, where there is room.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/menu-flip-check.mjs
 */
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'

const config = options()
const service = await boot(config)
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }

// Ten rows, taller than the space under the bulk bar, so where the list opens is not a matter of luck.
const suggestions = { items: Array.from({ length: 10 }, (_, i) => {
  const char = String.fromCodePoint(0x4e00 + i)
  return {
    char,
    code_point: 'U+' + (0x4e00 + i).toString(16).toUpperCase(),
    script: 'Han',
    reading: 'かん',
    name: `fixture ${i}`,
    candidates: { imported: 12 - i },
  }
}) }

/** Put text into an input the way a paste does: the native setter, then a real input event. */
const typeInto = (selector, text) => `(() => {
  const field = document.querySelector(${JSON.stringify(selector)})
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
  setter.call(field, ${JSON.stringify(text)})
  field.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: ${JSON.stringify(text)} }))
  return field.value
})()`

/** The box, its form and its list, in window coordinates. */
const geometryOf = selector => `(() => {
  const root = document.querySelector(${JSON.stringify(selector)})
  const form = root.querySelector('.find')
  const list = root.querySelector('.candidate-list')
  const f = form.getBoundingClientRect(), l = list.getBoundingClientRect()
  return { form: { top: f.top, bottom: f.bottom }, list: { top: l.top, bottom: l.bottom, height: l.height }, viewport: innerHeight }
})()`

try {
  browser = await Browser.launch({ width: 1280, height: 720 })
  // A signed-in reviewer, so the collection offers selection and the bar it puts at the bottom.
  await browser.writeAs('menu-check', { anonymous: false })
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })
  // The local review service serves no suggestions; the rows it would answer with come from here.
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*\/layers\/suggest*' }] })
  browser.listeners.push(m => { if (m.method === 'Fetch.requestPaused') browser.send('Fetch.fulfillRequest', {
    requestId: m.params.requestId, responseCode: 200,
    responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
    body: Buffer.from(JSON.stringify(suggestions)).toString('base64'),
  }) })

  await browser.goto(`${service.base}/en`, { waitFor: `document.querySelectorAll('.glyph-tile').length > 0` })
  await browser.waitFor(`document.querySelector('.select-toggle') !== null`)
  await browser.evaluate(`document.querySelector('.select-toggle').click()`)
  await browser.waitFor(`document.querySelector('.bulk-bar .character-search input') !== null`)

  // The bar under a selection stays at the bottom of the window: its list has to open above the box.
  await browser.evaluate(typeInto('.bulk-bar .character-search input', 'い'))
  await browser.waitFor(`document.querySelectorAll('.bulk-bar .candidate-list .candidate').length === 10`)
  await browser.screenshot('/tmp/atlas-menu-flip-bulk.png')
  const bulk = await browser.evaluate(geometryOf('.bulk-bar .character-search'))
  assert(bulk.list.bottom <= bulk.form.top + 1, `the bulk bar's list opens below its box: ${JSON.stringify(bulk)}`)
  assert(bulk.list.top >= -1, `the bulk bar's list starts above the window: ${JSON.stringify(bulk)}`)
  assert(bulk.list.height > 100, `the bulk bar's list shows ${bulk.list.height} px of rows`)

  // A phone wraps the bar onto another row, and a short window leaves little room above either: the
  // list still opens above the box and is cut to what the window has.
  await browser.setViewport(390, 844)
  await Bun.sleep(250)
  await browser.screenshot('/tmp/atlas-menu-flip-phone.png')
  const phone = await browser.evaluate(geometryOf('.bulk-bar .character-search'))
  assert(phone.list.bottom <= phone.form.top + 1, `the phone's list opens below its box: ${JSON.stringify(phone)}`)
  assert(phone.list.top >= -1, `the phone's list starts above the window: ${JSON.stringify(phone)}`)

  await browser.setViewport(1280, 460)
  await Bun.sleep(250)
  await browser.screenshot('/tmp/atlas-menu-flip-short.png')
  const short = await browser.evaluate(geometryOf('.bulk-bar .character-search'))
  assert(short.list.bottom <= short.form.top + 1, `the short window's list opens below its box: ${JSON.stringify(short)}`)
  assert(short.list.top >= -1, `the short window's list is cut at its top: ${JSON.stringify(short)}`)
  await browser.setViewport(1280, 720)
  await Bun.sleep(250)

  // The box in the toolbar has the page below it, so its list keeps opening downward.
  await browser.evaluate(typeInto('.collection-toolbar .character-search input', 'い'))
  await browser.waitFor(`document.querySelectorAll('.collection-toolbar .candidate-list .candidate').length === 10`)
  await browser.screenshot('/tmp/atlas-menu-flip-toolbar.png')
  const top = await browser.evaluate(geometryOf('.collection-toolbar .character-search'))
  assert(top.list.top >= top.form.bottom - 1, `the toolbar's list opens above its box: ${JSON.stringify(top)}`)
  assert(top.list.bottom <= top.viewport + 1, `the toolbar's list runs past the window: ${JSON.stringify(top)}`)

  // Scrolling moves the box under an open list: the side is chosen again with the room it now has.
  await browser.setViewport(1280, 360)
  await Bun.sleep(250)
  const tight = await browser.evaluate(geometryOf('.collection-toolbar .character-search'))
  assert(tight.list.bottom <= tight.form.top + 1, `the short window's toolbar list opens below its box: ${JSON.stringify(tight)}`)
  await browser.evaluate('window.scrollBy(0, 120)')
  await Bun.sleep(300)
  const scrolled = await browser.evaluate(geometryOf('.collection-toolbar .character-search'))
  assert(scrolled.list.top >= scrolled.form.bottom - 1, `the list did not follow the box after scrolling: ${JSON.stringify(scrolled)}`)
  assert(scrolled.list.bottom <= scrolled.viewport + 1, `the scrolled list runs past the window: ${JSON.stringify(scrolled)}`)

  assert(errors.length === 0, `page errors: ${errors.join('; ')}`)
  console.log('menu flip: the list under a selection opens above its box and stays in the window')
} finally {
  if (browser) await browser.close()
  await service.stop()
}
