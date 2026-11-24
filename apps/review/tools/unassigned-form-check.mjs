#!/usr/bin/env bun
// The form bar of a corpus crop nobody has assigned a character, in a real browser against a running
// site: what it offers, and what each choice would save. Reads go to the site, and no write does: every
// request but a GET is answered here.
//
//   bun apps/review/tools/unassigned-form-check.mjs http://127.0.0.1:4173 [--shots DIR]
import { join } from 'node:path'
import { mkdirSync } from 'node:fs'
import Browser from './browser.mjs'

const base = process.argv[2] ?? 'http://127.0.0.1:4173', at = process.argv.indexOf('--shots'), shots = at > 0 ? process.argv[at + 1] : null
// 図 of CODH's 春色梅児与美: its transcription names the grapheme and no character.
const ID = '4rz0007x'
// One whose grapheme's head has registered figures.
const FIGURED = '5jg0003c'
// One of a grapheme with more forms than a bar shows at once.
const LARGE = '3ed0003a'
const assert = (test, message) => { if (!test) throw new Error(message) }
const browser = await Browser.launch({ width: 1440, height: 1100 })
const writes = [], errors = []
let fail = null, unknown = false
browser.listeners.push(event => {
  if (event.method === 'Runtime.exceptionThrown') errors.push(event.params.exceptionDetails.exception?.description ?? event.params.exceptionDetails.text)
  if (event.method !== 'Fetch.requestPaused') return
  const { requestId, request } = event.params, path = decodeURIComponent(new URL(request.url).pathname)
  // With `unknown`, a crop's record names no grapheme.
  if (unknown && /^\/atlas\/characters\/[^/]+$/.test(path)) return void fetch(request.url).then(r => r.json()).then(({ grapheme, ...record }) => browser.send('Fetch.fulfillRequest', { requestId, responseCode: 200,
    responseHeaders: [{ name: 'Content-Type', value: 'application/json' }], body: Buffer.from(JSON.stringify({ ...record, family_members: ['図', '圖'] })).toString('base64') })).catch(error => errors.push(error.message))
  if (request.method === 'GET') return void browser.send('Fetch.continueRequest', { requestId }).catch(() => {})
  const answer = (value, code = 200) => browser.send('Fetch.fulfillRequest', { requestId, responseCode: code, responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
    body: Buffer.from(JSON.stringify(value)).toString('base64') }).catch(() => {})
  const body = (() => { try { return JSON.parse(request.postData ?? '{}') } catch { return {} } })()
  if (!/^\/(layers\/units|atlas\/characters)\//.test(path)) return void answer({})
  writes.push({ path, body })
  if (fail && path.endsWith(fail)) { fail = null; return void answer({ detail: 'refused by the check' }, 500) }
  answer(path.endsWith('/form') ? { id: ID, form: { status: 'asserted', values: [{ form: 'fm', text: body.form }] }, replaced: null }
    : path.endsWith('/structure') ? { id: ID, structure: null, form: null } : { id: body.id, results: [{ target_id: ID, revision: 1, state: 'checked' }] })
})
const dialog = 'document.querySelector("dialog.character-dialog[open]")'
const chips = `[...${dialog}.querySelectorAll('.crop-form .form-chip')]`
const evaluate = expression => browser.evaluate(expression)
const wait = ms => new Promise(done => setTimeout(done, ms))
const open = async (path = '/ja/crop/' + ID) => {
  writes.length = 0
  await browser.goto(base + path, { waitFor: `${dialog}?.querySelector('.save-character')`, timeout: 60000 })
  await browser.waitFor(`${chips}.length > 3 && ${dialog}.querySelector('.crop-viewport')?.dataset.ready === 'true'`, 60000)
  await wait(500)
}
const offered = () => evaluate(`${chips}.map(c => c.title)`)
const press = title => evaluate(`${chips}.find(c => c.title === ${JSON.stringify(title)}).click()`)
const state = () => evaluate(`(() => { const s = ${dialog}.querySelector('.save-character'); return { disabled: s.disabled, text: s.innerText.trim(), pressed: ${chips}.filter(c => c.getAttribute('aria-pressed') === 'true').map(c => c.title),
  issues: [...${dialog}.querySelectorAll('[aria-pressed=true]')].filter(e => !e.closest('.crop-form, .similar-crops')).map(e => e.innerText.trim().split('\\n')[0]), keys: ${chips}.map(c => c.querySelector('kbd')?.textContent ?? '').join('') } })()`)
const save = async () => { await evaluate(`${dialog}.querySelector('.save-character').click()`); await wait(300); await browser.waitFor(`!${dialog} || !${dialog}.querySelector('.save-character').disabled`, 30000); return writes.map(({ path, body }) => [path.replace(/^.*\/(units|characters)\//, '$1/'), body.character ?? null, body.issue ?? null, body.form ?? null, body.id, body.crop_version ?? null]) }
const shot = async name => { if (!shots) return; mkdirSync(shots, { recursive: true }); for (const scheme of ['light', 'dark']) { await browser.setColorScheme(scheme); await wait(700); await browser.screenshot(join(shots, `${name}-${scheme}.png`)) } await browser.setColorScheme('light') }

try {
  await browser.send('Runtime.enable').catch(() => {})
  await browser.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1100, deviceScaleFactor: 2, mobile: false })
  await browser.writeAs('unassigned-form-check')
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] })
  await open()
  const titles = await offered(), first = await state()
  console.log('offered', JSON.stringify(titles))
  assert(['図', '圖', '图', '⿴囗⿱口㐭 H', '啚'].every(title => titles.includes(title)), `the bar does not hold the whole grapheme: ${titles.join(' ')}`)
  assert(!first.pressed.length && first.disabled, 'a form is shown as the crop\'s own, or the crop can be saved untouched')
  assert(first.keys.startsWith('1234567890'.slice(0, Math.min(10, titles.length))), `the number keys do not follow the chips: ${first.keys}`)
  await shot('after')

  // A character: a correction to it, as any character of the bar is.
  await press('圖')
  const named = await state()
  assert(!named.disabled && named.pressed.join() === '圖' && named.issues.length, `choosing a character is no correction: ${JSON.stringify(named)}`)
  await shot('after-chosen')
  await press('圖')
  assert((await state()).disabled, 'choosing the chosen chip again does not drop it')
  await browser.key(String(titles.indexOf('圖') + 1))
  assert((await state()).pressed.join() === '圖', 'the number key does not choose its chip')
  const character = await save()
  assert(character.length === 1 && character[0][0] === 'units/' + ID && character[0][1] === '圖' && character[0][2] === 'character' && character[0][3] === null, JSON.stringify(character))
  console.log('PASS a character is assigned by a correction', JSON.stringify(character))

  // A figure and a regional shape of another member than the head: the member each is a form of is assigned
  // and the form claimed, in one save; one refused is sent again as it was.
  for (const [id, kind, of] of [[ID, /^(⿴囗⿱口㐭) H$/u, '圖'], [FIGURED, /^(.[\u{E0100}-\u{E01EF}]) /u, null]]) {
    await open('/ja/crop/' + id)
    const listed = await offered(), index = listed.findIndex(title => kind.test(title))
    assert(index >= 0, `no such form on ${id}: ${listed.join(' ')}`)
    const form = listed[index].match(kind)[1], base = of ?? [...form][0], other = listed.find(title => [...title].length === 1 && title !== base)
    await press(listed[index])
    const chosen = await state()
    assert(chosen.pressed.join() === listed[index] && chosen.issues.length && !chosen.disabled, JSON.stringify(chosen))
    assert(await evaluate(`${dialog}.innerText.includes(${JSON.stringify(base)})`), 'the character assigned is not shown')
    await shot('after-form-' + id)
    fail = ID === id ? '/' + ID : null
    const tried = await save(), sent = fail === null && id === ID ? await save() : tried
    assert(sent.length === (id === ID ? 2 : 1) && new Set(sent.map(one => one[4])).size === 1, JSON.stringify(sent))
    assert(sent.every(one => one[0] === 'units/' + id && one[1] === base && one[2] === 'character' && one[3] === form && one[5]?.startsWith(id + '@')), JSON.stringify(sent))
    console.log(`PASS ${form} assigns ${base} and is claimed in the same save`)
    // Another character chosen instead leaves the form behind, and it does not come back with its own.
    await open('/ja/crop/' + id); await press(listed[index]); await press(other)
    const instead = await save()
    assert(instead.length === 1 && instead[0][1] === other && instead[0][3] === null, JSON.stringify(instead))
    await open('/ja/crop/' + id); await press(listed[index]); await press(other); await press(base)
    const bare = await save()
    assert(bare.length === 1 && bare[0][1] === base && bare[0][3] === null, JSON.stringify(bare))
  }

  // A variant outside the grapheme is claimed alone: the crop stays in it, with no character.
  await open()
  await press('啚')
  const outside = await state()
  assert(!outside.disabled && !outside.issues.length, JSON.stringify(outside))
  const kept = await save()
  assert(kept.length === 1 && kept[0][0] === `characters/${ID}/form` && kept[0][1] === null && kept[0][3] === '啚', JSON.stringify(kept))
  console.log('PASS a variant outside the grapheme is claimed alone')

  // A description no source gives to a character: the claim alone.
  await open()
  await evaluate(`${dialog}.querySelector('.form-add').click()`)
  await browser.waitFor(`${dialog}.querySelector('.form-compose')`)
  await shot('after-picker')
  const described = await evaluate(`(() => { const o = [...${dialog}.querySelectorAll('.form-picker .form-option')].find(o => !o.classList.contains('form-structure') && o.title.startsWith('⿴')); o.click(); return o.title.split('\\n')[0] })()`)
  await browser.waitFor(`${chips}.some(c => c.getAttribute('aria-pressed') === 'true')`, 15000)
  const drawn = await state()
  assert(!drawn.disabled && !drawn.issues.length, JSON.stringify(drawn))
  const claimed = await save()
  assert(claimed.length === 1 && claimed[0][0] === `characters/${ID}/form` && claimed[0][3] === described, JSON.stringify(claimed))
  console.log(`PASS ${described} is claimed alone`)

  // The 構形 alone, from the picker: the same.
  await open()
  await evaluate(`${dialog}.querySelector('.form-add').click()`)
  await browser.waitFor(`${dialog}.querySelector('.form-structure')`)
  await evaluate(`${dialog}.querySelector('.form-structure').click()`)
  const built = await save()
  assert(built.length === 1 && built[0][0] === `characters/${ID}/structure`, JSON.stringify(built))
  console.log('PASS a 構形 is claimed alone')

  // A grapheme of many forms: the 構形 of the characters its source names stand first, and the rest fold away.
  await open('/ja/crop/' + LARGE)
  const few = await offered(), more = `${dialog}.querySelector('.form-more')`
  assert(await evaluate(`Boolean(${more})`) && few.length <= 45, `a bar of ${few.length} is not folded`)
  await shot('after-large')
  await evaluate(`${more}.click()`)
  await wait(400)
  const all = await offered()
  assert(all.length > few.length && few.every(title => all.includes(title)), `${few.length} → ${all.length}`)
  await shot('after-large-all')
  console.log(`PASS a bar of ${all.length} shows ${few.length} and folds the rest away`)

  // From a gallery's tile, and a crop whose grapheme is not known: no bar.
  for (const without of [false, true]) {
    unknown = without
    await browser.goto(base + '/ja/grapheme/U+56F3?style=unassessed', { timeout: 60000 })
    for (let i = 0; i < 20 && !await evaluate(`Boolean(document.querySelector('.glyph-tile .tile-glyph.unassigned'))`); i++) { await evaluate('window.scrollTo(0, document.body.scrollHeight)'); await wait(1500) }
    await evaluate(`document.querySelector('.glyph-tile .tile-glyph.unassigned').closest('.glyph-tile').click()`)
    await browser.waitFor(`${dialog}?.querySelector('.unassigned-title') && ${dialog}.querySelector('.crop-viewport')?.dataset.ready === 'true'`, 60000)
    await wait(6000)
    assert(await evaluate(`${dialog}.querySelectorAll('.form-chip').length`) > 3 !== without, without ? 'a crop of no known grapheme has a form bar' : 'a crop opened from its tile has no form bar')
    assert((await state()).disabled, 'an untouched crop can be saved')
  }
  console.log('PASS the bar is there from a tile, and not for a crop of no known grapheme')
  assert(!errors.length, errors.join('\n'))
} catch (error) {
  console.log('state', JSON.stringify(await state().catch(() => null)), await evaluate(`${dialog}?.innerText.slice(0, 900)`).catch(() => null))
  throw error
} finally { await browser.close() }
