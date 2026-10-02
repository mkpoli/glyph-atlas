#!/usr/bin/env bun
/**
 * Quick Review's address, in a real browser, against a disposable dataset.
 *
 * The address names the round on screen, `/review/U+3042?production=…`. Opening one deals that round,
 * a new round adds a history entry and Back returns to the round before it as it was left, a reload
 * deals the round the address names, and `/review` alone picks a grapheme. The local service has no
 * accounts, so the browser's anonymous sign-in is answered here, and rounds are not written.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/review-address-check.mjs
 */
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'

const config = options()
const service = await boot(config)
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const json = (requestId, status, value) => browser.send('Fetch.fulfillRequest', { requestId, responseCode: status,
  responseHeaders: [{ name: 'Content-Type', value: 'application/json' }], body: Buffer.from(JSON.stringify(value)).toString('base64') })

try {
  browser = await Browser.launch({ width: 1280, height: 1000 })
  const errors = []
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*/api/auth/sign-in/anonymous' }, { urlPattern: '*/atlas/rounds' }] })
  browser.listeners.push(m => {
    if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.exception?.description ?? m.params.exceptionDetails?.text)
    if (m.method !== 'Fetch.requestPaused') return
    const { requestId, request } = m.params
    if (request.url.includes('/api/auth/')) json(requestId, 200, { token: 'address-check', user: { id: 'address-check', name: 'Anonymous', isAnonymous: true } })
    else json(requestId, 200, { id: JSON.parse(request.postData || '{}').id, results: [] })
  })
  const settled = 'document.querySelector(".review-material select") && !document.querySelector(".review-material select").disabled && document.querySelector(".quiz-grid")?.getAttribute("aria-busy") !== "true" && document.querySelectorAll(".quiz-tile").length > 0'
  const here = () => browser.evaluate('location.pathname + location.search')
  const target = () => browser.evaluate('document.querySelector(".target-character").innerText')
  const material = () => browser.evaluate('document.querySelector(".review-material select").value')
  const keyOf = text => [...text].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join('-')
  const settle = async () => { await Bun.sleep(300); await browser.waitFor(settled, 30000) }

  // /review picks a grapheme and the address then names it.
  await browser.goto(`${service.base}/en/review`, { waitFor: settled, timeout: 60000 })
  await browser.waitFor('location.pathname !== "/en/review"')
  const first = await target()
  assert(await here() === `/en/review/${keyOf(first)}?production=not:printed/type`, `the address names the round (${await here()})`)
  const length = await browser.evaluate('history.length')
  console.log(`ok   /review picks ${first}: ${await here()}`)

  // A selection left in the first round, then a new round in another material: a new entry.
  await browser.evaluate('document.querySelector(".quiz-choice:not(:disabled)").click()')
  const select = value => browser.evaluate(`(() => { const s = document.querySelector('.review-material select'); s.value = ${JSON.stringify(value)}; s.dispatchEvent(new Event('change', { bubbles: true })) })()`)
  await select('unknown'); await settle()
  const second = await target()
  assert(await here() === `/en/review/${keyOf(second)}?production=unknown`, `the address follows the round (${await here()})`)
  assert(await browser.evaluate('history.length') === length + 1, 'a new round adds one history entry')
  console.log(`ok   a new round in another material: ${await here()}`)

  // Back returns to the first round as it was left; Forward to the second.
  await browser.evaluate('history.back()'); await settle()
  await browser.waitFor(`location.search === "?production=not:printed/type"`)
  assert(await target() === first && await material() === 'not:printed/type', 'Back returns to the first round')
  assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.selected").length') === 1, 'Back keeps the round as it was left')
  await browser.evaluate('history.forward()'); await settle()
  await browser.waitFor(`location.search === "?production=unknown"`)
  assert(await target() === second && await material() === 'unknown', 'Forward returns to the second round')
  console.log('ok   Back and Forward step through the rounds, keeping the selection')

  // Next character records the round and deals another, in a new entry.
  const before = await browser.evaluate('history.length')
  await browser.evaluate('document.querySelector(".round-switch .quiet-link").click()')
  await browser.waitFor(`document.querySelector(".target-character").innerText !== ${JSON.stringify(second)}`, 30000); await settle()
  const third = await target()
  assert(await here() === `/en/review/${keyOf(third)}?production=unknown` && await browser.evaluate('history.length') === before + 1, `Next adds the next round's entry (${await here()})`)
  await browser.evaluate('history.back()'); await settle()
  await browser.waitFor(`document.querySelector(".target-character").innerText === ${JSON.stringify(second)}`)
  await browser.evaluate('history.forward()'); await settle()
  await browser.waitFor(`document.querySelector(".target-character").innerText === ${JSON.stringify(third)}`)
  console.log(`ok   Next deals ${third} in a new entry, and Back returns to ${second}`)

  // A crop opened in the inspector takes its own entry over the round's; closing it leaves the round as it was.
  await browser.evaluate('document.querySelector(".inspect-choice").click()')
  await browser.waitFor('location.pathname.startsWith("/en/crop/") && !!document.querySelector("dialog[open]")', 30000)
  await browser.evaluate('document.querySelector(".close-inspector").click()')
  await browser.waitFor('!document.querySelector("dialog[open]")', 30000); await settle()
  assert(await target() === third && await here() === `/en/review/${keyOf(third)}?production=unknown`, `closing the inspector returns to the round (${await here()})`)
  console.log('ok   the inspector opens a crop over the round and closing it returns to the round')

  // The header's Quick Review link picks a grapheme again, and the address names the round it deals.
  await browser.evaluate('document.querySelector(".review-link").click()')
  await browser.waitFor('location.pathname.startsWith("/en/review/")', 30000); await settle()
  const picked = await target()
  assert(await here() === `/en/review/${keyOf(picked)}?production=not:printed/type`, `the header link's round is addressed (${await here()}, ${picked})`)
  assert(await browser.evaluate('document.querySelector(".review-link").classList.contains("current")'), 'the header marks Quick Review')
  console.log(`ok   the header link deals ${picked} and addresses it`)
  await browser.evaluate('history.back()'); await settle()
  await browser.waitFor(`document.querySelector(".target-character").innerText === ${JSON.stringify(third)}`)

  // A reload deals the round the address names; Back from there deals the entry's round again.
  await browser.send('Page.reload'); await settle()
  assert(await target() === third && await material() === 'unknown' && await here() === `/en/review/${keyOf(third)}?production=unknown`, `a reload keeps the round (${await here()})`)
  assert(await browser.evaluate('document.querySelector(".review-link").classList.contains("current")'), 'the header marks Quick Review on a round\'s address')
  await browser.evaluate('history.back()'); await settle()
  await browser.waitFor(`location.pathname === "/en/review/${keyOf(second)}"`)
  assert(await target() === second && await here() === `/en/review/${keyOf(second)}?production=unknown`, `Back after a reload deals the earlier round (${await here()}, ${await target()})`)
  console.log('ok   a reload keeps the round, and Back after it deals the earlier one')

  // Opening an address deals that grapheme in that material.
  await browser.goto(`${service.base}/en/review/U+3044?production=all`, { waitFor: settled, timeout: 60000 })
  assert(await target() === 'い' && await material() === 'all' && await here() === '/en/review/U+3044?production=all', `the address deals い in all materials (${await here()})`)
  console.log('ok   /review/U+3044?production=all deals い in all materials')

  const missing = await fetch(`${service.base}/en/review/not-a-grapheme`)
  assert(missing.status === 404, `an address that names no grapheme is not found (${missing.status})`)
  console.log('ok   /review/not-a-grapheme is not found')
  assert(errors.length === 0, 'browser exceptions: ' + errors.join(', '))
  console.log('\nreview-address-check passed')
} catch (error) {
  if (browser) await browser.screenshot('/tmp/review-address-failure.png').catch(() => {})
  console.log('Service log:', service.log.slice(-6).join('').slice(-4000))
  throw error
} finally { await browser?.close(); await service.stop() }
