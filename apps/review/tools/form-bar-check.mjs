#!/usr/bin/env bun
/**
 * The form bar, in a real browser, against a disposable dataset.
 *
 * Selecting crops in a round opens a bar with the forms of the round's grapheme. A form key or a chip
 * marks every selected crop as that form at once; undo takes the marking back. A crop marked this way
 * is decided: moving on does not record it as seen.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/form-bar-check.mjs
 */
import Browser from './browser.mjs'
import { boot, events, options } from './harness.mjs'

const config = options()
const service = await boot(config)
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const loadedTiles = `[...document.querySelectorAll('.quiz-tile img')].length > 2 && [...document.querySelectorAll('.quiz-tile img')].every(i => i.complete && i.naturalWidth > 0)`
const tileIds = `[...document.querySelectorAll('.quiz-tile')].map(tile => tile.dataset.unit)`
const clickTile = (index, shift = false) => browser.evaluate(`document.querySelectorAll('.quiz-choice')[${index}]
  .dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: ${shift} }))`)
const written = async id => { const d = await (await fetch(`${service.base}/atlas/characters/${encodeURIComponent(id)}`)).json(); return d.written_character || d.label }
const seenIds = () => events(config.directory).filter(e => e.field === 'seen' && e.new).map(e => e.target_id)

try {
  browser = await Browser.launch({ width: 1280, height: 1000 })
  await browser.goto(`${service.base}/en/review`)
  await browser.waitFor(loadedTiles, 60000)
  const ids = await browser.evaluate(tileIds)
  const before = await Promise.all(ids.slice(0, 3).map(written))

  // A real click, which leaves the crop's button focused: the form keys still work from there.
  const { x, y } = await browser.centre('.quiz-choice')
  await browser.click(x, y)
  assert(await browser.evaluate(`document.activeElement?.classList.contains('quiz-choice')`), 'the clicked crop has focus')
  await clickTile(2, true)
  assert(await browser.evaluate(`document.querySelector('.form-bar .selection-count')?.innerText`) === '3 selected', 'Shift-click selects the range')
  const forms = await browser.evaluate(`[...document.querySelectorAll('.form-chip > .script-text')].map(s => s.innerText)`)
  const members = await browser.evaluate(`document.querySelector('.round-members')?.innerText.split(' ')`)
  assert(forms.length > 1 && forms.join() === members.join(), `the bar offers the grapheme's forms (${forms.join(' ')})`)
  console.log(`ok   selection opens the bar with ${forms.length} forms`)

  // A form key marks the whole selection, which then shows its form and leaves the selection.
  await browser.key('2')
  await browser.waitFor(`document.querySelector('.form-done')`, 20000)
  for (const id of ids.slice(0, 3)) assert(await written(id) === forms[1], `${id} is written as ${forms[1]}`)
  const shown = await browser.evaluate(`[...document.querySelectorAll('.quiz-tile')].slice(0, 3).map(t => [t.classList.contains('assigned'), t.querySelector('.quiz-written')?.innerText])`)
  assert(shown.every(([assigned, label]) => assigned && label === forms[1]), `the tiles show the form (${JSON.stringify(shown)})`)
  assert(!await browser.evaluate(`document.querySelector('.selection-count')`), 'the marked crops leave the selection')
  console.log(`ok   2 marks the three crops as ${forms[1]}`)

  // Undo restores them, on the service and on screen.
  await browser.evaluate(`[...document.querySelectorAll('.form-bar button')].find(b => b.innerText === 'Undo').click()`)
  await browser.waitFor(`!document.querySelector('.form-done')`, 20000)
  const after = await Promise.all(ids.slice(0, 3).map(written))
  assert(after.join() === before.join(), `undo restores the characters (${after.join()} vs ${before.join()})`)
  assert(!await browser.evaluate(`document.querySelector('.quiz-tile.assigned')`), 'undo restores the tiles')
  console.log('ok   undo takes the marking back')

  // A chip marks too; moving on records the other crops as seen and the marked one as nothing more.
  await clickTile(1)
  await browser.evaluate(`document.querySelectorAll('.form-chip')[0].click()`)
  await browser.waitFor(`document.querySelector('.form-done')`, 20000)
  const seenBefore = seenIds().length
  await browser.waitFor(`document.querySelector('.next-round') && !document.querySelector('.next-round').disabled`, 20000)
  await browser.evaluate(`document.querySelector('.next-round').click()`)
  const deadline = Date.now() + 20000
  while (seenIds().length === seenBefore && Date.now() < deadline) await Bun.sleep(100)
  const seen = seenIds().slice(seenBefore)
  assert(seen.length > 0 && !seen.includes(ids[1]), `the marked crop is not recorded as seen (${seen.length} seen)`)
  console.log(`ok   moving on records ${seen.length} crops as seen, not the marked one`)
  console.log('\nform-bar-check passed')
} finally {
  await browser?.close()
  await service.stop?.()
}
