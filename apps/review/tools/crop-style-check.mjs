#!/usr/bin/env bun
/**
 * A crop's style in the inspector, in a browser on the built app against a stand-in API: pressed
 * buttons are held until the save, a style alone is saved with no review, and a style beside a form or
 * a review is given to the crop as that left it.
 * Run with: bun run build && bun tools/crop-style-check.mjs
 */
import { join } from 'node:path'
import { Browser } from './browser.mjs'

const APP = join(import.meta.dir, '..')
const fail = message => { throw new Error(message) }
const port = () => { const s = Bun.listen({ hostname: '127.0.0.1', port: 0, socket: { data() {} } }); const { port } = s; s.stop(true); return port }
const PIXEL = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs+9AAAAFUlEQVR42mNk+M9Qz0AEYBxVSF+FABJADveWkH6oAAAAAElFTkSuQmCC', 'base64')
let crop, sent = [], refuse = false, slow = false
const fresh = () => ({ id: '2r3g0001', source_id: 'a', origin: 'local', label: '華', char: '華', written_character: '華', script: 'han', code_point: 'U+83EF', grapheme: 'U+83EF',
  state: 'pending', issue: null, revision: 0, image: '/images/crop.png', image_sha256: 'sha', context: true, context_image: '/images/context.png',
  context_box: { x: 0, y: 0, w: 400, h: 400 }, crop_box: { x: 140, y: 60, w: 70, h: 70 }, source_scale: [1, 1], crop_version: 'v', crop_editable: false, dating: {}, source: {},
  grapheme_claim: null, form: null, structure: null, reading: null, word: null, style: 'cursive', style_basis: 'none', style_inherited: 'cursive', style_editable: true,
  style_choices: ['cursive', 'formal', 'mixed', 'printed', 'regular', 'running', 'seal'] })
const api = Bun.serve({ port: 0, hostname: '127.0.0.1', async fetch(req) {
  const { pathname: path } = new URL(req.url)
  if (path.endsWith('.png')) return new Response(PIXEL, { headers: { 'content-type': 'image/png' } })
  if (path.endsWith('/suggestions')) return Response.json({ status: 'ready', candidates: [] })
  if (path.endsWith('/similar')) return Response.json({ similar: [], filed_differently: [] })
  if (path === '/atlas/characters/2r3g0001' && req.method === 'GET') return Response.json(crop)
  if (path.startsWith('/atlas/characters/2r3g0001') && req.method === 'POST') {
    const body = await req.json(); sent.push({ path, body })
    if (refuse && path.endsWith('/style')) return Response.json({ detail: 'Refused.' }, { status: 422 })
    if (path.endsWith('/form') && slow) await Bun.sleep(1200)
    if (path.endsWith('/form')) return Response.json({ form: crop.form })
    if (body.revision !== crop.revision) return Response.json({ detail: 'This character changed.' }, { status: 409 })
    crop.revision++
    if (path.endsWith('/style')) {
      Object.assign(crop, body.style === 'unassessed' ? { style: crop.style_inherited, style_basis: 'none' } : { style: body.style, style_basis: 'unit' })
      return Response.json(crop)
    }
    crop.state = body.verdict === 'match' ? 'checked' : 'flagged'; crop.issue = body.issue ?? null
    return Response.json({ id: body.id, results: [{ id: 1, target_id: crop.id, field: 'review', revision: crop.revision, state: crop.state }] })
  }
  return Response.json({}, { status: 404 })
} })
const at = port(), base = `http://127.0.0.1:${at}`
const web = Bun.spawn(['bunx', 'vite', 'preview', '--port', String(at), '--strictPort', '--host', '127.0.0.1'],
  { cwd: APP, env: { ...process.env, ATLAS_REVIEW_API: `http://127.0.0.1:${api.port}` }, stdout: 'ignore', stderr: 'ignore' })
