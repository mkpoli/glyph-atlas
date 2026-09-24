#!/usr/bin/env bun
// Read-only browser check: staged UI, live corpus, bounded intercepted candidate examples.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import Browser from './browser.mjs'

const base = process.argv[2] ?? 'http://127.0.0.1:8770'
const build = process.argv[3] ?? 'dist-family-check'
const citedId = 'ex:c358736e26b4c11e:3b550be22a3a596506f6'
const browser = await Browser.launch({ width: 1440, height: 1000 })
const errors = [], queries = []
let failCandidates = false
const assert = (test, message) => { if (!test) throw new Error(message) }
const fulfill = (requestId, value, code = 200) => browser.send('Fetch.fulfillRequest', {
  requestId, responseCode: code, responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
  body: Buffer.from(JSON.stringify(value)).toString('base64'),
})
browser.listeners.push(event => {
  if (event.method === 'Runtime.exceptionThrown') errors.push(event.params.exceptionDetails.text)
  if (event.method !== 'Fetch.requestPaused') return
  const { requestId, request } = event.params
  const url = new URL(request.url), path = url.pathname
  const respond = async () => {
    if (path === '/layers/candidates') queries.push(Object.fromEntries(url.searchParams))
    if (path === '/layers/candidates' && failCandidates) return fulfill(requestId, { detail: 'Test corpus outage' }, 502)
    if (request.method !== 'GET') return browser.send('Fetch.failRequest', { requestId, errorReason: 'BlockedByClient' })
    if (path === '/' || path.startsWith('/assets/')) {
      const file = join(import.meta.dir, '..', build, path === '/' ? 'index.html' : path.slice(1))
      return browser.send('Fetch.fulfillRequest', { requestId, responseCode: 200,
        responseHeaders: [{ name: 'Content-Type', value: path === '/' ? 'text/html' : path.endsWith('.css') ? 'text/css' : 'application/javascript' }],
        body: readFileSync(file).toString('base64') })
    }
    if (path === '/layers/suggest' && url.searchParams.get('q') === 'ム') return fulfill(requestId, {
      items: [{ code_point: 'U+30E0', char: 'ム', script: 'katakana' }, { code_point: 'U+53B6', char: '厶', script: 'han' }], more: 0,
    })
    if (path === '/atlas' && !url.searchParams.get('q') && !url.searchParams.get('reading')) {
      const [response, citedResponse] = await Promise.all([fetch(request.url), fetch(base + '/atlas/characters/' + encodeURIComponent(citedId))])
      const payload = await response.json()
      if (citedResponse.ok) { const cited = await citedResponse.json(); payload.items = [cited, ...(payload.items ?? []).filter(item => item.id !== citedId)] }
      return fulfill(requestId, payload, response.status)
    }
    return browser.send('Fetch.continueRequest', { requestId })
  }
  respond().catch(error => { errors.push(error.message); browser.send('Fetch.failRequest', { requestId, errorReason: 'Failed' }).catch(() => {}) })
})
const click = async selector => {
  await browser.waitFor(`document.querySelector(${JSON.stringify(selector)})`)
  await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`)
  const point = await browser.centre(selector)
  await browser.click(point.x, point.y)
}
const type = value => browser.evaluate(`(() => { const input = document.querySelector('[aria-label="Find a character"]'); input.focus(); input.value = ${JSON.stringify(value)}; input.dispatchEvent(new Event('input', {bubbles:true})) })()`)
const settled = () => browser.waitFor(`document.querySelector('.glyph-grid')?.getAttribute('aria-busy') === 'false'`, 45000)
try {
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] })
  await browser.goto(base + '/', { waitFor: 'document.querySelector(".collection-progress-link")' })
  await browser.waitFor(`document.querySelector('[data-unit="${citedId}"] .tile-production')`)
  assert(await browser.evaluate(`document.querySelector('[data-unit="${citedId}"] .tile-production').textContent === 'Movable type'`), 'cited occurrence lacks Movable type label')
  await click(`[data-unit="${citedId}"]`)
  await browser.waitFor(`document.querySelector('.character-dialog[open] .production-badge')`)
  assert(await browser.evaluate(`document.querySelector('.character-dialog[open] .production-badge').textContent === 'Movable type'`), 'inspector lost production metadata')
  await click('[aria-label="Close reviewer"]')

  await type('ム')
  await browser.waitFor(`document.querySelectorAll('.candidate > .reference-glyph .script-char').length === 2`)
  assert(await browser.evaluate(`JSON.stringify([...document.querySelectorAll('.candidate > .reference-glyph .script-char')].map(el => [el.dataset.script,el.textContent])) === JSON.stringify([['katakana','ム'],['kanji','厶']])`), 'similar shapes have no script distinction')
  assert(await browser.evaluate(`new Set([...document.querySelectorAll('.candidate > .reference-glyph .script-char')].map(el => getComputedStyle(el).color)).size === 2`), 'character colors are indistinguishable')
  assert(await browser.evaluate(`!document.querySelector('.script-badge') && ['Hiragana','Katakana','Kanji','Symbol'].every(name => document.querySelector('.candidate-list .script-legend')?.textContent.includes(name))`), 'tags remain or the color legend is missing')
  assert(await browser.evaluate(`document.querySelector('.candidate > .reference-glyph .script-text')?.getAttribute('aria-label').includes('Katakana')`), 'script identity is only conveyed by color')
  await browser.screenshot('/tmp/atlas-script-colors.png')
  await type('トモ')
  await browser.waitFor(`document.querySelector('.candidate-row .zi-link[href*="%F0%AA%9C%88"]')`)
  assert(await browser.evaluate(`(() => { const link = document.querySelector('.candidate-row .zi-link[href*="%F0%AA%9C%88"]'); return link.target === '_blank' && link.rel.includes('noopener') && link.rel.includes('noreferrer') && !link.closest('button') })()`), 'supplementary zi.tools link is broken or nested')

  await type('仮')
  await browser.waitFor(`document.querySelector('.family')?.getAttribute('aria-pressed') === 'true'`, 30000)
  await settled()
  await browser.key('Escape')
  assert(queries.some(row => row.code_point === 'U+4EEE' && row.scope === 'grapheme'), 'family search did not use union scope')
  assert(await browser.evaluate(`document.querySelectorAll('.member').length === 2`), 'family lost distinct written characters')
  const hasUnassigned = await browser.evaluate(`!![...document.querySelectorAll('.glyph-tile.corpus .tile-reading')].find(el => el.textContent === 'Unassigned')`)
  assert(hasUnassigned, 'normalized source rows are mislabeled as written characters')
  await browser.evaluate(`[...document.querySelectorAll('.glyph-tile.corpus')].find(el => el.querySelector('.tile-reading').textContent === 'Unassigned').click()`)
  await browser.waitFor(`document.querySelector('.corpus-dialog[open] .unassigned-title')`)
  assert(await browser.evaluate(`document.querySelector('.corpus-dialog .save-character').disabled`), 'unassigned source label can be confirmed without a choice')
  await click('[aria-label="Close reviewer"]')
  const group = await browser.evaluate(`document.querySelector('[data-visual-group]')?.dataset.visualGroup ?? null`)
  if (group) {
    await click(`[data-visual-group="${group}"]`)
    await settled()
    assert(queries.some(row => row.visual_group === group), 'shape group filter was not requested')
    await click('.visual-groups button')
    await settled()
  }
  failCandidates = true
  await click('[aria-label="Show character 假"]')
  await browser.waitFor(`document.querySelector('.glyph-grid')?.getAttribute('aria-busy') === 'false' && document.querySelector('.corpus-fault')`, 30000)
  assert(await browser.evaluate(`document.querySelectorAll('.glyph-tile.corpus').length === 0`), 'failed exact query retained family crops')
  failCandidates = false
  await click('.find-count .quiet-link')
  await settled()
  assert(queries.some(row => row.code_point === 'U+5047' && row.scope === 'character'), 'exact selection did not request assigned identity')
  assert(await browser.evaluate(`[...document.querySelectorAll('.tile-reading')].every(el => el.textContent === '假')`), 'exact gallery mixes unassigned or different identities')
  assert(!errors.length, errors.join('\n'))
  console.log('PASS: family union/unassigned/exact, group filter, stale response cleanup, production badges, script distinctions, supplementary zi.tools link')
} catch (error) {
  console.log('Browser state:', await browser.evaluate('document.querySelector("dialog[open]")?.innerText ?? document.body.innerText.slice(0,1600)'))
  throw error
} finally { await browser.close() }
