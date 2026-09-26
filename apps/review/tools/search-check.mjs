#!/usr/bin/env bun
/**
 * The collection's find box, in a real browser, against a disposable fixture.
 *
 * What this checks that the API tests cannot: that a reviewer can put a character into the visible
 * box and see the crops for it. The character used is 𪜈 (U+2A708), which is in the supplementary
 * plane — Python indexes it as one character and JavaScript as two UTF-16 units — and which no
 * imported corpus records, so the fixture is the only place a positive result exists. The fixture
 * also writes ゐ read as い, which is what tells a search on the written character from a search on
 * the reading.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/search-check.mjs
 */
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'

const config = options()
const service = await boot(config)
const CHARACTER = '\u{2A708}'
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }

/** Put text into an input the way a paste does: the native setter, then a real input event. */
const typeInto = (selector, text) => `(() => {
  const field = document.querySelector(${JSON.stringify(selector)})
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
  setter.call(field, ${JSON.stringify(text)})
  field.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: ${JSON.stringify(text)} }))
  return field.value
})()`
const rows = `[...document.querySelectorAll('.glyph-tile')].map(tile => ({
  id: tile.dataset.unit,
  label: tile.querySelector('.tile-reading').textContent,
  image: tile.querySelector('img')?.getAttribute('src') ?? null,
  loaded: (tile.querySelector('img')?.naturalWidth ?? 0) > 0,
}))`
const countText = `document.querySelector('.find-count')?.textContent ?? null`

