#!/usr/bin/env bun
/**
 * Filing a form under a 構形 from the inspector, in a real browser, against a disposable dataset.
 *
 * The bar sets a crop's forms under their 構形. A signed-in reader opens a 構形's caption and files
 * the selected form there: the chip moves, and the claim is the form's (has_structure), so it holds
 * for every crop. The caption the form then stands under takes the reader's filing back. A form the
 * reader has not chosen for the crop is filed all the same once it is selected. A caption also folds
 * its 構形 into another of the bar (folds_into), so the forms of both stand under that one, and the
 * merged caption splits off what the reader folded into it.
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
const captions = `[...document.querySelectorAll('dialog[open] .caption-menu-trigger')]`
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
  await browser.waitFor(`${chips}.length >= 10 && ${captions}.length > 0`, 30000)
  const standing = () => browser.evaluate(`${groups}.map(g => [(g.querySelector('.caption-menu-trigger') ?? g.querySelector('.form-group-label'))?.textContent.trim() ?? '', [...g.querySelectorAll('.script-text')].map(s => s.textContent)])`)
  const before = await standing()
  const own = '所'
  const from = before.find(([, forms]) => forms.includes(own))?.[0], to = before.find(([caption, forms]) => caption && !forms.includes(own))?.[0]
  assert(from && to, `the bar has no second 構形 to file under: ${JSON.stringify(before)}`)
  const under = (caption, form) => `${groups}.some(g => (g.querySelector('.caption-menu-trigger') ?? g.querySelector('.form-group-label'))?.textContent.trim() === ${JSON.stringify(caption)} && [...g.querySelectorAll('.script-text')].some(s => s.textContent === ${JSON.stringify(form)}))`
  const menu = async (caption, item) => {
    await browser.evaluate(`${captions}.find(b => b.textContent.trim() === ${JSON.stringify(caption)}).click()`)
    await browser.waitFor(`!!document.querySelector('.caption-menu:popover-open')`)
    const items = await browser.evaluate(`[...document.querySelectorAll('.caption-menu:popover-open [role="menuitem"]')].map(b => b.textContent)`)
    assert(items.includes(item), `the caption ${caption} does not offer ${item}: ${JSON.stringify(items)}`)
  }
  // Under the 構形 its description gives, the form has nothing to file or take back.
  await browser.evaluate(`${captions}.find(b => b.textContent.trim() === ${JSON.stringify(from)}).click()`)
  await browser.waitFor(`!!document.querySelector('.caption-menu:popover-open')`)
  const standingItems = await browser.evaluate(`[...document.querySelectorAll('.caption-menu:popover-open [role="menuitem"]')].map(b => b.textContent)`)
  assert(!standingItems.some(item => item.includes(own + ' ')), `the 構形 the form stands under offers to file it: ${JSON.stringify(standingItems)}`)
  // A menu a script opened shares its Escape with the dialog under it, so it is closed here by hand.
  await browser.evaluate(`document.querySelector('.caption-menu:popover-open').hidePopover()`)
  await browser.waitFor(`!document.querySelector('.caption-menu:popover-open')`)

  // The crop's own form is filed under another 構形 and taken back.
  await menu(to, `Place ${own} here`)
  assert(await browser.evaluate(`document.activeElement?.getAttribute('role') === 'menuitem'`), 'the menu does not take the focus')
  await browser.screenshot(join(screenshots, 'structure-menu-light.png'))
  await browser.setColorScheme('dark')
  await Bun.sleep(400)
  await browser.screenshot(join(screenshots, 'structure-menu-dark.png'))
  await browser.setColorScheme('light')
  await browser.key('Enter')
  await browser.waitFor(under(to, own), 20000)
  assert(JSON.stringify(filed(own)) === JSON.stringify([to]), `${own} was not filed under ${to}: ${filed(own)}`)
  await browser.waitFor(`document.activeElement?.classList.contains('form-chip') && document.activeElement.querySelector('.script-text')?.textContent === ${JSON.stringify(own)}`, 5000)
  await browser.screenshot(join(screenshots, 'structure-filed-light.png'))
  await browser.setColorScheme('dark')
  await Bun.sleep(400)
  await browser.screenshot(join(screenshots, 'structure-filed-dark.png'))
  await browser.setColorScheme('light')
  console.log(`PASS the crop's form is filed under ${to} from that 構形's caption`)

  // Escape closes the menu and leaves the inspector open.
  await menu(to, `Take ${own} out`)
  await browser.key('Escape')
  await browser.waitFor(`!document.querySelector('.caption-menu:popover-open')`)
  assert(await browser.evaluate('!!document.querySelector("dialog[open]")'), 'Escape in the menu closed the inspector')
  await menu(to, `Take ${own} out`)
  await browser.key('Enter')
  await browser.waitFor(under(from, own), 20000)
  assert(filed(own).length === 0, `the filing was not taken back: ${filed(own)}`)
  assert(JSON.stringify(await standing()) === JSON.stringify(before), 'the bar does not return to what the descriptions give')
  console.log('PASS the filing is taken back from the caption the form stands under')

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
  await menu(from, `Place ${typed} here`)
  await browser.key('Enter')
  await browser.waitFor(under(from, typed), 20000)
  assert(JSON.stringify(filed(typed)) === JSON.stringify([from]), `${typed} was not filed under ${from}: ${filed(typed)}`)
  await browser.setViewport(390, 844)
  assert(await browser.evaluate('document.querySelector("dialog").scrollWidth <= innerWidth + 1'), 'the form bar overflows on a phone')
  await menu(from, `Take ${typed} out`)
  const box = await browser.evaluate(`(() => { const r = document.querySelector('.caption-menu:popover-open').getBoundingClientRect(); return [r.left, r.right, innerWidth] })()`)
  assert(box[0] >= 0 && box[1] <= box[2], `the menu leaves a phone's screen: ${box}`)
  await browser.screenshot(join(screenshots, 'structure-menu-phone-light.png'))
  await browser.key('Escape')
  console.log(`PASS a description typed in the picker is filed under ${from} before it is saved`)

  // A 構形 is folded into another from its caption: its forms join that group, and the merged caption
  // splits it off again.
  await browser.setViewport(1440, 1000)
  await browser.goto(`${service.base}/en/crop/${encodeURIComponent(crop)}`, { waitFor: ready, timeout: 90000 })
  await browser.waitFor(`${chips}.length >= 10 && ${captions}.length > 0`, 30000)
  const apart = await standing()
  const moving = apart.find(([caption]) => caption === to)[1]
  const pick = async (caption, item) => {
    await menu(caption, item)
    await browser.evaluate(`[...document.querySelectorAll('.caption-menu:popover-open [role="menuitem"]')].find(b => b.textContent === ${JSON.stringify(item)}).click()`)
  }
  const folds = () => {
    const db = new Database(join(config.directory, 'review.sqlite'), { readonly: true })
    try {
      return db.query(`SELECT a.label AS part,b.label AS head FROM current_claims c JOIN structures a ON a.id=c.subject JOIN structures b ON b.id=c.object
        WHERE c.predicate='folds_into' AND c.status='asserted'`).all().map(row => [row.part, row.head])
    } finally { db.close() }
  }
  await menu(to, `Merge into ${from}`)
  await browser.screenshot(join(screenshots, 'structure-merge-menu-light.png'))
  await browser.evaluate(`[...document.querySelectorAll('.caption-menu:popover-open [role="menuitem"]')].find(b => b.textContent === ${JSON.stringify(`Merge into ${from}`)}).click()`)
  await browser.waitFor(`${under(from, moving[0])} && !${groups}.some(g => (g.querySelector('.caption-menu-trigger') ?? g.querySelector('.form-group-label'))?.textContent.trim() === ${JSON.stringify(to)})`, 20000)
  assert(JSON.stringify(folds()) === JSON.stringify([[to, from]]), `${to} was not folded into ${from}: ${JSON.stringify(folds())}`)
  await browser.screenshot(join(screenshots, 'structure-merged-light.png'))
  await browser.setColorScheme('dark')
  await Bun.sleep(400)
  await browser.screenshot(join(screenshots, 'structure-merged-dark.png'))
  await browser.setColorScheme('light')
  console.log(`PASS ${to} is folded into ${from} and its forms join that group`)

  await pick(from, `Split ${to} off`)
  await browser.waitFor(under(to, moving[0]), 20000)
  assert(folds().length === 0, `the fold was not taken back: ${JSON.stringify(folds())}`)
  assert(JSON.stringify(await standing()) === JSON.stringify(apart), 'the bar does not return to its groups after the split')
  console.log(`PASS ${to} is split off again from the merged caption`)

  assert(!errors.length, 'page errors: ' + errors.join('; '))
  console.log('PASS structure filing')
} finally {
  await browser?.close()
  await service.stop({ keep: config.keep })
}
