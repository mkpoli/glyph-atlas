#!/usr/bin/env bun
// Open the IDS picker in each selection bar, edit only the second 率 in a
// compound, and save a registered figure as the crop's written form.
import assert from 'node:assert/strict'
import { Database } from 'bun:sqlite'
import { join } from 'node:path'
import Browser from './browser.mjs'
import { boot, options, units } from './harness.mjs'

const config = options()
const service = await boot(config)
const database = new Database(join(config.directory, 'review.sqlite'), { readonly: true })
const browser = await Browser.launch({ width: 1280, height: 1000 })
const variants = ['⿱亠⿱⿲冫幺⿱丿丶十', '⿱亠⿱⿲𰀪幺⺀十', '率󠄀', '率󠄁']
async function click(selector) {
  const target = `document.querySelector(${JSON.stringify(selector)})`
  await browser.waitFor(`!!${target} && !${target}.disabled`, 30000)
  await browser.evaluate(`${target}.click()`)
}
const field = 'document.querySelector(".ids-field input")'
const type = text => browser.evaluate(`(() => { const input = ${field}; input.value = ${JSON.stringify(text)}; input.dispatchEvent(new Event('input', { bubbles: true })) })()`)

try {
  await browser.writeAs('ids-selection-check', { anonymous: false })
  // The local fixture implements individual form claims. Fulfill the Worker's
  // batch endpoint through that fixture, so the saved value can be read from its ledger.
  await browser.send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => {
    const send = window.fetch
    window.fetch = async (input, init) => {
      if (input !== '/atlas/forms' || init?.method !== 'POST') return send(input, init)
      const { crops } = JSON.parse(init.body)
      const saved = []
      for (const crop of crops) {
        const response = await send('/atlas/characters/' + encodeURIComponent(crop.id) + '/form', {
          ...init, body: JSON.stringify({ id: crypto.randomUUID(), form: crop.form, crop_version: crop.crop_version })
        })
        if (!response.ok) return response
        saved.push(await response.json())
      }
      return Response.json({ crops: saved })
    }
  })()` })
  const cases = [['collection', variants[3]], ['round', variants[2]], ['collection', '⿰率' + variants[0]], ['round', '⿰率' + variants[1]]]
  for (const [mode, spelling] of cases) {
    const collection = mode === 'collection'
    const tile = collection ? '.glyph-tile[data-unit]' : '.quiz-tile[data-unit]'
    await browser.goto(service.base + (collection ? '/en/' : '/en/review'), {
      waitFor: `!!document.querySelector(${JSON.stringify(tile)})`, timeout: 60000,
    })
    if (!collection) await browser.waitFor('[...document.querySelectorAll(".quiz-choice")].slice(0, 2).filter(button => !button.disabled).length === 2', 30000)
    const ids = await browser.evaluate(`[...document.querySelectorAll(${JSON.stringify(tile)})].slice(0, 2).map(tile => tile.dataset.unit)`)
    assert.equal(ids.length, 2)
    const before = units(config.directory)
    if (collection) await click('.select-toggle')
    await browser.evaluate(`[...document.querySelectorAll(${JSON.stringify(collection ? tile : '.quiz-choice')})].slice(0, 2).forEach(button => button.click())`)
    await browser.waitFor('!!document.querySelector(".ids-open")')
    await click('.ids-open')
    await browser.waitFor(`!!${field}`)
    await type('⿰率率')
    await click('.ids-tree [data-path="1"]')
    await browser.waitFor('document.querySelectorAll(".ids-rate-option svg").length === 4')
    for (const [index, spelling] of variants.entries()) {
      await browser.evaluate(`document.querySelectorAll(".ids-rate-option")[${index}].click()`)
      await browser.waitFor(`${field}?.value === ${JSON.stringify('⿰率' + spelling)}`)
    }
    if (collection) {
      await click('.ids-use')
      await click('.ids-open')
      await browser.waitFor(`${field}?.value === ${JSON.stringify('⿰率' + variants[3])}`)
    }
    await type(spelling)
    await browser.waitFor('!document.querySelector(".ids-use")?.disabled')
    await click('.ids-use')
    if (collection) await click('.bulk-assign > button')
    await browser.waitFor(collection ? '!!document.querySelector(".bulk-done, .bulk-error")' : '!!document.querySelector(".form-done, .form-error")', 30000)
    const error = await browser.evaluate('document.querySelector(".bulk-error, .form-error")?.textContent')
    assert.ok(!error, mode + ': ' + error)
    const records = units(config.directory)
    for (const id of ids) {
      const claim = database.query(`SELECT r.value FROM assertions a JOIN forms f ON f.id = a.object JOIN representations r ON r.id = f.anchor
        WHERE a.subject = ? AND a.predicate = 'has_form' AND r.value = ? AND NOT EXISTS
          (SELECT 1 FROM assertion_actions x WHERE x.assertion = a.id AND x.action = 'retract')`).get(id, spelling)
      assert.ok(claim, mode + ': missing saved form ' + spelling)
      assert.equal(records[id].unicode, before[id].unicode, mode + ': saving the form relabeled the crop')
    }
    console.log('PASS ' + mode + ' selection saves its exact figure and retains the crop character')
  }
} finally {
  await browser.close()
  database.close()
  await service.stop()
}
