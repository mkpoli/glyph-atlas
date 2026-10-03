#!/usr/bin/env bun
/**
 * Search by image, in a real browser, against a disposable dataset.
 *
 * The site's image-search routes are answered in the browser (DevTools `Fetch`): the model is the tiny
 * stand-in tools/image-search-fixture.py writes, the runtime the onnxruntime-web WebAssembly in
 * node_modules, and the query a fixed answer, held back when a step needs a search in flight. It checks:
 *
 * - nothing is downloaded before the reader presses Download, and the download is kept;
 * - an image pasted into a text field is the field's, and one pasted onto the page opens;
 * - a search shows the candidate's crops, and sends a vector, never the image;
 * - removing the model while a search runs leaves no results behind.
 *
 * Run through devrun, after `bun run build`:
 *   devrun bun apps/review/tools/image-search-check.mjs
 */
import { join } from 'node:path'
import { mkdirSync, readFileSync } from 'node:fs'
import Browser from './browser.mjs'
import { APP, HERE, boot, options } from './harness.mjs'

const config = options()
const service = await boot(config)
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const fixture = join(config.directory, 'image-search-model')
mkdirSync(fixture, { recursive: true })
const built = Bun.spawnSync([config.python, join(HERE, 'image-search-fixture.py'), fixture])
if (built.exitCode !== 0) throw new Error(`image-search-fixture.py failed: ${built.stderr}`)
const manifest = JSON.parse(readFileSync(join(fixture, 'model.json'), 'utf8'))
const ortDirectory = join(APP, 'node_modules', 'onnxruntime-web')
const ortVersion = JSON.parse(readFileSync(join(ortDirectory, 'package.json'), 'utf8')).version
const wasm = readFileSync(join(ortDirectory, 'dist', 'ort-wasm-simd-threaded.asyncify.wasm'))
const runtime = { version: ortVersion, file: 'ort-wasm-simd-threaded.asyncify.wasm', bytes: wasm.length,
  sha256: new Bun.CryptoHasher('sha256').update(wasm).digest('hex') }
const files = Object.fromEntries(['model', 'classes'].map(name => [name, { path: `models/${manifest.version}/${manifest.files[name].name}`,
  bytes: manifest.files[name].bytes, sha256: manifest.files[name].sha256 }]))
const info = { model: { version: manifest.version, encoder: manifest.encoder, preprocessing: manifest.preprocessing, features: 512, files },
  index: { revision: 'fixture', crops: 1, encoder: manifest.encoder }, ready: true }
