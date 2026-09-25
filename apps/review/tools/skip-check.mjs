#!/usr/bin/env bun
/**
 * Skip, in a real browser, against a disposable dataset.
 *
 * The behaviour under test is an absence: skipping must write nothing, count nothing, and still move
 * the reader on. So every assertion about a skip is paired with a count of the events the service
 * recorded and the number the interface says it has reviewed.
 *
 * Skip is the only way to leave a crop unjudged, and it is checked across every surface it appears
 * on: a crop's own control in the grid, the round's bulk controls, a crop's own review step, and the
 * reviewer that opens from a crop.
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

  // 2. It is out of the round that gets saved: select the rest, give each the same problem, save, and
  // the answers are the rest.
  await click('.quiz-submit .review-selected')
  for (let n = 0; n < before.length; n++) {
    if (!/to decide/.test(await browser.evaluate(`document.querySelector('.round-selection')?.textContent ?? ''`))) break
    await browser.waitFor(`document.querySelector('.quiz-workspace .issue-card[data-issue="crop"]') !== null`)
    await click('.quiz-workspace .issue-card[data-issue="crop"]')
    await sleep(150)
    if (await browser.evaluate(`!!document.querySelector('.quiz-submit .next-crop:not(:disabled)')`)) await click('.quiz-submit .next-crop')
    await sleep(150)
  }
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

  // 3. Skip inside a crop's own review step skips that crop alone: the decision already made for the
  // other selected crop stays, and nothing is written.
  const beforeSecond = events(service.fixture.directory).length
  const reviewedSecond = await browser.evaluate(reviewedText)
  await browser.waitFor(`document.querySelectorAll('.quiz-choice').length > 1`)
  await click('.quiz-tile:nth-child(1) .quiz-choice')
  await click('.quiz-tile:nth-child(2) .quiz-choice')
  await click('.quiz-submit .review-selected')
  await browser.waitFor(`document.querySelector('.issue-card[data-issue="crop"]') !== null`)
  await click('.issue-card[data-issue="crop"]')
  await click('.quiz-submit .next-crop')
  await browser.waitFor(`document.querySelector('.skip-current') !== null`)
  await click('.skip-current')
  await sleep(400)
  assert(events(service.fixture.directory).length === beforeSecond, 'skipping from the review step wrote to the journal')
  assert(await browser.evaluate(reviewedText) === reviewedSecond, 'skipping from the review step was counted as reviewed')
  const tally = await browser.evaluate(`document.querySelector('.round-selection')?.textContent ?? ''`)
  assert(/1 skipped/.test(tally), 'skipping from the review step did not skip the crop')
  assert(/1 issues/.test(tally), 'skipping one crop discarded the decision made for the other')
  await click('.focus-back')
  await browser.waitFor(`document.querySelector('.quiz-grid') !== null`)

  // 4. The Skip control in the reviewer closes without writing.
  const beforeInspector = events(service.fixture.directory).length
  await click('.quiz-tile:nth-child(2) .inspect-choice')
  await browser.waitFor(`document.querySelector('dialog[open] .skip-character') !== null`)
  await click('dialog[open] .skip-character')
  await browser.waitFor(`document.querySelector('dialog[open]') === null`, 10000)
  const afterInspector = events(service.fixture.directory).length
  assert(afterInspector === beforeInspector, `the reviewer's Skip control wrote ${afterInspector - beforeInspector} events`)

  // 5. A round in which every crop is skipped posts nothing until the reader moves on.
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
  // Moving on records the skips against the reviewer, and nothing else: no decision, no count.
  const skippedAll = (await browser.evaluate(rows)).filter(row => row.skipped).map(row => row.id)
  await click('.quiz-submit .next-round')
  await browser.waitFor(`document.querySelectorAll('.quiz-tile.skipped').length === 0`, 20000)
  await sleep(500)
  const recorded = events(service.fixture.directory).slice(beforeAll)
  assert(recorded.length === skippedAll.length && recorded.every(row => row.field === 'seen' && row.new === 'skipped'),
    `moving on recorded ${recorded.map(row => row.field + ':' + row.new).join(', ')} for ${skippedAll.length} skipped crops`)
  assert(recorded.every(row => skippedAll.includes(row.target_id)), 'a skip was recorded for a crop that was not skipped')
  assert(await browser.evaluate(reviewedText) === reviewedSecond, 'recording skips changed the reviewed count')
  // They rest for this reviewer: the next round does not deal them back.
  const next = (await browser.evaluate(rows)).map(row => row.id)
  assert(!next.some(id => skippedAll.includes(id)), 'a crop the reviewer just skipped was dealt back to them')
  const afterPass = events(service.fixture.directory).length

  // 6. In the collection inspector, Skip advances the existing queue in place, however many times
  // in a row it is pressed.
  await browser.evaluate(`location.hash = '#/'`)
  await browser.waitFor(`document.querySelectorAll('.glyph-tile[data-unit]').length > 3`)
  const order = await browser.evaluate(`[...document.querySelectorAll('.glyph-tile[data-unit]')].map(t => t.dataset.unit)`)
  await click('.glyph-tile[data-unit]')
  await browser.waitFor(`document.querySelector('.inspector-navigation > span')?.textContent.startsWith('1 /')`)
  await click('.skip-character')
  await browser.waitFor(`document.querySelector('.inspector-navigation > span')?.textContent.startsWith('2 /')`)
  await click('.skip-character')
  await browser.waitFor(`document.querySelector('.inspector-navigation > span')?.textContent.startsWith('3 /')`)
  await click('.close-inspector')
  assert(events(service.fixture.directory).length === afterPass, 'queue skipping wrote an event')
  assert(JSON.stringify(await browser.evaluate(`[...document.querySelectorAll('.glyph-tile[data-unit]')].map(t => t.dataset.unit)`)) === JSON.stringify(order), 'skipping refreshed or reordered the collection')

  assert(errors.length === 0, `page errors: ${errors.join('; ')}`)
  console.log(`skip: ${reviewEvents.length} answers saved for ${before.length} crops offered, skipped crop excluded`)
  console.log('      a skip writes nothing until the reader moves on, then only a skip record: no decision, no reviewed-count change, and the crop is not dealt back to them')
} finally {
  if (browser) await browser.close()
  await service.stop()
}
