#!/usr/bin/env bun
// Read-only export regression against the running API, using the staged frontend.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import Browser from './browser.mjs'

const index = process.argv.indexOf('--server')
const base = (index >= 0 ? process.argv[index + 1] : 'http://127.0.0.1:8770').replace(/\/$/, '')
const browser = await Browser.launch({ width: 1440, height: 1000 })
const requested = [], errors = [], writes = []
const assert = (value, message) => { if (!value) throw new Error(message) }
browser.listeners.push(event => {
  if (event.method === 'Runtime.exceptionThrown') errors.push(event.params.exceptionDetails.text)
  if (event.method !== 'Fetch.requestPaused') return
  const { requestId, request } = event.params
  const url = new URL(request.url)
  if (url.pathname === '/atlas/reviews') requested.push(url.pathname + url.search)
  if (request.method !== 'GET') {
    writes.push(url.pathname)
    browser.send('Fetch.fulfillRequest', { requestId, responseCode: 405, body: '' })
  } else if (url.pathname === '/' || url.pathname.startsWith('/assets/')) {
    const path = url.pathname === '/' ? 'index.html' : url.pathname.slice(1)
    browser.send('Fetch.fulfillRequest', { requestId, responseCode: 200,
      responseHeaders: [{ name: 'Content-Type', value: path.endsWith('.html') ? 'text/html' : path.endsWith('.css') ? 'text/css' : 'application/javascript' }],
      body: readFileSync(join(import.meta.dir, '..', 'dist-next', path)).toString('base64') })
  } else browser.send('Fetch.continueRequest', { requestId })
})
async function click(selector) {
  await browser.waitFor(`!!document.querySelector(${JSON.stringify(selector)})`)
  await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center',behavior:'instant'})`)
  const point = await browser.centre(selector)
  await browser.click(point.x, point.y)
}
try {
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] })
  await browser.goto(base + '/#/review', { waitFor: `!!document.querySelector('[aria-label="Review options"]')` })
  await click('[aria-label="Review options"]')
  await click('.options-menu button:last-of-type')
  await browser.waitFor('!!document.querySelector(".export-count")')
  assert(!await browser.evaluate('document.querySelector(".export-history input").checked'), 'incremental export is not the default')
  await click('.export-history input')
  try {
    await browser.waitFor('document.querySelector(".export-dialog textarea") && JSON.parse(document.querySelector(".export-dialog textarea").value).scope === "history"')
  } catch (error) {
    console.log(JSON.stringify({ requested, state: await browser.evaluate(`({checked:document.querySelector('.export-history input').checked,text:document.querySelector('.export-dialog').innerText})`), errors }))
    throw error
  }
  const full = await browser.evaluate('JSON.parse(document.querySelector(".export-dialog textarea").value)')
  assert(full.reviews.length === full.counts.total, 'full history count differs from its preview')
  assert(await browser.evaluate('document.querySelector(".export-actions a").getAttribute("href")') === '/atlas/reviews.json?include_processed=true', 'download and preview scopes differ')
  await click('.export-history input')
  await browser.waitFor('!!document.querySelector(".export-count")')
  const value = await browser.evaluate('document.querySelector(".export-dialog textarea")?.value')
  assert(!value || JSON.parse(value).scope === 'unprocessed', 'unchecking retained the full history')
  assert(requested.join('|') === '/atlas/reviews|/atlas/reviews?include_processed=true|/atlas/reviews', 'toggle requests have the wrong scope')
  assert(!errors.length && !writes.length, 'export caused an exception or write')
  console.log(`ok export: incremental default, ${full.reviews.length} history entries, matching download, return to incremental`)
} finally { await browser.close() }
