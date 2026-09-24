#!/usr/bin/env bun
/**
 * Skip, in a real browser, against a disposable dataset.
 *
 * The behaviour under test is an absence: skipping must write nothing, count nothing, and still move
 * the reader on. So every assertion about a skip is paired with a count of the events the service
 * recorded and the number the interface says it has reviewed.
 *
 * "Can't tell" is the same action wearing an issue's name, and it is checked in both places it can
 * be chosen: on a crop in a round, and in the reviewer that opens from a crop.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/skip-check.mjs
 */
import Browser from './browser.mjs'
import { boot, events, options } from './harness.mjs'

const config = options()
const service = await boot(config)
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const sleep = ms => Bun.sleep(ms)

try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })
  const click = async selector => {
    await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`)
    const point = await browser.centre(selector)
    await browser.click(point.x, point.y)
  }
  const rows = `[...document.querySelectorAll('.quiz-tile')].map(tile => ({
    id: tile.dataset.unit ?? tile.getAttribute('data-unit'),
    skipped: tile.classList.contains('skipped'),
    selected: tile.classList.contains('selected'),
  }))`
  const reviewedText = `document.querySelector('.round-count')?.textContent ?? ''`

  await browser.goto(`${service.base}/#/review`, {
    waitFor: `document.querySelectorAll('.quiz-choice').length > 0`,
  })
  // Deliberately no wait for the round to settle: the round loads itself on mount and may still be
  // in flight here, and the reader is allowed to act while it is. A skip taken now must survive the
  // load that is already running — that race is the thing this check exists for.
  const startEvents = events(service.fixture.directory).length
  const startReviewed = await browser.evaluate(reviewedText)
  const before = await browser.evaluate(rows)
  assert(before.length > 1, `the round offers more than one crop: ${before.length}`)

  // 1. Skipping one crop: it leaves the round, nothing is written, nothing is counted.
  await click('.quiz-tile:nth-child(1) .skip-choice')
  await sleep(300)
  const afterOne = await browser.evaluate(rows)
  assert(afterOne[0]?.skipped, 'the crop is marked as skipped')
  assert(!afterOne[0]?.selected, 'a skipped crop is not also selected')
  assert(events(service.fixture.directory).length === startEvents,
    'a skip wrote to the journal')
  assert(await browser.evaluate(reviewedText) === startReviewed,
    'a skip changed the reviewed count')
  assert(/1 skipped/.test(await browser.evaluate(`document.querySelector('.round-selection')?.textContent ?? ''`)),
    'the bar does not report the skip as a skip')
  assert(await browser.evaluate(
    `document.querySelector('.quiz-tile.skipped .choice-label')?.textContent`) === 'Skipped',
    'the skipped crop does not say so')
  assert(await browser.evaluate(`!!document.querySelector('.quiz-tile.skipped .restore-choice')`),
    'there is no way to take a skipped crop back')

  // Select all is where a skip used to be erased: it takes every crop still open, and a skipped one
  // is not open.
  await click('.stage-toolbar .bulk-toggle')
  await sleep(200)
  assert(await browser.evaluate(`document.querySelectorAll('.quiz-tile.skipped.selected').length`) === 0,
    'Select all selected a skipped crop')
  assert(await browser.evaluate(`document.querySelectorAll('.quiz-tile.selected').length`)
    === before.length - 1, 'Select all did not take exactly the open crops')

  // 2. It is out of the round that gets saved: select the rest, save, and the answers are the rest.
  await click('.quiz-workspace .issue-card[data-issue="merged"]')
  await browser.waitFor(`document.querySelector('.quiz-submit .primary')?.disabled === false`)
  const skippedId = afterOne[0].id
  await click('.quiz-submit .primary')
  await browser.waitFor(`document.querySelector('.quiz-grid') !== null`)
  await sleep(600)
  const written = events(service.fixture.directory).slice(startEvents)
  const reviewEvents = written.filter(row => row.field === 'review')
  assert(reviewEvents.length > 0, 'the round saved nothing at all')
  assert(!reviewEvents.some(row => row.target_id === skippedId),
    'the skipped crop was saved as part of the round')
  assert(reviewEvents.length === before.length - 1,
    `the round saved ${reviewEvents.length} answers for ${before.length - 1} decided crops`)

  // 3. "Can't tell" in a round is the same neutral skip.
  const beforeSecond = events(service.fixture.directory).length
  const reviewedSecond = await browser.evaluate(reviewedText)
  await browser.waitFor(`document.querySelectorAll('.quiz-choice').length > 0`)
  await click('.quiz-tile:nth-child(1) .quiz-choice')
  await click('.quiz-workspace .issue-card[data-issue="unclear"]')
  await sleep(400)
  assert(events(service.fixture.directory).length === beforeSecond, '"Can\'t tell" wrote to the journal')
  assert(await browser.evaluate(reviewedText) === reviewedSecond, '"Can\'t tell" was counted as reviewed')
  assert(await browser.evaluate(`document.querySelectorAll('.quiz-tile.skipped').length`) >= 1,
    '"Can\'t tell" did not skip the crop')
  assert(await browser.evaluate(`document.querySelectorAll('.quiz-tile.wrong, .quiz-tile.unsure').length`) === 0,
    '"Can\'t tell" stored a verdict')

  // 4. "Can't tell" in the reviewer: the dialog closes and nothing is written.
  const beforeInspector = events(service.fixture.directory).length
  await click('.quiz-tile:nth-child(2) .inspect-choice')
  await browser.waitFor(`document.querySelector('dialog[open] .issue-card') !== null`)
  await click('dialog[open] .issue-card[data-issue="unclear"]')
  await browser.waitFor(`document.querySelector('dialog[open]') === null`, 10000)
  const afterInspector = events(service.fixture.directory).length
  assert(afterInspector === beforeInspector,
    `the reviewer wrote ${afterInspector - beforeInspector} events for a skip`)

  // 5. The explicit Skip control in the reviewer does the same, and advances.
  await click('.quiz-tile:nth-child(3) .inspect-choice')
  await browser.waitFor(`document.querySelector('dialog[open] .skip-character') !== null`)
  await click('dialog[open] .skip-character')
  await browser.waitFor(`document.querySelector('dialog[open]') === null`)
  assert(events(service.fixture.directory).length === afterInspector, 'the Skip control wrote a review')

  // 6. A round in which every crop is skipped posts nothing at all.
  const beforeAll = events(service.fixture.directory).length
  await click('.quiz-actionbar .skip-selected')
  await browser.waitFor(`document.querySelector('.quiz-tile.skipped') !== null`, 15000)
  let guard = 0
  while (guard++ < 24
         && await browser.evaluate(`!!document.querySelector('.quiz-actionbar .skip-selected')`)
         && !(await browser.evaluate(`document.querySelector('.quiz-actionbar .skip-selected')?.disabled ?? true`))) {
    await click('.quiz-actionbar .skip-selected')
    await sleep(250)
  }
  await sleep(500)
  assert(events(service.fixture.directory).length === beforeAll,
    'a round where everything was skipped still posted')
  assert(await browser.evaluate(reviewedText) === reviewedSecond,
    'skipping everything changed the reviewed count')
  assert(await browser.evaluate(`!!document.querySelector('.quiz-submit .next-round')`),
    'a round with everything skipped is a dead end')
  assert(await browser.evaluate(`document.querySelector('.quiz-submit .next-round')?.disabled`) === false,
    'the way out of an all-skipped round is disabled')
  await click('.quiz-submit .next-round')
  await browser.waitFor(`document.querySelectorAll('.quiz-tile.skipped').length === 0`, 20000)
  assert(events(service.fixture.directory).length === beforeAll,
    'moving to the next round posted something')
  assert(await browser.evaluate(`document.querySelectorAll('.quiz-tile').length`) > 0,
    'the next round is empty')

  // 7. In the collection inspector, Skip and Can't tell advance the existing queue in place.
  await browser.evaluate(`location.hash = '#/'`)
  await browser.waitFor(`document.querySelectorAll('.glyph-tile[data-unit]').length > 3`)
  const order = await browser.evaluate(`[...document.querySelectorAll('.glyph-tile[data-unit]')].map(t => t.dataset.unit)`)
  await click('.glyph-tile[data-unit]')
  await browser.waitFor(`document.querySelector('.inspector-navigation > span')?.textContent.startsWith('1 /')`)
  await click('.skip-character')
  await browser.waitFor(`document.querySelector('.inspector-navigation > span')?.textContent.startsWith('2 /')`)
  await browser.waitFor(`document.querySelector('dialog .issue-card[data-issue="unclear"]')`)
  await click('dialog .issue-card[data-issue="unclear"]')
  await browser.waitFor(`document.querySelector('.inspector-navigation > span')?.textContent.startsWith('3 /')`)
  await click('.close-inspector')
  assert(events(service.fixture.directory).length === beforeAll, 'queue skipping wrote an event')
  assert(JSON.stringify(await browser.evaluate(`[...document.querySelectorAll('.glyph-tile[data-unit]')].map(t => t.dataset.unit)`)) === JSON.stringify(order), 'skipping refreshed or reordered the collection')

  assert(errors.length === 0, `page errors: ${errors.join('; ')}`)
  console.log(`skip: ${reviewEvents.length} answers saved for ${before.length} crops offered, skipped crop excluded`)
  console.log('      no journal write and no reviewed-count change for skip, Can\'t tell in round or reviewer')
} finally {
  if (browser) await browser.close()
  await service.stop()
}
