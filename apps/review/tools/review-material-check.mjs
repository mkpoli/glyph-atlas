// Read-only browser check against the running Atlas. No review writes are allowed.
import Browser from './browser.mjs'

const base = process.argv[2] || 'http://127.0.0.1:8770'
const browser = await Browser.launch({ width: 1440, height: 1000 })
const writes = [], errors = [], requests = []
const assert = (condition, message) => { if (!condition) throw new Error(message) }
browser.listeners.push(event => {
  if (event.method === 'Runtime.exceptionThrown') errors.push(event.params.exceptionDetails.text)
  if (event.method !== 'Fetch.requestPaused') return
  const { requestId, request } = event.params
  const url = new URL(request.url)
  if (url.pathname === '/atlas') requests.push(url.searchParams.get('production'))
  if (request.method !== 'GET') {
    writes.push(request.url)
    browser.send('Fetch.failRequest', { requestId, errorReason: 'BlockedByClient' })
  } else browser.send('Fetch.continueRequest', { requestId })
})
const click = async selector => {
  await browser.waitFor(`document.querySelector(${JSON.stringify(selector)})?.disabled === false`)
  await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`)
  const point = await browser.centre(selector)
  await browser.click(point.x, point.y)
}
const settled = () => browser.waitFor('!document.querySelector(".review-material select").disabled')
const material = () => browser.evaluate('document.querySelector(".review-material select").value')
const choose = async value => {
  await browser.evaluate(`(() => {
    const select = document.querySelector('.review-material select')
    select.value = ${JSON.stringify(value)}
    select.dispatchEvent(new Event('change', {bubbles:true}))
  })()`)
  await settled()
}
try {
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] })
  await browser.goto(base + '/#/review', { waitFor: 'document.querySelectorAll(".quiz-choice:not(:disabled)").length > 0' })
  await settled()
  assert(await material() === 'not:printed/type', 'initial review included movable type')
  assert(!await browser.evaluate('[...document.querySelectorAll(".quiz-tile [data-production]")].some(t => t.dataset.production.startsWith("printed/type"))'), 'movable type in default queue')
  const original = await browser.evaluate('[...document.querySelectorAll(".quiz-tile")].map(t => t.dataset.unit)')
  await click('.quiz-choice:not(:disabled)')
  await choose('printed/type')
  await browser.waitFor('document.querySelectorAll(".quiz-tile").length > 0')
  assert(await browser.evaluate('[...document.querySelectorAll(".quiz-tile [data-production]")].every(t => t.dataset.production.startsWith("printed/type"))'), 'explicit movable-type queue is mixed')
  const start = requests.length
  if (await browser.evaluate('!!document.querySelector(".load-more:not(:disabled)")')) await click('.load-more')
  await settled()
  assert(requests.slice(start).every(scope => scope === 'printed/type'), 'load more escaped material filter')
  await click('.quiz-choice:not(:disabled)')
  const printed = await browser.evaluate('[...document.querySelectorAll(".quiz-tile")].map(t => t.dataset.unit)')
  await click('.history-character:first-child')
  assert(await material() === 'not:printed/type', 'history lost original material filter')
  assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.selected").length') === 1, 'history lost original selection')
  assert(JSON.stringify(await browser.evaluate('[...document.querySelectorAll(".quiz-tile")].map(t => t.dataset.unit)')) === JSON.stringify(original), 'history changed original crops')
  await click('.history-character:last-child')
  assert(await material() === 'printed/type', 'explicit return lost printed material')
  assert(await browser.evaluate('document.querySelectorAll(".quiz-tile.selected").length') === 1, 'history lost printed selection')
  assert(JSON.stringify(await browser.evaluate('[...document.querySelectorAll(".quiz-tile")].map(t => t.dataset.unit)')) === JSON.stringify(printed), 'history changed printed crops')
  await choose('mixed')
  assert(await material() === 'mixed', 'empty material selection retained previous material')
  assert(!await browser.evaluate('[...document.querySelectorAll(".quiz-tile [data-production]")].some(t => t.dataset.production.startsWith("printed/type"))'), 'old printed crops remained after filtering')
  await choose('not:printed/type')
  await browser.waitFor('document.querySelectorAll(".quiz-tile").length > 0')
  assert(!await browser.evaluate('[...document.querySelectorAll(".quiz-tile [data-production]")].some(t => t.dataset.production.startsWith("printed/type"))'), 'return to default leaked printed crops')
  await browser.setViewport(390, 844)
  assert(await browser.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'material control overflows mobile')
  assert(!writes.length, 'changing filters tried to save reviews')
  assert(!errors.length, errors.join(' | '))
  console.log('passed: default exclusion, explicit movable type, load more, material history, draft preservation, empty scope, mobile; no reviews written')
} finally { await browser.close() }
