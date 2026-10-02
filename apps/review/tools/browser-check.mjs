#!/usr/bin/env bun
// Run through devrun. All editorial writes use a disposable dataset.
import { join } from 'node:path'
import { mkdirSync, readdirSync } from 'node:fs'
import Browser from './browser.mjs'
import { boot, options, events, units } from './harness.mjs'
const config = options(), service = await boot(config)
const screenshots = '/tmp/atlas-character-shots'
mkdirSync(screenshots, { recursive: true })
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })
  // Deterministic OCR suggestions for synthetic glyphs; real model smoke is a separate read-only check.
  // The fixture's kana have no entry in the 異体字 graph, so the written-form picker is given one
  // variant, a hentaigana, to offer.
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*\/suggestions?*' }, { urlPattern: '*\/layers/characters/*' }] })
  const suggested = { status: 'ready', candidates: [
    { text: 'シヨロ', engine: 'NDLkotenOCR', score: .9 },
    { text: 'カ', engine: 'fixture classifier', score: .7 },
    { text: 'ア', engine: 'fixture classifier', score: .2 },
  ] }
  const variantCard = { variants: { items: [{ char: '𛀂', code_point: 'U+1B002', sources: ['fixture'] }], related: [] } }
  browser.listeners.push(m => { if (m.method === 'Fetch.requestPaused') browser.send('Fetch.fulfillRequest', {
    requestId: m.params.requestId, responseCode: 200,
    responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
    body: Buffer.from(JSON.stringify(m.params.request.url.includes('/layers/characters/') ? variantCard : suggested)).toString('base64'),
  }) })
  async function click(selector) {
    await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`)
    // A control still disabled while a load is in flight ignores the click; wait for it to take one.
    await browser.waitFor(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); return el !== null && !el.disabled })()`)
    const p = await browser.centre(selector); await browser.click(p.x, p.y)
  }
  async function route(hash, ready) { await browser.evaluate(`visit(${JSON.stringify(hash)})`); await browser.waitFor(ready) }
  const roundReady = 'document.querySelectorAll(".quiz-choice").length > 0 && !document.querySelector(".quiz-submit .primary")?.disabled'
  const inspectorReady = 'document.querySelector("dialog[open] .crop-viewport")?.dataset.ready === "true" && !document.querySelector(".save-character")?.disabled'
  await browser.goto(service.base + '/en', { waitFor: 'document.querySelectorAll(".glyph-tile").length > 0' })
  await browser.waitFor('Array.from(document.querySelectorAll(".glyph-grid img")).slice(0,12).every(i => i.complete && i.naturalWidth)')
  assert(!await browser.evaluate('document.querySelector("nav").innerText.includes("Sources")'), 'old source navigation remains')
  const before = await browser.evaluate('document.querySelector(".glyph-grid img").src')
  await click('.shuffle')
  await browser.waitFor(`document.querySelector('.glyph-grid img')?.src !== ${JSON.stringify(before)}`)
  console.log('PASS crop grid and shuffle')

  // A crop not already read か, so the correction to カ below has a reading to carry.
  const gridOrder = await browser.evaluate('Array.from(document.querySelectorAll(".glyph-grid [data-unit]")).map(i=>i.dataset.unit)')
  const originalId = gridOrder.slice(9).find(id => units(config.directory)[id]?.reading !== 'か')
  await click(`.glyph-grid [data-unit="${originalId}"]`)
  await browser.waitFor(inspectorReady)
  assert(await browser.evaluate('document.querySelector("dialog[open] .record-id code").textContent') === originalId, 'the inspector opened another crop')
  const originalText = units(config.directory)[originalId].text_source
  const order = await browser.evaluate('Array.from(document.querySelectorAll(".glyph-grid [data-unit]")).map(i=>i.dataset.unit)')
  const scroll = await browser.evaluate('scrollY')
  await browser.evaluate('window.sameCollection = document.querySelector(".glyph-grid")')
  assert(!await browser.evaluate('!!document.querySelector("dialog[open] .advanced-edit, dialog[open] .reading-input")'), 'the inspector has no typed character or reading fields')
  await click('dialog .issue-card[data-issue="merged"]')
  await browser.waitFor('document.querySelector("dialog .suggestion-options") !== null')
  assert(await browser.evaluate('document.querySelector(".save-character").innerText.includes("Save problem")'), 'reporting an error must not confirm the wrong label')
  await browser.screenshot(join(screenshots, 'error-review-desktop.png'))
  await click('.save-character')
  await browser.waitFor('document.querySelector("dialog[open]") === null')
  assert(await browser.evaluate('document.querySelector(".glyph-grid") === window.sameCollection'), 'save must not remount the collection')
  assert(await browser.evaluate('JSON.stringify(Array.from(document.querySelectorAll(".glyph-grid [data-unit]")).map(i=>i.dataset.unit))') === JSON.stringify(order), 'save must preserve crop order')
  assert(await browser.evaluate('scrollY') === scroll, 'save must preserve collection scroll position')
  const firstReport = events(config.directory).find(e => e.target_id === originalId && e.field === 'review')
  assert(firstReport?.new === 'disputed' && JSON.parse(firstReport.evidence).issue === 'merged', 'joined report must persist without text')
  const reportedTile = `.glyph-grid [data-unit="${originalId}"]`
  console.log('PASS no typing, correct error save, back to a stable collection')

  await click(reportedTile)
  await browser.waitFor(inspectorReady)
  await click('dialog .issue-card[data-issue="reading"]')
  await browser.waitFor('document.querySelector("dialog .suggestion-options button")?.innerText === "カ"')
  await click('dialog .suggestion-options button')
  await click('dialog .no-suggestion')
  assert(!await browser.evaluate('!!document.querySelector("dialog .suggestion-options button[aria-pressed=true]")'),
    'None of these cancels the proposed identity')
  await click('dialog .suggestion-options button')
  await click('.save-character')
  await browser.waitFor('document.querySelector("dialog[open]") === null')
  assert(units(config.directory)[originalId].unicode === 'U+30AB', 'choosing an OCR suggestion updates the written identity')
  // A kana with one stated reading carries it along; what the transcriber typed stays in text_source.
  assert(units(config.directory)[originalId].reading === 'か', 'a corrected kana takes its reading from the character layer')
  assert(units(config.directory)[originalId].text_source === originalText, 'an identity correction keeps the transcribed text')
  console.log('PASS optional correction by clicking a suggestion')

  await click(reportedTile)
  await browser.waitFor(inspectorReady)
  await click('dialog .issue-card[data-issue="crop"]')
  await click('.adjust-crop')
  await browser.waitFor('document.querySelector(".context-region img")?.naturalWidth > 0')
  await browser.evaluate('document.querySelector(".context-region").scrollIntoView({block:"center"})')
  const region = await browser.evaluate('(() => { const r=document.querySelector(".context-region").getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}; })()')
  await browser.drag({x:region.x+region.w*.25,y:region.y+region.h*.25},{x:region.x+region.w*.65,y:region.y+region.h*.72})
  assert(await browser.evaluate('document.querySelector(".crop-change") !== null'), 'dragging adjusts crop')
  assert(await browser.evaluate('document.querySelector(".save-character").innerText.includes("Save & close")'), 'a redrawn crop is saved, not reported')
  await click('.save-character')
  await browser.waitFor('document.querySelector("dialog[open]") === null')
  assert(events(config.directory).some(e => e.field === 'box'), 'crop adjustment saved')
  // The redrawn crop is fixed by the save, so it is reviewed, not left needing fixing.
  assert(events(config.directory).filter(e => e.target_id === originalId && e.field === 'review').at(-1)?.new === 'reviewed', 'a redrawn crop stays flagged')
  console.log('PASS optional crop adjustment')

  await route('/en/review?grapheme=U%2B3042', roundReady)
  // A round saves only the problems a reviewer picked; crops left unselected are not confirmed.
  // A crop can be picked only once its image has loaded.
  await browser.waitFor('[...document.querySelectorAll(".quiz-tile img")].every(i => i.complete)', 15000)
  const viewable = await browser.evaluate('Array.from(document.querySelectorAll(".quiz-tile:not(.unavailable)")).map(tile => tile.dataset.unit)')
  assert(viewable.length >= 4, 'fixture has four viewable crops')
  const [joinedA, joinedB, skippedId] = viewable
  // Last in grid order, and not already read か, so the correction to カ has a reading to carry.
  const correctedId = viewable.slice(3).find(id => units(config.directory)[id]?.reading !== 'か')
  assert(correctedId, 'the fixture has a crop not read か')
  const tile = id => `.quiz-tile[data-unit="${id}"]`
  const beforeCorrection = units(config.directory)[correctedId]
  const roundMark = events(config.directory).length
  // The id on a tile's hover card copies itself and leaves the tile unselected. Headless Chromium has
  // no hovering pointer, so the card never takes pointer events here and the id is clicked directly.
  await browser.send('Browser.grantPermissions', { permissions: ['clipboardReadWrite', 'clipboardSanitizedWrite'], origin: new URL(service.base).origin })
  await browser.evaluate(`document.querySelector('${tile(joinedA)} .copy-inline').click()`)
  await Bun.sleep(100)
  await browser.screenshot(join(screenshots, 'quiz-copy-id-desktop.png'))
  assert(await browser.evaluate('navigator.clipboard.readText()') === joinedA, 'the tile id is copied')
  assert(await browser.evaluate(`document.querySelector('${tile(joinedA)} .copy-inline').textContent === 'Copied'`), 'the tile id says it was copied')
  assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.selected").length === 0'), 'copying the id selects no crop')
  for (const id of [joinedA, joinedB, skippedId, correctedId]) await click(tile(id) + ' .quiz-choice')
  assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.selected").length === 4'), 'multiple crops selected')
  assert(!await browser.evaluate('!!document.querySelector(".issue-picker")'), 'selecting must not ask for a problem yet')
  await click(tile(skippedId) + ' .skip-choice')
  assert(await browser.evaluate(`document.querySelector('${tile(skippedId)}').classList.contains('skipped')`), 'Skip skips the crop')
  await click('.review-selected')
  await browser.waitFor('!!document.querySelector(".issue-picker")')
  const focused = () => browser.evaluate('document.querySelector(".focus-figure").dataset.unit')
  assert(await focused() === joinedA, 'the first selected crop is asked first')
  await click('.quiz-workspace [data-issue="merged"]')
  await click('.next-crop')
  await browser.waitFor(`document.querySelector(".focus-figure")?.dataset.unit === ${JSON.stringify(joinedB)}`)
  // The keyboard path: 2 is Joined characters, and Ctrl+Enter moves on.
  await browser.key('2')
  // Joined characters offers the suggestions for what the crop reads; the problem is then chosen.
  await browser.waitFor('!!document.querySelector(".quiz-workspace .reading-suggestions")')
  await browser.key('Enter', { ctrl: true })
  await browser.waitFor(`document.querySelector(".focus-figure")?.dataset.unit === ${JSON.stringify(correctedId)}`)
  await click('.quiz-workspace [data-issue="reading"]')
  await browser.waitFor('document.querySelector(".quiz-workspace .suggestion-options button")?.innerText === "カ"')
  await click('.quiz-workspace .suggestion-options button')
  await click('.quiz-workspace .no-suggestion')
  await click('.quiz-workspace .suggestion-options button')
  await browser.screenshot(join(screenshots, 'error-quiz-desktop.png'))
  await browser.evaluate('document.activeElement.blur()')
  await browser.key('Enter', { ctrl: true })
  await browser.waitFor('document.querySelector(".round-count")?.innerText.includes("3 problems saved")')
  const written = events(config.directory).slice(roundMark)
  const rounds = written.filter(e => e.field === 'review')
  assert(rounds.length === 3, `only the picked problems are saved, got ${rounds.length}`)
  // A skip is recorded as seen, which counts toward "hard to read"; it is never a review.
  const skipRows = written.filter(e => e.target_id === skippedId)
  assert(skipRows.some(e => e.field === 'seen' && e.new === 'skipped'), 'the skip was not recorded')
  assert(skipRows.every(e => e.field === 'seen'), 'Skip wrote a review')
  // Crops shown but not picked are recorded as seen, never judged.
  const unpicked = written.filter(e => ![joinedA, joinedB, skippedId, correctedId].includes(e.target_id))
  assert(unpicked.length > 0 && unpicked.every(e => e.field === 'seen'), 'crops shown but not picked are recorded as seen only')
  assert(rounds.filter(e => e.new === 'disputed' && JSON.parse(e.evidence).issue === 'merged').length === 2, 'joined crops remain flagged')
  assert(rounds.find(e => e.target_id === correctedId)?.new === 'reviewed', 'an explicit correction confirms the crop')
  const corrected = units(config.directory)[correctedId]
  assert(corrected.unicode === 'U+30AB', 'the round changes the selected identity')
  assert(corrected.reading === 'か' && corrected.text_source === beforeCorrection.text_source,
    'a corrected kana takes its reading from the character layer and keeps the transcribed text')
  const exported = await (await fetch(service.base + '/atlas/reviews')).json()
  const correctionReview = exported.reviews.filter(row => row.event.target_id === correctedId).pop()
  assert(correctionReview?.current, 'the round identity correction is current in the export')
  assert(JSON.parse(correctionReview.event.evidence).correction?.unicode === 'U+30AB', 'the export carries the corrected identity')
  console.log('PASS multi-select, one problem per crop, keyboard pick and save, only picked problems saved')

  // The last round survives a reload and can be undone whole: identity and reading come back.
  const undoRound = '.undo-round:not(.shape-toggle):not(.suspect-toggle)'
  await browser.send('Page.reload')
  await browser.waitFor(roundReady)
  assert(await browser.evaluate(`document.querySelector('${undoRound}') !== null`), 'last round retained after reload')
  const undoMark = events(config.directory).length
  await click(undoRound)
  await browser.waitFor(`document.querySelector('${undoRound}') === null`)
  const undone = events(config.directory).slice(undoMark)
  // Undo compensates everything the round wrote, the seen and skip records included.
  assert(undone.every(e => e.evidence?.startsWith('undo of ')), 'undo writes only compensating events')
  assert(undone.length === written.length, `undo wrote ${undone.length} events for a round of ${written.length}`)
  const restored = units(config.directory)[correctedId]
  assert(restored.unicode === beforeCorrection.unicode && restored.reading === beforeCorrection.reading,
    'undo restores identity and reading')
  console.log('PASS durable undo restores identity and reading')

  await browser.waitFor(roundReady)
  // Select all takes every crop whose image has loaded, so the count is compared once every image
  // has either loaded or failed.
  await browser.waitFor('[...document.querySelectorAll(".quiz-tile img")].every(i => i.complete)', 15000)
  const loadedTiles = '[...document.querySelectorAll(".quiz-tile:not(.unavailable):not(.skipped):not(.recorded)")].filter(t => t.querySelector("img")?.complete && t.querySelector("img").naturalWidth).length'
  await click('.stage-toolbar .bulk-toggle')
  const chosen = await browser.evaluate('document.querySelectorAll(".quiz-tile.selected").length')
  assert(chosen > 0 && chosen === await browser.evaluate(loadedTiles), 'select all viewable crops')
  await click('.stage-toolbar .bulk-toggle')
  assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.selected").length === 0'), 'deselect all')
  const staleId = await browser.evaluate('document.querySelector(".quiz-tile:not(.unavailable)").dataset.unit')
  await click(tile(staleId) + ' .quiz-choice')
  await click('.review-selected')
  await browser.waitFor('!!document.querySelector(".issue-picker")')
  await click('.quiz-workspace [data-issue="blank"]')
  const mark = events(config.directory).length
  await fetch(service.base + '/reviews', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ target_type:'unit', target_id:staleId, field:'note', new:'Concurrent edit', client_id:'other' }) })
  await click('.save-round')
  await browser.waitFor('document.querySelector(".quiz-workspace .error-message")?.innerText.includes("round changed")')
  assert(events(config.directory).length === mark + 1, 'stale round has no partial saves')
  assert(await browser.evaluate('document.querySelector(".quiz-workspace [data-issue=blank]")?.getAttribute("aria-pressed") === "true"'), 'stale round keeps choices')
  console.log('PASS conflicts preserve choices without partial saves')

  await browser.setViewport(390, 844)
  await browser.screenshot(join(screenshots, 'error-quiz-mobile.png'))
  assert(await browser.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'quiz mobile overflow')
  await route('/en', 'document.querySelectorAll(".glyph-tile").length > 0')
  await click('.glyph-tile')
  await browser.waitFor(inspectorReady)
  await click('dialog .issue-card[data-issue="merged"]')
  await browser.screenshot(join(screenshots, 'error-review-mobile.png'))
  assert(await browser.evaluate('document.querySelector("dialog").scrollWidth <= innerWidth + 1'), 'reviewer mobile overflow')
  await click('.close-inspector')
  console.log('PASS mobile error choices and continuous reviewer')

  // Saving closes the inspector until the reader turns on "Next after saving"; then a save and a
  // skip both go on to the next crop of the list, and both buttons say so.
  await browser.setViewport(1440, 1000)
  await route('/en', 'document.querySelectorAll(".glyph-tile").length > 2')
  const listed = await browser.evaluate('Array.from(document.querySelectorAll(".glyph-grid [data-unit]")).map(i=>i.dataset.unit)')
  const shownId = 'document.querySelector("dialog[open] .record-id code")?.textContent'
  const labels = 'document.querySelector(".save-character").innerText + " | " + document.querySelector(".skip-character").innerText'
  await click(`.glyph-grid [data-unit="${listed[0]}"]`)
  await browser.waitFor(inspectorReady)
  assert(await browser.evaluate('document.querySelector(".advance-switch").getAttribute("aria-checked")') === 'false', 'the inspector goes on to the next crop by default')
  const closing = await browser.evaluate(labels)
  assert(closing.includes('Looks right & close') && closing.includes('Skip & close'), 'the buttons do not say they close: ' + closing)
  await browser.screenshot(join(screenshots, 'advance-off-light.png'))
  await click('.skip-character')
  await browser.waitFor('document.querySelector("dialog[open]") === null')
  await click(`.glyph-grid [data-unit="${listed[0]}"]`)
  await browser.waitFor(inspectorReady)
  await click('.advance-switch')
  const advancing = await browser.evaluate(labels)
  assert(advancing.includes('Looks right & next') && advancing.includes('Skip →'), 'the buttons do not say they go on: ' + advancing)
  await browser.setColorScheme('dark')
  await browser.screenshot(join(screenshots, 'advance-on-dark.png'))
  await browser.setColorScheme('light')
  await browser.screenshot(join(screenshots, 'advance-on-light.png'))
  const beforeAdvance = events(config.directory).length
  await click('.save-character')
  await browser.waitFor(`${shownId} === ${JSON.stringify(listed[1])}`)
  await browser.waitFor(inspectorReady)
  assert(events(config.directory).length > beforeAdvance, 'the save before going on was not recorded')
  await browser.waitFor('document.activeElement?.classList.contains("save-character")')
  await click('.skip-character')
  await browser.waitFor(`${shownId} === ${JSON.stringify(listed[2])}`)
  assert(await browser.evaluate('JSON.parse(localStorage.getItem("atlas.advance"))') === true, 'the choice is not remembered')
  await click('.advance-switch')
  await click('.close-inspector')
  await browser.waitFor('document.querySelector("dialog[open]") === null')
  console.log('PASS saving and skipping go on to the next crop only when asked')

  // The written form: picked from the variants or typed as a description, saved on its own. The crop
  // keeps its character, revision and review, and is not flagged.
  await route('/en', 'document.querySelectorAll(".glyph-tile").length > 2')
  const formId = await browser.evaluate('document.querySelectorAll(".glyph-grid [data-unit]")[3].dataset.unit')
  const formBefore = units(config.directory)[formId]
  await click(`.glyph-grid [data-unit="${formId}"]`)
  await browser.waitFor(inspectorReady)
  const formShown = 'document.querySelector("dialog[open] .form-value b")?.textContent'
  assert(await browser.evaluate(formShown) === await browser.evaluate('document.querySelector(".inspector-title h2").textContent'), 'the written form starts as the label')
  await click('.form-summary')
  await browser.waitFor('document.querySelector(".form-variant")?.textContent === "𛀂"')
  await browser.screenshot(join(screenshots, 'written-form-desktop-light.png'))
  await browser.setColorScheme('dark')
  await browser.screenshot(join(screenshots, 'written-form-desktop-dark.png'))
  await browser.setColorScheme('light')
  const formMark = events(config.directory).length
  await click('.form-variant')
  await browser.waitFor(`${formShown} === "𛀂"`)
  const typeForm = async text => {
    await browser.evaluate('(() => { const i = document.querySelector(".form-input input"); i.focus(); i.select() })()')
    await browser.send('Input.insertText', { text })
  }
  await click('.form-summary')
  await typeForm('⿺辶')
  await browser.waitFor('document.querySelector(".form-panel .form-error")?.textContent.includes("missing")')
  assert(await browser.evaluate('document.querySelector(".form-apply").disabled'), 'a malformed description cannot be saved')
  await typeForm('⿺辶𦊷')
  await browser.waitFor('!document.querySelector(".form-apply").disabled')
  await click('.form-apply')
  await browser.waitFor(`${formShown} === "⿺辶𦊷"`)
  const formEvents = events(config.directory).slice(formMark)
  assert(formEvents.length === 2 && formEvents.every(e => e.field === 'written_form' && e.target_id === formId),
    'only the written form is recorded: ' + formEvents.map(e => e.field).join(', '))
  assert(JSON.stringify(formEvents.map(e => e.new)) === JSON.stringify(['𛀂', '⿺辶𦊷']), 'the picked and the typed forms are recorded')
  const formAfter = units(config.directory)[formId]
  assert(formAfter.written_form === '⿺辶𦊷' && formAfter.unicode === formBefore.unicode && formAfter.review === formBefore.review,
    'the crop keeps its character and review')
  assert(!await browser.evaluate('!!document.querySelector("dialog[open] .state-pill.flagged")'), 'the crop is not flagged')
  assert(!await browser.evaluate('document.querySelector(".save-character").disabled'), 'the crop can still be reviewed')
  await click('.close-inspector')
  await browser.waitFor('document.querySelector("dialog[open]") === null')
  await browser.waitFor(`document.querySelector('.glyph-grid [data-unit="${formId}"] .tile-details')?.textContent.includes("Written ⿺辶𦊷")`)
  await browser.setViewport(390, 844)
  await click(`.glyph-grid [data-unit="${formId}"]`)
  await browser.waitFor(inspectorReady)
  await click('.form-summary')
  await browser.waitFor('document.querySelector(".form-variant") !== null')
  assert(await browser.evaluate('document.querySelector("dialog").scrollWidth <= innerWidth + 1'), 'written form picker overflows on a phone')
  await browser.screenshot(join(screenshots, 'written-form-mobile-light.png'))
  await browser.setColorScheme('dark')
  await browser.screenshot(join(screenshots, 'written-form-mobile-dark.png'))
  await browser.setColorScheme('light')
  await click('.close-inspector')
  await browser.setViewport(1440, 1000)
  console.log('PASS written form picked, typed as a description, saved without a review')

  // The header fits a phone, a tablet and the narrowest full header in every language without
  // widening the page. On a phone it holds the wordmark, Quick review and the menu; the menu leads
  // with the nav's destinations, closes on Escape with focus back on its button and on a tap outside
  // it, and a destination chosen in it closes it.
  for (const [width, height] of [[360, 780], [800, 1000], [1100, 800]]) {
    await browser.setViewport(width, height)
    for (const tag of readdirSync(join(import.meta.dir, '../src/locales')).map(name => name.slice(0, -5))) {
      await browser.goto(`${service.base}/${tag}`)
      assert(await browser.evaluate('document.documentElement.scrollWidth <= innerWidth'), `the header widens the page in ${tag} at ${width} px`)
      assert(await browser.evaluate('[...document.querySelectorAll(".site-header a, .site-header button")].filter(e => e.offsetParent).every(e => { const r = e.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth })'),
        `a header control leaves the screen in ${tag} at ${width} px`)
    }
  }
  await browser.setViewport(360, 780)
  await browser.goto(`${service.base}/en`)
  const menuState = 'document.querySelector(".menu-button").getAttribute("aria-expanded") + " " + document.querySelector("#site-menu").hidden'
  assert(await browser.evaluate('document.querySelector(".site-nav").offsetParent === null'), 'the header nav shows on a phone')
  await click('.menu-button')
  await browser.waitFor(`${menuState} === "true false"`)
  assert(await browser.evaluate('JSON.stringify([...document.querySelectorAll(".menu-nav a")].map(a => a.href)) === JSON.stringify([...document.querySelectorAll(".site-nav a")].map(a => a.href))'),
    'the menu lacks a destination of the nav')
  assert(await browser.evaluate('[...document.querySelectorAll("#site-menu a, #site-menu button")].every(e => e.getBoundingClientRect().height >= 44 && e.getBoundingClientRect().right <= innerWidth)'),
    'a menu row is under 44 px or off the screen')
  assert(await browser.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'the open menu widens the page')
  await browser.screenshot(join(screenshots, 'phone-menu.png'))
  await browser.key('Escape')
  await browser.waitFor(`${menuState} === "false true"`)
  assert(await browser.evaluate('document.activeElement === document.querySelector(".menu-button")'), 'Escape leaves focus off the menu button')
  await click('.menu-button')
  await browser.waitFor(`${menuState} === "true false"`)
  await browser.click(175, 36)
  await browser.waitFor(`${menuState} === "false true"`)
  await click('.menu-button')
  await click('.menu-nav a[href$="/history"]')
  await browser.waitFor(`location.pathname === "/en/history" && ${menuState} === "false true"`)
  await browser.setViewport(1440, 1000)
  assert(await browser.evaluate('document.querySelector(".site-nav").offsetParent !== null && document.querySelector(".menu-nav").offsetParent === null'), 'the wide header lost its nav')
  console.log('PASS phone menu: every destination, Escape, a tap outside, no sideways scroll')

  await browser.send('Network.enable')
  await browser.send('Network.setBlockedURLs', { urls: ['*/atlas/media/*', '*/atlas/characters/*/image*'] })
  await browser.send('Network.setCacheDisabled', { cacheDisabled: true })
  await route('/en/review?grapheme=U%2B3044', 'document.querySelectorAll(".quiz-tile.unavailable").length > 0')
  await browser.waitFor('document.querySelectorAll(".quiz-tile.unavailable").length === document.querySelectorAll(".quiz-tile").length')
  assert(await browser.evaluate('document.querySelector(".quiz-submit .primary").classList.contains("next-round")'), 'unseen crops offer only Next round')
  const beforeUnavailable = events(config.directory).length
  await click('.next-round')
  await browser.waitFor('document.querySelectorAll(".quiz-tile.unavailable").length > 0')
  assert(events(config.directory).length === beforeUnavailable, 'unseen crops cannot be confirmed')
  assert(errors.length === 0, 'browser exceptions: ' + errors.join(', '))
  console.log('PASS unavailable images and no browser exceptions')
} catch (error) {
  if (browser) { console.log('Failure detail:', await browser.evaluate('Array.from(document.querySelectorAll(".error-message")).map(e=>e.innerText).join("; ")')); await browser.screenshot(join(screenshots, 'failure.png')) }
  console.log('Service failure:', service.log.slice(-8).join('').slice(-10000))
  throw error
} finally { await browser?.close(); await service.stop() }
