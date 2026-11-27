#!/usr/bin/env bun
/**
 * Quick Review's selection bar offers the forms the inspector offers a crop of the round's grapheme, in a
 * real browser against a disposable dataset: tiles are selected, the bar's chips are read, and they are
 * compared with the members and variants of the grapheme's card, which the inspector offers. The fixture holds only kana, whose forms are
 * their grapheme's members, so `--as 対` deals its crops as crops of that character instead, whose
 * forms the service reads from the reference tables (its variants among them).
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/quiz-bar-forms-check.mjs [--shots dir]
 */
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'

const config = options()
const argv = process.argv.slice(2)
const shots = argv.includes('--shots') ? argv[argv.indexOf('--shots') + 1] : null
if (shots) mkdirSync(shots, { recursive: true })
const as = argv.includes('--as') ? argv[argv.indexOf('--as') + 1] : null
const grapheme = as ? `U+${as.codePointAt(0).toString(16).toUpperCase()}` : 'U+3042'

const service = await boot(config)
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
// A chip's form as it reads, without its number key or proposal mark.
const chipsIn = selector => `[...document.querySelectorAll(${JSON.stringify(selector + ' .form-chip')})].map(chip => { const copy = chip.cloneNode(true); copy.querySelectorAll('kbd, sup').forEach(node => node.remove()); return copy.textContent.trim() })`

try {
  browser = await Browser.launch({ width: 1280, height: 1000 })
  await browser.writeAs('quiz-bar-forms-check')
  await browser.send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 1000, deviceScaleFactor: 2, mobile: false })
  if (as) {
    // The round of `as` is dealt the fixture's あ crops, written as `as`.
    await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*/atlas?*', requestStage: 'Request' }] })
    browser.listeners.push(async m => {
      if (m.method !== 'Fetch.requestPaused') return
      const { requestId, request } = m.params
      const url = new URL(request.url)
      if (url.searchParams.get('purpose') === 'review' && url.searchParams.get('grapheme') === grapheme) {
        url.searchParams.set('grapheme', 'U+3042')
        const payload = await (await fetch(url)).json()
        const items = (payload.items ?? []).map(item => ({ ...item, label: as, written_character: as, char: as }))
        const body = { ...payload, grapheme: { ...(payload.grapheme ?? {}), key: grapheme, members: [as] }, items }
        await browser.send('Fetch.fulfillRequest', { requestId, responseCode: 200, responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
          body: Buffer.from(JSON.stringify(body)).toString('base64') })
        return
      }
      await browser.send('Fetch.continueRequest', { requestId }).catch(() => {})
    })
  }
  await browser.goto(`${service.base}/en/review/${grapheme}`, { waitFor: 'document.querySelectorAll(".quiz-tile .quiz-choice:not([disabled])").length > 1', timeout: 30000 })
  await browser.evaluate('document.querySelectorAll(".quiz-tile .quiz-choice:not([disabled])")[0].click()')
  await browser.evaluate('document.querySelectorAll(".quiz-tile .quiz-choice:not([disabled])")[1].click()')
  await browser.waitFor('document.querySelector(".form-bar .form-chip") !== null')
  // The bar's forms settle once the grapheme's are read.
  await browser.waitFor('document.querySelectorAll(".form-bar .form-chip").length > 1', 20000).catch(() => {})
  const bar = await browser.evaluate(chipsIn('.form-bar'))
  if (shots) {
    await browser.evaluate('document.querySelector(".form-bar").scrollIntoView({ block: "center" })')
    for (const theme of ['light', 'dark']) {
      await browser.setColorScheme(theme)
      await Bun.sleep(200)
      await browser.screenshot(join(shots, `bar-${theme}.png`))
    }
    await browser.setColorScheme('light')
  }
  // What the inspector offers a crop of the grapheme: its members and the variants of its card.
  const card = await browser.evaluate(`fetch('/layers/graphemes/${grapheme}/forms').then(r => r.json())`)
  const expected = [...new Set([...card.members.map(m => m.char), ...(card.variants ?? []).map(v => v.char)])]
  console.log('bar ', bar.join(' '))
  console.log('card', expected.join(' '))
  const missing = expected.filter(char => !bar.includes(char))
  assert(!missing.length, `the bar lacks forms of the grapheme: ${missing.join(' ')}`)
  console.log(`ok   the bar offers all ${expected.length} members and variants of ${grapheme}`)

  if (as) {
    // A number key marks the selection as the bar's form at its place. A member of the grapheme names the crops'
    // character; a variant outside it is claimed on them and leaves their character as it is.
    await browser.evaluate(`(() => { window.posts = []; const send = window.fetch; window.fetch = (input, init = {}) => { if (init.method === 'POST') posts.push([String(input), JSON.parse(init.body)]); return send(input, init) } })()`)
    const chips = await browser.evaluate(`[...document.querySelectorAll('.form-bar .form-chip')].map(chip => { const copy = chip.cloneNode(true); copy.querySelectorAll('kbd, sup').forEach(node => node.remove()); return copy.textContent.trim() })`)
    const press = async (key, tiles) => {
      await browser.evaluate('posts.length = 0')
      await browser.evaluate(`dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))`)
      await browser.waitFor('document.querySelectorAll(".quiz-tile.selected").length === 0', 5000).catch(() => {})
      for (const n of tiles) await browser.evaluate(`document.querySelectorAll('.quiz-tile .quiz-choice:not([disabled])')[${n}]?.click()`)
      await browser.evaluate(`document.activeElement?.blur(); dispatchEvent(new KeyboardEvent('keydown', { key: '${key}', bubbles: true }))`)
      await browser.waitFor('posts.length > 0', 15000).catch(() => {})
      await Bun.sleep(1500)
      return browser.evaluate('posts')
    }
    const member = card.members.map(m => m.char).find(char => char !== as)
    const memberKey = String(chips.indexOf(member) + 1)
    const outside = (card.variants ?? []).map(v => v.char).find(char => chips.indexOf(char) >= 0 && chips.indexOf(char) < 10 && !card.members.some(m => m.char === char))
    const outsideKey = String((chips.indexOf(outside) + 1) % 10)
    const onMember = await press(memberKey, [0, 1])
    const named = onMember.filter(([url]) => url.includes('/atlas/corrections')).map(([, body]) => body.character)
    assert(named.length && named.every(char => char === member), `key ${memberKey} should name ${member}: ${JSON.stringify(onMember)}`)
    console.log(`ok   key ${memberKey} marks the selection as the member ${member}`)
    if (outside) {
      const onOutside = await press(outsideKey, [0, 1])
      const claimed = onOutside.filter(([url]) => url.includes('/atlas/forms')).flatMap(([, body]) => body.crops.map(crop => crop.form))
      const renamed = onOutside.filter(([url]) => url.includes('/atlas/corrections')).map(([, body]) => body.character)
      assert(claimed.length && claimed.every(form => form === outside), `key ${outsideKey} should claim ${outside}: ${JSON.stringify(onOutside)}`)
      assert(!renamed.includes(outside), `key ${outsideKey} should leave the crops' character: ${JSON.stringify(onOutside)}`)
      console.log(`ok   key ${outsideKey} claims the variant ${outside} and keeps the crops' character`)
    }
  }
} finally {
  await browser?.close()
  await service.stop?.()
}
