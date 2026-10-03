#!/usr/bin/env bun
// What a reader sees while the site's database is busy (an import holds it): the hosted Worker answers
// 503 with `code: 'busy'`. The fixture service never does, so the browser's requests are answered busy
// here, over the DevTools protocol. Run through devrun: `devrun bun apps/review/tools/busy-check.mjs`.
import { join } from 'node:path'
import { mkdirSync } from 'node:fs'
import Browser from './browser.mjs'
import { boot, options, events } from './harness.mjs'

const config = options(), service = await boot(config)
const screenshots = '/tmp/atlas-busy-shots'
mkdirSync(screenshots, { recursive: true })
const assert = (condition, message) => { if (!condition) throw new Error(message) }
let browser
try {
  browser = await Browser.launch({ width: 1280, height: 900 })
  await browser.writeAs('busy-check')
  // `busy(request, url)` answers the requests it picks with a busy 503 before they reach the service;
  // `busyAfter` answers busy once the service has handled them, as a Worker whose answer was lost
  // would. Every other request goes on as it is. `posts` keeps each POST that reached the service.
  let busy = () => false, busyAfter = () => false
  const posts = []
  const patterns = ['*/atlas/*', '*/layers/*'].flatMap(urlPattern => [{ urlPattern }, { urlPattern, requestStage: 'Response' }])
  await browser.send('Fetch.enable', { patterns })
  const answerBusy = requestId => browser.send('Fetch.fulfillRequest', { requestId, responseCode: 503, responseHeaders: [
    { name: 'Content-Type', value: 'application/json' }, { name: 'Retry-After', value: '1' }],
    body: Buffer.from(JSON.stringify({ detail: 'The database is updating. Try again in a moment.', code: 'busy' })).toString('base64') })
  browser.listeners.push(m => {
    if (m.method !== 'Fetch.requestPaused') return
    const { requestId, request, responseStatusCode } = m.params, url = new URL(request.url)
    if (responseStatusCode === undefined) {
      if (busy(request, url)) return answerBusy(requestId)
      if (request.method === 'POST') posts.push({ path: url.pathname, body: JSON.parse(request.postData ?? 'null') })
      return browser.send('Fetch.continueRequest', { requestId })
    }
    if (busyAfter(request, url)) return answerBusy(requestId)
    browser.send('Fetch.continueRequest', { requestId })
  })
  const kept = `new Promise(done => { const asked = indexedDB.open('atlas-outbox'); asked.onsuccess = () => {
    const count = asked.result.transaction('saves').objectStore('saves').count(); count.onsuccess = () => done(count.result) } })`
  const noteSays = text => `document.querySelector(".database-status")?.matches(":popover-open") && document.querySelector(".database-status").innerText.includes(${JSON.stringify(text)})`
  // The submissions the service recorded for a crop, by the id each save was made under.
  const saves = crop => new Set(events(config.directory).filter(e => e.target_id === crop && e.idempotency_key).map(e => e.idempotency_key))
  const inspectorReady = 'document.querySelector("dialog[open] .crop-viewport")?.dataset.ready === "true" && !document.querySelector(".save-character")?.disabled'
  const tile = n => browser.evaluate(`document.querySelectorAll(".glyph-grid [data-unit]")[${n}].dataset.unit`)
  const shown = 'document.querySelector(".database-status")?.matches(":popover-open") === true'
  async function click(selector) {
    await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`)
    const p = await browser.centre(selector); await browser.click(p.x, p.y)
  }

  await browser.goto(service.base + '/en', { waitFor: 'document.querySelectorAll(".glyph-tile").length > 0' })
  const order = 'Array.from(document.querySelectorAll(".glyph-grid [data-unit]")).slice(0, 12).map(i => i.dataset.unit).join()'
  const before = await browser.evaluate(order)

  // A crop read while the database is busy: the inspector shows the list's row, the note says the
  // read is being tried again, and the crop arrives once the database answers.
  const id = await browser.evaluate('document.querySelectorAll(".glyph-grid [data-unit]")[4].dataset.unit')
  const record = '/atlas/characters/' + encodeURIComponent(id)
  let refused = 0
  busy = (request, url) => request.method === 'GET' && url.pathname === record && refused++ < 3
  await click(`.glyph-grid [data-unit="${id}"]`)
  await browser.waitFor(shown, 10000)
  assert((await browser.evaluate('document.querySelector(".database-status").innerText')).includes('The database is updating'), 'the busy note has its words')
  await browser.screenshot(join(screenshots, 'read-busy-light.png'))
  await browser.setColorScheme('dark')
  await browser.screenshot(join(screenshots, 'read-busy-dark.png'))
  await browser.setColorScheme('light')
  await browser.waitFor('document.querySelector("dialog[open] .crop-viewport")?.dataset.ready === "true"', 30000)
  await browser.waitFor(`!(${shown})`, 5000)
  assert(refused >= 3, `the read was refused ${refused} times`)
  assert(!await browser.evaluate('!!document.querySelector("dialog[open] .error-message")'), 'the inspector shows no error')
  assert(await browser.evaluate('document.querySelector("dialog[open] .record-id code")?.textContent') === id, 'the inspector shows the crop')
  assert(await browser.evaluate(order) === before, 'the collection behind it is as it was')
  console.log(`PASS a busy read is tried again (${refused} busy answers), and the crop arrives with no error`)
  await browser.key('Escape')
  busy = () => false
  await browser.waitFor('!document.querySelector("dialog[open]")')

  // A save whose answer said busy although the service had recorded it: it is kept on this device,
  // the note says so, it is sent again, and the service holds it once.
  const saved = await tile(6), savePath = '/atlas/characters/' + encodeURIComponent(saved)
  await click(`.glyph-grid [data-unit="${saved}"]`)
  await browser.waitFor(inspectorReady, 30000)
  let lost = 0
  busyAfter = (request, url) => request.method === 'POST' && url.pathname === savePath && lost++ < 1
  await click('.save-character')
  await browser.waitFor(noteSays('Saved on this device'), 10000)
  await browser.screenshot(join(screenshots, 'write-kept-light.png'))
  await browser.setColorScheme('dark')
  await browser.screenshot(join(screenshots, 'write-kept-dark.png'))
  await browser.setColorScheme('light')
  await browser.waitFor('!document.querySelector("dialog[open]")', 30000)
  await browser.waitFor(`!(${shown})`, 10000)
  assert(posts.filter(p => p.path === savePath).length === 2, `the save reached the service ${posts.filter(p => p.path === savePath).length} times`)
  assert(saves(saved).size === 1, `the service recorded ${saves(saved).size} submissions for the crop`)
  assert(await browser.evaluate(kept) === 0, 'nothing is left on the device')
  console.log('PASS a save answered busy is kept, sent again, and recorded once')

  // A save kept when the page closes is sent by the next page.
  const later = await tile(7), laterPath = '/atlas/characters/' + encodeURIComponent(later)
  await click(`.glyph-grid [data-unit="${later}"]`)
  await browser.waitFor(inspectorReady, 30000)
  busy = (request, url) => request.method === 'POST' && url.pathname === laterPath
  await click('.save-character')
  await browser.waitFor(noteSays('Saved on this device'), 10000)
  assert(await browser.evaluate(kept) === 1, 'the save is kept on the device')
  await browser.goto(service.base + '/en', { waitFor: 'document.querySelectorAll(".glyph-tile").length > 0' })
  assert(saves(later).size === 0, 'the service has not recorded the save before the page reloads')
  busy = () => false
  await browser.waitFor(`!(${shown})`, 20000)
  assert(saves(later).size === 1, `the next page sent the save once (${saves(later).size})`)
  assert(await browser.evaluate(kept) === 0, 'nothing is left on the device after the reload')
  console.log('PASS a save kept when the page closed is sent by the next page, once')

  // Two saves kept one after another, the second made while the first waits: both are sent, in order,
  // each recorded once.
  await browser.key('Escape')
  await browser.waitFor('!document.querySelector("dialog[open]")')
  const [one, two] = [await tile(10), await tile(11)], pathOf = id => '/atlas/characters/' + encodeURIComponent(id)
  busy = (request, url) => request.method === 'POST' && [pathOf(one), pathOf(two)].includes(url.pathname)
  for (const id of [one, two]) {
    await click(`.glyph-grid [data-unit="${id}"]`)
    await browser.waitFor(inspectorReady, 30000)
    await click('.save-character')
    await browser.waitFor(noteSays('on this device'), 10000)
    await browser.key('Escape')
    await browser.waitFor('!document.querySelector("dialog[open]")')
  }
  await browser.waitFor(noteSays('2 saves kept on this device'), 10000)
  const sent = posts.length
  busy = () => false
  await browser.waitFor(`!(${shown})`, 20000)
  const went = posts.slice(sent).map(p => p.path)
  assert(went.indexOf(pathOf(one)) >= 0 && went.indexOf(pathOf(one)) < went.indexOf(pathOf(two)), `the saves went in order: ${went}`)
  assert(saves(one).size === 1 && saves(two).size === 1, 'each save is recorded once')
  assert(await browser.evaluate(kept) === 0, 'nothing is left on the device')
  console.log('PASS two saves kept one after another are both sent, in order, once each')

  // A save kept for an account that is not signed in waits for it: it is neither sent nor counted.
  await browser.evaluate(`new Promise(done => { const asked = indexedDB.open('atlas-outbox'); asked.onsuccess = () => {
    const put = asked.result.transaction('saves', 'readwrite').objectStore('saves').put({ key: '/atlas/rounds#x', path: '/atlas/rounds',
      body: { id: '00000000-0000-4000-8000-000000000000' }, user: 'someone-else', anonymous: false, seq: 1 }); put.onsuccess = () => done(true) } })`)
  const posted = posts.length
  await browser.goto(service.base + '/en', { waitFor: 'document.querySelectorAll(".glyph-tile").length > 0' })
  await Bun.sleep(2000)
  assert(!await browser.evaluate(shown), 'another account\'s save is not shown as sending')
  assert(!posts.slice(posted).some(p => p.path === '/atlas/rounds'), 'another account\'s save is not sent')
  assert(await browser.evaluate(kept) === 1, 'another account\'s save stays kept')
  await browser.evaluate(`new Promise(done => { const asked = indexedDB.open('atlas-outbox'); asked.onsuccess = () => {
    const gone = asked.result.transaction('saves', 'readwrite').objectStore('saves').clear(); gone.onsuccess = () => done(true) } })`)
  console.log('PASS a save kept for another account waits, unsent and uncounted')

  // A kept save the crop moved past meanwhile is refused when it is sent, and the inspector shows the
  // refusal as it shows any.
  const moved = await tile(8), movedPath = '/atlas/characters/' + encodeURIComponent(moved)
  await click(`.glyph-grid [data-unit="${moved}"]`)
  await browser.waitFor(inspectorReady, 30000)
  busy = (request, url) => request.method === 'POST' && url.pathname === movedPath
  await click('.save-character')
  await browser.waitFor(noteSays('Saved on this device'), 10000)
  // Someone else reviews the crop first, at the revision the kept save names.
  const pending = await browser.evaluate(`new Promise(done => { const asked = indexedDB.open('atlas-outbox'); asked.onsuccess = () => {
    const all = asked.result.transaction('saves').objectStore('saves').getAll(); all.onsuccess = () => done(all.result) } })`)
  const body = pending[0].body
  const other = await fetch(service.api + movedPath, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ ...body, id: crypto.randomUUID(), client_id: 'someone-else' }) })
  assert(other.ok, `the other review was saved (${other.status})`)
  busy = () => false
  await browser.waitFor('!!document.querySelector("dialog[open] .error-message")', 20000)
  await browser.waitFor(`!(${shown})`, 10000)
  const refusal = await browser.evaluate('document.querySelector("dialog[open] .error-message").innerText')
  await browser.screenshot(join(screenshots, 'write-refused.png'))
  assert(await browser.evaluate(kept) === 0, 'the refused save is not kept')
  console.log(`PASS a kept save the crop moved past is refused in the inspector: ${refusal.split('\n')[0]}`)
  await browser.key('Escape')
  await browser.waitFor('!document.querySelector("dialog[open]")')

  // The same refusal for a save an earlier page kept is shown in the corner, until it is dismissed.
  const orphan = await tile(9), orphanPath = '/atlas/characters/' + encodeURIComponent(orphan)
  await click(`.glyph-grid [data-unit="${orphan}"]`)
  await browser.waitFor(inspectorReady, 30000)
  busy = (request, url) => request.method === 'POST' && url.pathname === orphanPath
  await click('.save-character')
  await browser.waitFor(noteSays('Saved on this device'), 10000)
  const [left] = await browser.evaluate(`new Promise(done => { const asked = indexedDB.open('atlas-outbox'); asked.onsuccess = () => {
    const all = asked.result.transaction('saves').objectStore('saves').getAll(); all.onsuccess = () => done(all.result) } })`)
  await browser.goto(service.base + '/en', { waitFor: 'document.querySelectorAll(".glyph-tile").length > 0' })
  const first = await fetch(service.api + orphanPath, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ ...left.body, id: crypto.randomUUID(), client_id: 'someone-else' }) })
  assert(first.ok, `the other review was saved (${first.status})`)
  busy = () => false
  await browser.waitFor(noteSays('was not accepted'), 20000)
  await browser.screenshot(join(screenshots, 'write-refused-earlier-light.png'))
  await browser.setColorScheme('dark')
  await browser.screenshot(join(screenshots, 'write-refused-earlier-dark.png'))
  await browser.setColorScheme('light')
  await click('.database-status .refused button')
  await browser.waitFor(`!(${shown})`, 5000)
  assert(saves(orphan).size === 1, 'only the other review is recorded')
  console.log('PASS a refused save from an earlier page is shown in the corner and can be dismissed')
  console.log(`screenshots: ${screenshots}`)
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
