#!/usr/bin/env bun
/**
 * The inspector's unknown-variant choice, in a real browser, against a disposable dataset.
 *
 * A reader who knows the character but cannot name its shape records the form slot as `unresolved`:
 * the crop keeps its character, the choice shows as held when the crop is opened again, and no form
 * chip is marked current. Screenshots are taken light and dark.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/form-unknown-check.mjs --shots /tmp/atlas-unknown-shots
 */
import { Database } from 'bun:sqlite'
import { join } from 'node:path'
import { mkdirSync } from 'node:fs'
import Browser from './browser.mjs'
import { boot, options, units } from './harness.mjs'

const config = options()
const service = await boot(config)
const shotsAt = process.argv.indexOf('--shots')
const screenshots = shotsAt >= 0 ? process.argv[shotsAt + 1] : '/tmp/atlas-unknown-shots'
mkdirSync(screenshots, { recursive: true })
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const ready = 'document.querySelector("dialog[open] .crop-viewport")?.dataset.ready === "true" && !document.querySelector(".save-character")?.disabled'
const label = () => browser.evaluate('document.querySelector(".save-character").innerText')
const unknown = 'document.querySelector("dialog[open] .form-unknown")'
// The reader's standing form claims for a crop, oldest first, by representation text or slot state.
const claimed = id => {
  const db = new Database(join(config.directory, 'review.sqlite'), { readonly: true })
  try {
    return db.query(`SELECT coalesce(r.value, json_extract(a.value, '$')) AS value
      FROM assertions a LEFT JOIN forms f ON f.id=a.object LEFT JOIN representations r ON r.id=f.anchor
      WHERE a.subject=? AND a.predicate='has_form' AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract')
      ORDER BY a.rowid`).all(id).map(row => row.value)
  } finally { db.close() }
}

try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  await browser.writeAs('form-unknown-check')
  const errors = []
  browser.listeners.push(m => { if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text) })
  const stored = units(config.directory)
  const [id] = Object.entries(stored).filter(([, unit]) => unit.active && unit.unicode === 'U+3042').map(([id]) => id)
  assert(id, 'the fixture has no crop written as あ')
  const open = async () => {
    await browser.goto(`${service.base}/en/crop/${encodeURIComponent(id)}`, { waitFor: ready, timeout: 90000 })
    await browser.waitFor(`!!${unknown}`, 30000)
  }

  await open()
  assert((await label()).includes('Looks right'), 'an untouched crop does not offer Looks right')
  await browser.evaluate(`${unknown}.click()`)
  await browser.waitFor(`${unknown}.getAttribute('aria-pressed') === 'true'`)
  assert((await label()).startsWith('Save'), 'the unknown-variant choice does not turn the button into Save')
  await browser.screenshot(join(screenshots, 'form-unknown-light.png'))
  await browser.setColorScheme('dark')
  await browser.screenshot(join(screenshots, 'form-unknown-dark.png'))
  await browser.setColorScheme('light')
  await browser.evaluate('document.querySelector(".save-character").click()')
  await browser.waitFor('document.querySelector("dialog[open]") === null', 30000)
  assert(JSON.stringify(claimed(id)) === JSON.stringify(['unresolved']), `the unknown variant was not recorded: ${claimed(id)}`)
  assert(units(config.directory)[id].unicode === 'U+3042', 'the unknown variant changed the crop\'s character')

  await open()
  assert(await browser.evaluate(`${unknown}.getAttribute('aria-pressed') === 'true'`), 'the recorded unknown variant is not shown as current')
  assert(await browser.evaluate('document.querySelectorAll("dialog[open] .form-chip.current").length') === 0,
    'a form is shown as current beside the unknown variant')
  assert((await label()).includes('Looks right'), 'a crop whose variant is unknown is not settled')
  await browser.screenshot(join(screenshots, 'form-unknown-saved-light.png'))
  await browser.setColorScheme('dark')
  await browser.screenshot(join(screenshots, 'form-unknown-saved-dark.png'))
  await browser.setColorScheme('light')
  await browser.setViewport(390, 844)
  assert(await browser.evaluate('document.querySelector("dialog").scrollWidth <= innerWidth + 1'), 'the unknown variant overflows a phone')
  await browser.screenshot(join(screenshots, 'form-unknown-phone-light.png'))
  await browser.setColorScheme('dark')
  await browser.screenshot(join(screenshots, 'form-unknown-phone-dark.png'))
  await browser.setColorScheme('light')
  console.log('PASS the unknown variant is recorded as unresolved, and the crop keeps its character')
  assert(!errors.length, 'page errors: ' + errors.join('; '))
  console.log('PASS unknown variant')
} finally {
  await service.stop({ keep: config.keep })
  if (browser) await browser.close()
}
