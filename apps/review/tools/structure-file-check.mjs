#!/usr/bin/env bun
/**
 * Arranging the 構形 of a crop's forms, in a real browser, against a disposable dataset.
 *
 * The inspector's bar sets a crop's forms under their 構形, and a signed-in reader opens them as a
 * board of trays. A form is filed under another 構形 by dragging it to that tray, by picking it up
 * with a click and clicking the tray, or from the keyboard; the claim is the form's (has_structure),
 * so it holds for every crop. A form the reader filed is taken back with its × or by putting it back
 * where its description puts it. A whole tray is moved into another the same ways (folds_into): the
 * two are one 構形, the first stands as a tray within the second, and the reader's fold is split off
 * again. The character page opens the same board.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/structure-file-check.mjs
 */
import { Database } from 'bun:sqlite'
import { join } from 'node:path'
import { mkdirSync } from 'node:fs'
import Browser from './browser.mjs'
import { boot, options, units } from './harness.mjs'

const config = options()
const service = await boot(config)
const shotsAt = process.argv.indexOf('--shots')
const screenshots = shotsAt >= 0 ? process.argv[shotsAt + 1] : '/tmp/atlas-character-shots'
mkdirSync(screenshots, { recursive: true })
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const ready = 'document.querySelector("dialog[open] .crop-viewport")?.dataset.ready === "true" && !document.querySelector(".save-character")?.disabled'
const chips = 'document.querySelectorAll("dialog[open] .crop-form .form-chip")'
const groups = `[...document.querySelectorAll('dialog[open] .form-group')]`
const board = `document.querySelector('.structure-dialog[open]')`
const trays = `[...document.querySelectorAll('.structure-dialog[open] .tray')]`
// The 構形 a written value is filed under now, by label.
const filed = value => {
  const db = new Database(join(config.directory, 'review.sqlite'), { readonly: true })
  try {
    return db.query(`SELECT s.label FROM assertions a JOIN structures s ON s.id=a.object JOIN forms f ON f.id=a.subject JOIN representations r ON r.id=f.anchor
      WHERE a.predicate='has_structure' AND r.value=? AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract')`).all(value).map(row => row.label)
  } finally { db.close() }
}