try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })

  await browser.goto(`${service.base}/`, { waitFor: `document.querySelectorAll('.glyph-tile').length > 0` })

  // The work menu offers the works that have crops (the fixture's second work has none), and a chosen
  // work shows in the toggle until "All works" clears it.
  const units = `[...document.querySelectorAll('.glyph-tile[data-unit]')].map(tile => tile.dataset.unit)`
  await browser.evaluate(`document.querySelector('.work-toggle').click()`)
  await browser.waitFor(`document.querySelector('.work-menu input') !== null`)
  const offered = await browser.evaluate(`[...document.querySelectorAll('.work-menu li button span')].map(s => s.textContent)`)
  assert(JSON.stringify(offered) === '["Calibration book A"]', `the work menu offers ${JSON.stringify(offered)}`)
  await browser.evaluate(typeInto('.work-menu input', 'no such work'))
  await browser.waitFor(`document.querySelector('.work-none') !== null`)
  await browser.evaluate(typeInto('.work-menu input', 'book a'))
  await browser.waitFor(`document.querySelectorAll('.work-menu li button').length === 2`)
  await browser.evaluate(`document.querySelectorAll('.work-menu li button')[1].click()`)
  await browser.waitFor(`document.querySelector('.work-name').textContent === 'Calibration book A'`)
  await browser.waitFor(`${units}.length > 0 && ${units}.every(id => id.startsWith('doc-1:'))`, 15000)
  await browser.evaluate(`document.querySelector('.work-toggle').click()`)
  await browser.waitFor(`document.querySelector('.work-menu li button') !== null`)
  await browser.evaluate(`document.querySelector('.work-menu li button').click()`)
  await browser.waitFor(`document.querySelector('.work-name').textContent === 'All works'`)

  // A reading filter is on, as a reviewer would have left it. A direct character search has to
  // answer its own question rather than intersect with it.
  // The empty box, focused, lists the readings; choosing one puts it in the box as a token.
  await browser.evaluate(`document.querySelector('.find input').focus()`)
  await browser.waitFor(`document.querySelector('.browse-panel .category-options button') !== null`)
  await browser.evaluate(`[...document.querySelectorAll('.browse-panel .category-options button')]
    .find(b => b.querySelector('span').textContent === 'あ').click()`)
  await browser.waitFor(`document.querySelector('.find-token')?.textContent.includes('あ')`)
  const before = await browser.evaluate(`document.querySelectorAll('.glyph-tile').length`)
  assert(before > 0, 'the reading filter shows some rows to start from')

  // Now search the character itself.
  const typed = await browser.evaluate(typeInto('.find input', CHARACTER))
  assert(typed === CHARACTER, `the box holds ${JSON.stringify(typed)}, not the character`)
  assert(typed.length === 2, 'JavaScript sees it as a surrogate pair, which is the point')
  assert(typed.codePointAt(0) === 0x2A708, 'the code point the box holds is U+2A708')

  await browser.waitFor(`${countText} !== null`, 15000)
  const count = await browser.evaluate(countText)
  assert(/^1 glyph · 1 here/.test(count.trim()), `the count reads ${JSON.stringify(count)}`)
  assert(!/No occurrence/.test(count), 'the character has a recorded occurrence in the fixture')

  // The reading filter is cleared by the search, so the box no longer narrows the answer.
  const token = await browser.evaluate(`document.querySelector('.find-token')?.textContent ?? null`)
  assert(token === null, `the reading filter still says ${JSON.stringify(token)}`)
  const group = await browser.evaluate(`document.querySelector('.filter-tabs button.active').textContent.trim()`)
  assert(group === 'All', `the type filter still says ${JSON.stringify(group)}`)

  const found = await browser.evaluate(rows)
  assert(found.length === 1, `${found.length} rows for one occurrence`)
  assert(found[0].id === 'doc-1:p1:l3:u0',
    `the matched unit is ${JSON.stringify(found[0].id)}, not the one written U+2A708`)
  await browser.waitFor(rows + '[0]?.loaded === true', 15000)
  assert(/\/image\?/.test(found[0].image ?? ''), 'the row points at a crop')
  await browser.screenshot('/tmp/atlas-search-found.png')

  // Clearing puts the whole collection back.
  await browser.evaluate(`document.querySelector('.find-clear').click()`)
  await browser.waitFor(`document.querySelectorAll('.glyph-tile').length > 1`)
  assert(await browser.evaluate(countText) === null, 'the count goes with the query')
  assert(await browser.evaluate(`document.querySelector('.find input').value`) === '', 'the box is clear')

  // A character the fixture does not record: zero, said plainly, with a way back.
  await browser.evaluate(typeInto('.find input', '𰃂'))
  await browser.waitFor(`document.querySelector('.empty h2') !== null`, 15000)
  const empty = await browser.evaluate(`document.querySelector('.empty h2').textContent`)
  assert(/No occurrence of/.test(empty), `the empty state reads ${JSON.stringify(empty)}`)
  assert(await browser.evaluate(`document.querySelectorAll('.glyph-tile').length`) === 0, 'no rows')
  assert(/^0 glyphs/.test((await browser.evaluate(countText)).trim()), 'the count says zero')
  await browser.screenshot('/tmp/atlas-search-empty.png')
  await browser.evaluate(`document.querySelector('.empty button.primary').click()`)
  await browser.waitFor(`document.querySelectorAll('.glyph-tile').length > 1`)

  // ゐ is written on a record read as い: found by what was written, not by the reading. The label
  // is the reading, which for this record is い, so the assertion is on the matched unit.
  await browser.evaluate(typeInto('.find input', 'ゐ'))
  await browser.waitFor(`${countText} !== null`, 15000)
  assert(/^1 glyph(?!s)/.test((await browser.evaluate(countText)).trim()), 'ゐ is found by its character')
  const katakana = await browser.evaluate(rows)
  assert(katakana.length === 1 && katakana[0].id === 'doc-1:p1:l3:u1',
    `ゐ matched ${JSON.stringify(katakana.map(row => row.id))}`)

  // い is written on many records and on neither of the two written 𪜈/ゐ: the search is by
  // character, so the reading search returns the い records and not those two.
  await browser.evaluate(typeInto('.find input', 'い'))
  await browser.waitFor(`${countText} !== null`, 15000)
  const ne = Number((await browser.evaluate(countText)).match(/^([\d,]+)/)[1].replace(/,/g, ''))
  assert(ne > 0, `the fixture has ordinary い records; the count said ${ne}`)
  await browser.screenshot('/tmp/atlas-search-reading.png')

  assert(errors.length === 0, `page errors: ${errors.join('; ')}`)
  console.log('search: the box finds 𪜈 by character and by U+2A708, clears the reading filter, says zero plainly')
} finally {
  if (browser) await browser.close()
  await service.stop()
}