// One crop for each answer: a white square drawn as a data URL, filed under 字.
const crop = { id: 'fixture:1', label: '字', origin: 'local', script: 'han', source: 'Fixture', holder: 'Fixture holder',
  image: 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><rect width="40" height="40" fill="white"/><text x="8" y="30" font-size="28">字</text></svg>'), score: 0.9 }
const answer = candidates => ({ revision: 'fixture', similar: [crop], candidates: candidates.map(characters => ({ characters, crops: [crop] })) })

let browser
const requested = [], queries = []
let hold = false, held = null

async function respond(requestId, status, body, type) {
  const bytes = typeof body === 'string' ? Buffer.from(body) : body
  await browser.send('Fetch.fulfillRequest', { requestId, responseCode: status, body: bytes.toString('base64'),
    responseHeaders: [{ name: 'content-type', value: type }, { name: 'content-length', value: String(bytes.length) }] })
}

async function paused({ requestId, request }) {
  const path = new URL(request.url).pathname
  requested.push(path)
  if (path === '/atlas/similar/model') return respond(requestId, 200, JSON.stringify(info), 'application/json')
  if (path === `/atlas/similar/files/runtime/onnxruntime-web-${ortVersion}/runtime.json`) return respond(requestId, 200, JSON.stringify(runtime), 'application/json')
  if (path.endsWith('.wasm')) return respond(requestId, 200, wasm, 'application/wasm')
  if (path.startsWith('/atlas/similar/files/models/')) return respond(requestId, 200, readFileSync(join(fixture, path.split('/').pop())), 'application/octet-stream')
  if (path === '/atlas/similar/query') {
    const body = JSON.parse(request.postData ?? '{}')
    queries.push(body)
    const send = () => respond(requestId, 200, JSON.stringify(answer(body.candidates ?? [])), 'application/json')
    if (hold) { held = send; return }
    return send()
  }
  return respond(requestId, 404, '{"detail":"no such route"}', 'application/json')
}

const count = selector => browser.evaluate(`document.querySelectorAll(${JSON.stringify(selector)}).length`)
// An image file made in the page, pasted onto `target`, an expression for the element.
const paste = target => browser.evaluate(`(async () => {
  const canvas = Object.assign(document.createElement('canvas'), { width: 60, height: 80 })
  const context = canvas.getContext('2d'); context.fillStyle = '#fff'; context.fillRect(0, 0, 60, 80)
  context.fillStyle = '#000'; context.fillRect(20, 10, 20, 60)
  const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'))
  const data = new DataTransfer(); data.items.add(new File([blob], 'glyph.png', { type: 'image/png' }))
  const event = new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true })
  ;(${target}).dispatchEvent(event)
  return event.defaultPrevented
})()`)

try {
  browser = await Browser.launch({ width: 1360, height: 900 })
  browser.listeners.push(message => { if (message.method === 'Fetch.requestPaused') paused(message.params).catch(error => console.error(error)) })
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*/atlas/similar/*' }] })
  await browser.goto(`${service.base}/en`, { waitFor: '!!document.querySelector("button.find-image")', timeout: 90000 })

  await browser.evaluate('document.querySelector("button.find-image").click()')
  await browser.waitFor('!!document.querySelector(".image-search .model-actions .primary")', 30000)
  assert(!requested.some(path => path.startsWith('/atlas/similar/files/models/') || path.endsWith('.wasm')), `files were fetched before Download: ${requested}`)
  const label = await browser.evaluate('document.querySelector(".image-search .model-actions .primary").textContent')
  assert(/Download model \(\d+ MB\)/.test(label), `the button does not name the size: ${label}`)
  await browser.evaluate('document.querySelector(".image-search .model-actions .primary").click()')
  await browser.waitFor('!!document.querySelector(".image-drop")', 60000)
  assert(requested.some(path => path.endsWith('classifier.onnx')) && requested.some(path => path.endsWith('.wasm')), 'Download fetched no model or runtime')
  console.log('PASS nothing downloads before Download, and the model is kept')

  // A paste into a text field is the field's; one onto the page opens the image.
  await browser.evaluate(`document.body.append(Object.assign(document.createElement('textarea'), { id: 'elsewhere' }))`)
  await paste('document.getElementById("elsewhere")')
  await Bun.sleep(600)
  assert(await count('.image-stage') === 0, 'an image pasted into a text field opened in the image search')
  await browser.evaluate(`document.body.append(Object.assign(document.createElement('div'), { id: 'editable', contentEditable: 'true' }))`)
  await paste('document.getElementById("editable")')
  await Bun.sleep(600)
  assert(await count('.image-stage') === 0, 'an image pasted into an editable element opened in the image search')
  assert(await paste('document.body'), 'a paste onto the page was not taken')
  await browser.waitFor('!!document.querySelector(".image-stage .box-editor")', 20000)
  console.log('PASS a paste into a text field is left alone, and one onto the page opens')

  await browser.evaluate('document.querySelector(".image-side .primary").click()')
  await browser.waitFor('document.querySelectorAll(".result-tile").length > 0', 60000)
  const sent = queries.at(-1)
  assert(sent && Object.keys(sent).sort().join() === 'candidates,encoder,vector' && sent.vector.length === 2732, `the query sent ${JSON.stringify(Object.keys(sent ?? {}))}`)
  assert(JSON.stringify(sent.candidates[0]) === '["字"]', `the candidates were ${JSON.stringify(sent.candidates)}`)
  console.log('PASS a search sends a vector and candidates, and shows their crops')

  // Removing the model while a search runs leaves nothing of that search.
  hold = true
  await browser.evaluate('document.querySelector(".image-stage").focus()')
  await browser.key('ArrowRight')
  await browser.evaluate('document.querySelector(".image-side .primary").click()')
  await browser.waitFor('document.querySelector(".image-side .primary")?.textContent.includes("Searching")', 20000)
  const deadline = Date.now() + 20000
  while (!held && Date.now() < deadline) await Bun.sleep(50)
  assert(held, 'the second search sent no query')
  await browser.evaluate(`[...document.querySelectorAll('.model-status button')].find(b => b.textContent.includes('Remove')).click()`)
  await browser.waitFor('!!document.querySelector(".image-search .model-actions .primary")', 20000)
  // The page aborted the search, so the browser may have dropped the request already.
  await held().catch(() => {})
  await Bun.sleep(800)
  assert(await count('.image-results, .result-tile') === 0, 'a search answered after the model was removed and its results came back')
  console.log('PASS removing the model during a search leaves no results')
  console.log('PASS image search')
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