let browser
try {
  for (const end = Date.now() + 30000; ;) { try { if ((await fetch(base + '/en')).ok) break } catch { /* Not listening yet. */ } if (Date.now() > end) fail('no server'); await Bun.sleep(150) }
  browser = await Browser.launch({ width: 1440, height: 900 })
  await browser.writeAs('reviewer')
  const open = async () => {
    crop = fresh(); sent = []
    await browser.goto(`${base}/en/crop/2r3g0001`, { waitFor: 'document.querySelector(".style-option") && !document.querySelector(".save-character").disabled', timeout: 40000 })
  }
  const state = () => browser.evaluate(`JSON.stringify([...document.querySelectorAll('.style-option')].map(b => b.textContent + (b.getAttribute('aria-pressed') === 'true' ? '*' : '') + (b.classList.contains('inherited') ? '~' : '')))`).then(JSON.parse)
  const press = text => browser.evaluate(`[...document.querySelectorAll('.style-option')].find(b => b.textContent === ${JSON.stringify(text)}).click()`)
  const save = () => browser.evaluate(`document.querySelector('.save-character').click()`)
  const label = () => browser.evaluate(`document.querySelector('.save-character').textContent.trim()`)
  const same = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) fail(`${what}: ${JSON.stringify(a)}, expected ${JSON.stringify(b)}`); console.log('  ok  ', what) }
  const settle = async count => { for (const end = Date.now() + 8000; sent.length < count && Date.now() < end;) await Bun.sleep(50); await Bun.sleep(400) }

  await open()
  same(await state(), ['Regular', 'Running', 'Cursive~', 'Seal'], 'the inherited style is marked, none is pressed')
  await press('Regular')
  same(await state(), ['Regular*', 'Running', 'Cursive~', 'Seal'], 'a pressed style is held')
  same(sent.length, 0, 'nothing is sent before the save')
  same((await label()).startsWith('Save & '), true, 'the save says it saves the change')
  await press('Regular')
  same([await state(), (await label()).startsWith('Looks right')], [['Regular', 'Running', 'Cursive~', 'Seal'], true], 'pressed again it is dropped')
  await press('Regular'); await save(); await settle(1)
  same([sent.map(s => s.path.split('/').at(-1)), sent[0].body.style, crop.state], [['style'], 'regular', 'pending'], 'a style alone is saved with no review')

  await open()
  await browser.evaluate(`window.dispatchEvent(new KeyboardEvent('keydown', { key: '"', code: 'Digit2', shiftKey: true, bubbles: true }))`)
  same(await state(), ['Regular', 'Running*', 'Cursive~', 'Seal'], 'Shift and a digit press a style')
  await browser.evaluate(`document.querySelector('.form-chip')?.click()`); await Bun.sleep(200)
  await save(); await settle(3)
  same([sent.map(s => s.path.split('/').at(-1)), sent[1]?.body.revision, sent[2]?.body, crop.state, crop.style].map(v => v?.style ? [v.style, v.revision] : v),
    [['form', '2r3g0001', 'style'], 0, ['running', 1], 'checked', 'running'], 'a style beside a form is given to the crop as the review left it')

  await open()
  await browser.evaluate(`document.querySelectorAll('.issue-card')[2].click()`); await press('Regular'); await Bun.sleep(200)
  await browser.evaluate(`document.querySelector('.looks-right').click()`); await settle(2)
  same([sent.map(s => s.path.split('/').at(-1)), sent[0]?.body.verdict, crop.style], [['2r3g0001', 'style'], 'match', 'regular'], 'It looks right reviews the crop and keeps the style')

  await open()
  refuse = true
  await browser.evaluate(`document.querySelectorAll('.issue-card')[2].click()`); await press('Seal'); await save(); await settle(2)
  same([sent.map(s => s.path.split('/').at(-1)), crop.state, await state()], [['2r3g0001', 'style'], 'flagged', ['Regular', 'Running', 'Cursive~', 'Seal*']], 'a refused style stays chosen, the review saved')
  refuse = false
  await save(); await settle(3)
  same([sent.slice(2).map(s => s.path.split('/').at(-1)), crop.style], [['style'], 'seal'], 'saving again sends the style alone')

  await open()
  slow = true
  await press('Seal'); await browser.evaluate(`document.querySelector('.form-chip')?.click()`); await Bun.sleep(200)
  await save(); await Bun.sleep(150)
  await browser.evaluate(`document.querySelector('dialog[open]').dispatchEvent(new Event('cancel', { cancelable: true }))`); await settle(2)
  slow = false
  same([sent.map(s => s.path.split('/').at(-1)), crop.style], [['form', 'style'], 'seal'], 'closing while the form is saved keeps the style')

  await open()
  Object.assign(crop, { style: 'regular', style_basis: 'unit' })
  await browser.goto(`${base}/en/crop/2r3g0001?again`, { waitFor: 'document.querySelector(".style-option.chosen") && !document.querySelector(".save-character").disabled', timeout: 40000 })
  same(await state(), ['Regular*', 'Running', 'Cursive~', 'Seal'], 'the crop\'s own style is pressed')
  await press('Regular'); await save(); await settle(1)
  same(sent[0]?.body.style, 'unassessed', 'pressing it takes it back')
} finally { await browser?.close(); web.kill(); api.stop(true) }