try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  await browser.writeAs('structure-file-check')
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })
  await fetch(`${service.base}/layers/characters/U%2B6240?expand=none`)
  // A crop written as あ, one with an image to name, is renamed 所: its bar holds several 構形.
  await browser.goto(`${service.base}/en/review`)
  const candidates = Object.entries(units(config.directory)).filter(([, unit]) => unit.active && unit.unicode === 'U+3042').map(([id]) => id)
  const [crop, renamed] = await browser.evaluate(`(async () => {
    for (const id of ${JSON.stringify(candidates)}) {
      const crop = await (await fetch('/atlas/characters/' + encodeURIComponent(id))).json()
      if (!crop.image_sha256) continue
      const response = await fetch('/layers/units/' + encodeURIComponent(id), { method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ id: crypto.randomUUID(), revision: crop.revision, image_sha256: crop.image_sha256, verdict: 'wrong', issue: 'character', character: '所' }) })
      return [id, response.status + ' ' + await response.text()]
    }
    return [null, 'no crop written as あ has an image']
  })()`)
  assert(crop && renamed.startsWith('2'), `no crop could be renamed to 所: ${renamed}`)
  await browser.goto(`${service.base}/en/crop/${encodeURIComponent(crop)}`, { waitFor: ready, timeout: 90000 })
  await browser.waitFor(`${chips}.length >= 10 && !!document.querySelector('dialog[open] .form-arrange')`, 30000)
  const barStanding = () => browser.evaluate(`${groups}.map(g => [g.querySelector('.form-group-label')?.textContent.trim() ?? '', [...g.querySelectorAll('.script-text')].map(s => s.textContent)])`)
  // Each tray with the forms that stand in it, a tray folded into another after that one.
  const standing = () => browser.evaluate(`${trays}.filter(tray => !tray.inert).map(tray => [tray.querySelector('h3 .handle')?.textContent.trim() ?? '', [...tray.querySelectorAll(':scope > .tray-forms > .token:not([inert]) > [data-form]')].map(chip => chip.dataset.form)])`)
  const under = (label, form) => `${trays}.some(tray => !tray.inert && (tray.querySelector('h3 .handle')?.textContent.trim() ?? '') === ${JSON.stringify(label)} && !!tray.querySelector(':scope > .tray-forms > .token:not([inert]) > [data-form="' + CSS.escape(${JSON.stringify(form)}) + '"]')) && !${board}.querySelector('[aria-busy="true"]') && !${board}.querySelector('.token[inert]')`
  const barUnder = (caption, form) => `${groups}.some(g => g.querySelector('.form-group-label')?.textContent.trim() === ${JSON.stringify(caption)} && [...g.querySelectorAll('.script-text')].some(s => s.textContent === ${JSON.stringify(form)}))`
  const chip = form => `${board}.querySelector('.token:not([inert]) > [data-form="' + CSS.escape(${JSON.stringify(form)}) + '"]')`
  const tray = label => `${trays}.find(tray => !tray.inert && (tray.querySelector('h3 .handle')?.textContent.trim() ?? '') === ${JSON.stringify(label)})`
  const handle = label => `${tray(label)}.querySelector('h3 .handle')`
  const centre = expression => browser.evaluate(`(() => { const r = ${expression}.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 } })()`)
  const mouse = (type, at, held = true) => browser.send('Input.dispatchMouseEvent', { type, x: Math.round(at.x), y: Math.round(at.y), button: type === 'mouseMoved' && !held ? 'none' : 'left', buttons: held && type !== 'mouseReleased' ? 1 : 0, clickCount: 1 })
  const open = async () => {
    // A real click: Chrome gives a dialog opened without one the same Escape as the dialog beneath it.
    await browser.evaluate('document.querySelector("dialog[open] .form-arrange").scrollIntoView({ block: "center", behavior: "instant" })')
    const at = await browser.centre('dialog[open] .form-arrange')
    await browser.click(at.x, at.y)
    await browser.waitFor(`!!${board} && ${trays}.length > 1`, 20000)
  }
  const shut = async () => {
    await browser.key('Escape')
    await browser.waitFor(`!document.querySelector('.structure-dialog')`)
    assert(await browser.evaluate('!!document.querySelector("dialog[open] .crop-form")'), 'closing the board closed the inspector')
  }
  const shoot = async name => {
    for (const scheme of ['light', 'dark']) {
      await browser.setColorScheme(scheme)
      await Bun.sleep(400)
      await browser.screenshot(join(screenshots, `${name}-${scheme}.png`))
    }
    await browser.setColorScheme('light')
  }
  const own = '所'
  const bar = await barStanding()
  await open()
  const before = await standing()
  const from = before.find(([, forms]) => forms.includes(own))?.[0], to = before.find(([label, forms]) => label && !forms.includes(own))?.[0]
  assert(from && to, `the board has no second 構形 to file under: ${JSON.stringify(before)}`)
  assert(JSON.stringify(before.filter(([label]) => label).map(([label]) => label)) === JSON.stringify(bar.filter(([caption]) => caption).map(([caption]) => caption)), `the board's trays are not the bar's 構形: ${JSON.stringify([before, bar])}`)
  assert(await browser.evaluate(`document.activeElement === ${chip(own)}`), 'the board does not start on the form the reader came from')
  await shoot('structure-board')

  // The crop's own form is dragged to another 構形: the tray under the pointer shows where it will
  // stand, and the form is filed there when it is let go.
  const start = await centre(chip(own)), end = await centre(tray(to))
  await mouse('mouseMoved', start, false)
  await mouse('mousePressed', start)
  for (let step = 1; step <= 10; step += 1) { await mouse('mouseMoved', { x: start.x + (end.x - start.x) * step / 10, y: start.y + (end.y - start.y) * step / 10 }); await Bun.sleep(20) }
  assert(await browser.evaluate(`${tray(to)}.classList.contains('over') && !!${tray(to)}.querySelector('.ghost') && !${tray(from)}.classList.contains('target')`), 'the tray under a dragged form does not show where it will stand')
  assert(await browser.evaluate(`${trays}.filter(tray => tray.classList.contains('target')).length === ${trays}.filter(tray => tray.querySelector('h3')).length - 1`), 'not every other 構形 offers itself to a dragged form')
  await shoot('structure-drag')
  await mouse('mouseReleased', end)
  await browser.waitFor(under(to, own), 20000)
  assert(JSON.stringify(filed(own)) === JSON.stringify([to]), `${own} was not filed under ${to}: ${filed(own)}`)
  assert(await browser.evaluate(`!!${chip(own)}.parentElement.querySelector('.take') && document.activeElement === ${chip(own)}`), 'a filed form carries no way back, or loses the focus')
  assert(await browser.evaluate(`!${board}.querySelector('[aria-pressed="true"]') && !${board}.querySelector('.tray.target')`), 'the board still holds the form after the drop')
  await shoot('structure-filed')
  await shut()
  await browser.waitFor(barUnder(to, own), 20000)
  console.log(`PASS the crop's form is dragged to ${to} and filed there, on the board and on the bar`)

  // Two other readers file the form elsewhere: it stands there, and the reader's own filing is still
  // theirs to take back.
  const [elsewhere, [holder]] = before.find(([label, forms]) => label && label !== to && !forms.includes(own))
  const say = async (reviewer, body) => {
    const response = await fetch(`${service.base}/atlas/forms/${encodeURIComponent(own)}/structure`, { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ id: crypto.randomUUID(), client_id: reviewer, ...body }) })
    const answer = await response.text()
    assert(response.ok, `${reviewer} could not file ${own}: ${response.status} ${answer}`)
    return JSON.parse(answer)
  }
  let held
  for (const reviewer of ['bob', 'cat']) held = (await say(reviewer, { char: holder })).structures.find(value => value.label === elsewhere).structure
  const address = await browser.evaluate('location.href')
  await browser.goto(address, { waitFor: ready, timeout: 90000 })
  await browser.waitFor(barUnder(elsewhere, own), 30000)
  await open()
  await browser.waitFor(under(elsewhere, own), 20000)
  // A move the two outweigh comes back, and the board says readers disagree.
  await browser.evaluate(`${chip(own)}.parentElement.querySelector('.take').click()`)
  await browser.waitFor(`!${chip(own)}.parentElement.querySelector('.take') && !${board}.querySelector('[aria-busy="true"]')`, 20000)
  await browser.evaluate(`${chip(own)}.click()`)
  await browser.evaluate(`${tray(to)}.click()`)
  await browser.waitFor(`${board}.querySelector('.structure-notice')?.textContent === ${JSON.stringify(`Readers disagree about ${own}`)}`, 20000)
  await browser.waitFor(under(elsewhere, own), 20000)
  assert(JSON.stringify(filed(own).sort()) === JSON.stringify([elsewhere, elsewhere, to].sort()), `the outweighed filing was not saved: ${filed(own)}`)
  await browser.evaluate(`${chip(own)}.parentElement.querySelector('.take').click()`)
  await browser.waitFor(`!${chip(own)}.parentElement.querySelector('.take') && !${board}.querySelector('[aria-busy="true"]')`, 20000)
  assert(JSON.stringify(filed(own)) === JSON.stringify([elsewhere, elsewhere]), `the reader's own filing was not taken back alone: ${filed(own)}`)
  for (const reviewer of ['bob', 'cat']) await say(reviewer, { structure: held, remove: true })
  await browser.goto(address, { waitFor: ready, timeout: 90000 })
  await browser.waitFor(barUnder(from, own), 30000)
  console.log(`PASS a filing another reader's outweighs is still taken back with its ×`)

  // A click on the board's own margin lets a held form go and leaves the board open.
  await open()
  await browser.evaluate(`${chip(own)}.click()`)
  const edge = await browser.evaluate(`(() => { const r = ${board}.getBoundingClientRect(); return { x: r.left + 6, y: r.top + 6 } })()`)
  await browser.click(edge.x, edge.y)
  assert(await browser.evaluate(`!!${board} && !${board}.querySelector('[aria-pressed="true"]')`), 'a click on the board\'s margin closes it, or keeps the form held')
  await shut()

  // The keyboard: Enter picks the form up, the arrows aim at a 構形 without stepping to another crop,
  // Escape lets go without closing the board, and Enter puts the form down.
  await open()
  await browser.evaluate(`${chip(own)}.focus()`)
  await browser.key('Enter')
  await browser.waitFor(`${chip(own)}.getAttribute('aria-pressed') === 'true'`)
  await browser.key('Escape')
  assert(await browser.evaluate(`!!${board} && ${chip(own)}.getAttribute('aria-pressed') === 'false'`), 'Escape with a form held closes the board, or keeps the form held')
  await browser.key('Enter')
  for (let presses = 0; presses < before.length && !(await browser.evaluate(`${tray(to)}.classList.contains('over')`)); presses += 1) await browser.key('ArrowRight')
  assert(await browser.evaluate(`${tray(to)}.classList.contains('over')`), `the arrows never aim at ${to}`)
  assert(await browser.evaluate('location.href') === address, 'an arrow key on the board steps to another crop')
  assert((await browser.evaluate(`${board}.querySelector('[aria-live]').textContent`)).includes(to), 'the 構形 aimed at is not announced')
  await browser.key('Enter')
  await browser.waitFor(under(to, own), 20000)
  assert(JSON.stringify(filed(own)) === JSON.stringify([to]), `${own} was not filed under ${to} from the keyboard: ${filed(own)}`)
  assert(await browser.evaluate(`document.activeElement === ${chip(own)} && ${chip(own)}.getAttribute('aria-pressed') === 'false'`), 'the form put down from the keyboard is held again, or loses the focus')
  console.log(`PASS the form is filed under ${to} from the keyboard`)

  // A held form the reader filed can be let go out of its filing, on the strip at the board's end.
  await browser.evaluate(`${chip(own)}.click()`)
  await browser.waitFor(`${board}.querySelector('.tray.out')?.textContent.trim() === ${JSON.stringify(`Take ${own} out`)}`)
  await browser.evaluate(`${board}.querySelector('.tray.out').click()`)
  await browser.waitFor(under(from, own), 20000)
  assert(filed(own).length === 0 && await browser.evaluate(`!${board}.querySelector('.tray.out')`), `the strip did not take the filing back: ${filed(own)}`)
  await browser.evaluate(`${chip(own)}.click()`)
  assert(await browser.evaluate(`!${board}.querySelector('.tray.out')`), 'a form with no filing of the reader offers a way out')
  await browser.evaluate(`${tray(to)}.click()`)
  await browser.waitFor(under(to, own), 20000)

  // A click picks the form up and a click on a tray puts it down; back where its description puts
  // it, the reader's filing is taken back and the board is as it was.
  await browser.evaluate(`${chip(own)}.click()`)
  await browser.waitFor(`${chip(own)}.getAttribute('aria-pressed') === 'true' && ${tray(from)}.classList.contains('target')`)
  await browser.evaluate(`${tray(from)}.click()`)
  await browser.waitFor(under(from, own), 20000)
  assert(filed(own).length === 0, `the filing was not taken back: ${filed(own)}`)
  assert(JSON.stringify(await standing()) === JSON.stringify(before), 'the board does not return to what the descriptions give')
  await shut()
  assert(JSON.stringify(await barStanding()) === JSON.stringify(bar), 'the bar does not return to what the descriptions give')
  console.log('PASS a form put back where its description puts it has its filing taken back')

  // A description typed in the picker, chosen and not saved, is filed too: it has no 構形 until then.
  const typed = '⿰戸今'
  await browser.evaluate('document.querySelector("dialog[open] .form-add").click()')
  await browser.waitFor('!!document.querySelector("dialog[open] .form-compose")')
  await browser.evaluate('document.querySelector("dialog[open] .form-compose").click()')
  await browser.waitFor('!!document.querySelector("dialog[open] .ids-field input")')
  await browser.evaluate(`(() => { const i = document.querySelector('dialog[open] .ids-field input'); i.focus(); i.value = ${JSON.stringify(typed)}; i.dispatchEvent(new Event('input', { bubbles: true })) })()`)
  await browser.waitFor('!document.querySelector("dialog[open] .ids-use").disabled', 20000)
  await browser.evaluate('document.querySelector("dialog[open] .ids-use").click()')
  await browser.waitFor(`[...${chips}].some(c => c.classList.contains('chosen') && c.textContent.includes(${JSON.stringify(typed)}))`, 20000)
  await open()
  assert(await browser.evaluate(`!!${chip(typed)}`), 'a typed description is not on the board')
  await browser.evaluate(`${chip(typed)}.click()`)
  await browser.evaluate(`${tray(from)}.click()`)
  await browser.waitFor(under(from, typed), 20000)
  assert(JSON.stringify(filed(typed)) === JSON.stringify([from]), `${typed} was not filed under ${from}: ${filed(typed)}`)
  await browser.setViewport(390, 844)
  await Bun.sleep(300)
  assert(await browser.evaluate(`${board}.scrollWidth <= ${board}.clientWidth + 1 && ${board}.getBoundingClientRect().right <= innerWidth`), 'the board overflows on a phone')
  await shoot('structure-board-phone')
  await browser.setViewport(1440, 1000)
  await shut()
  await browser.waitFor(barUnder(from, typed), 20000)
  console.log(`PASS a description typed in the picker is filed under ${from} before it is saved`)

  // A whole 構形 is dragged by its caption into another: the tray under the pointer shows it will
  // stand within, and once let go the two are one 構形 on the board and on the bar.
  const folds = () => {
    const db = new Database(join(config.directory, 'review.sqlite'), { readonly: true })
    try {
      return db.query(`SELECT a.label AS part,b.label AS head FROM current_claims c JOIN structures a ON a.id=c.subject JOIN structures b ON b.id=c.object
        WHERE c.predicate='folds_into' AND c.status='asserted'`).all().map(row => [row.part, row.head])
    } finally { db.close() }
  }
  const within = (part, head) => `!!${tray(part)}?.classList.contains('part') && ${tray(part)}.parentElement === ${tray(head)} && !${board}.querySelector('[aria-busy="true"]') && !${board}.querySelector('.tray[inert]')`
  const apart = part => `!!${tray(part)} && !${tray(part)}.classList.contains('part') && !${board}.querySelector('[aria-busy="true"]') && !${board}.querySelector('.tray[inert]')`
  await open()
  const apartBoard = await standing()
  const grab = await centre(handle(to)), into = await centre(tray(from))
  await mouse('mouseMoved', grab, false)
  await mouse('mousePressed', grab)
  for (let step = 1; step <= 10; step += 1) { await mouse('mouseMoved', { x: grab.x + (into.x - grab.x) * step / 10, y: grab.y + (into.y - grab.y) * step / 10 }); await Bun.sleep(20) }
  assert(await browser.evaluate(`${tray(from)}.classList.contains('over') && ${tray(from)}.querySelector('.ghost.within')?.textContent.trim() === ${JSON.stringify(to)}`), 'the tray under a dragged tray does not show it will stand within')
  await shoot('structure-merge-drag')
  await mouse('mouseReleased', into)
  await browser.waitFor(within(to, from), 20000)
  assert(JSON.stringify(folds()) === JSON.stringify([[to, from]]), `${to} was not folded into ${from}: ${JSON.stringify(folds())}`)
  assert(await browser.evaluate(`document.activeElement === ${handle(to)} && !!${tray(to)}.querySelector('h3 .take')`), 'a folded tray carries no way back, or loses the focus')
  await shoot('structure-merged')
  console.log(`PASS ${to} is dragged into ${from} and stands within it`)

  // A click inside the folded tray a held form stands in lets go: it is not filed under the tray around it.
  const [inside] = before.find(([label]) => label === to)[1]
  await browser.evaluate(`${chip(inside)}.click()`)
  await browser.waitFor(`${chip(inside)}.getAttribute('aria-pressed') === 'true'`)
  await browser.evaluate(`${tray(to)}.querySelector('.tray-forms').click()`)
  await Bun.sleep(300)
  assert(filed(inside).length === 0 && await browser.evaluate(`!${board}.querySelector('[aria-pressed="true"]') && !${board}.querySelector('[aria-busy="true"]')`), `a click inside its own folded tray filed ${inside}: ${filed(inside)}`)

  // A form is filed under the folded 構形 itself, and taken back from it.
  await browser.evaluate(`${chip(own)}.click()`)
  await browser.evaluate(`${tray(to)}.click()`)
  await browser.waitFor(under(to, own), 20000)
  assert(JSON.stringify(filed(own)) === JSON.stringify([to]), `${own} was not filed under the folded ${to}: ${filed(own)}`)
  await browser.evaluate(`${chip(own)}.parentElement.querySelector('.take').click()`)
  await browser.waitFor(under(from, own), 20000)
  assert(filed(own).length === 0, `the filing under a folded 構形 was not taken back: ${filed(own)}`)
  await shut()
  const merged = await barStanding()
  assert(!merged.some(([caption]) => caption === to) && merged.find(([caption]) => caption === from)[1].length > bar.find(([caption]) => caption === from)[1].length, `the bar does not show ${to} under ${from}: ${JSON.stringify(merged)}`)
  console.log(`PASS a form is filed under the folded ${to}, and the bar shows one 構形`)

  // The reader's fold is split off with its ×, made again from the keyboard, and split off on the
  // strip that shows while the folded tray is held.
  await open()
  await browser.waitFor(within(to, from), 20000)
  await browser.evaluate(`${tray(to)}.querySelector('h3 .take').click()`)
  await browser.waitFor(apart(to), 20000)
  assert(folds().length === 0, `the fold was not taken back: ${JSON.stringify(folds())}`)
  await browser.evaluate(`${handle(to)}.focus()`)
  await browser.key('Enter')
  await browser.waitFor(`${handle(to)}.getAttribute('aria-pressed') === 'true'`)
  assert(await browser.evaluate(`!${tray(to)}.classList.contains('target') && !${board}.querySelector('.tray.out')`), 'a held tray offers itself, or a way out it does not have')
  for (let presses = 0; presses < apartBoard.length && !(await browser.evaluate(`${tray(from)}.classList.contains('over')`)); presses += 1) await browser.key('ArrowRight')
  assert((await browser.evaluate(`${board}.querySelector('[aria-live]').textContent`)) === `Merge into ${from}`, 'the 構形 a tray is aimed at is not announced')
  await browser.key('Enter')
  await browser.waitFor(within(to, from), 20000)
  assert(JSON.stringify(folds()) === JSON.stringify([[to, from]]) && await browser.evaluate(`document.activeElement === ${handle(to)}`), `${to} was not folded into ${from} from the keyboard: ${JSON.stringify(folds())}`)
  await browser.evaluate(`${handle(to)}.click()`)
  await browser.waitFor(`${board}.querySelector('.tray.out')?.textContent.trim() === ${JSON.stringify(`Split ${to} off`)}`)
  await browser.evaluate(`${board}.querySelector('.tray.out').click()`)
  await browser.waitFor(apart(to), 20000)
  assert(folds().length === 0, `the strip did not take the fold back: ${JSON.stringify(folds())}`)
  assert(JSON.stringify(await standing()) === JSON.stringify(apartBoard), 'the board does not return to its trays after the split')
  await shut()
  console.log(`PASS the fold is split off with its × and on the strip, and made from the keyboard`)

  // The character's page opens the same board from its 構形 row.
  await browser.goto(`${service.base}/en/character/U+6240`, { waitFor: `!!document.querySelector('.character-layers .arrange')`, timeout: 90000 })
  await browser.evaluate(`document.querySelector('.character-layers .arrange').click()`)
  await browser.waitFor(`!!${board} && ${trays}.length > 0 && !!${chip(own)}`, 20000)
  assert(await browser.evaluate(`!!${chip('数')}`), 'the character page board lacks related 数')
  await shoot('structure-board-character')
  await browser.key('Escape')
  await browser.waitFor(`!document.querySelector('.structure-dialog')`)
  console.log('PASS the character page opens the board')

  assert(!errors.length, 'page errors: ' + errors.join('; '))
  console.log('PASS structure filing')
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
