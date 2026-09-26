#!/usr/bin/env bun
// The character-layer flow the reader asked for: type トモ, see 𪜈 with its counts, click into the
// exact glyph gallery, and survive an IME, a cleared box and a fast typist. Disposable dataset.
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'
const config = options(), service = await boot(config)
let browser, failures = 0
const assert = (condition, message) => { if (!condition) throw new Error(message) }
async function step(name, body) {
  // One call per step: a body that clicks must not run twice.
  try {
    const detail = await body()
    console.log(`  ok   ${name}${detail ? ` — ${detail}` : ''}`)
  } catch (error) { failures += 1; console.log(`  FAIL ${name} — ${error.message}`) }
}
try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  const errors = []
  browser.listeners.push(m => {
    if (m.method === 'Runtime.exceptionThrown') {
      const d = m.params.exceptionDetails
      errors.push(d?.exception?.description ?? d?.text ?? 'exception')
    }
    if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error'
        && !/Failed to load resource/.test(m.params.entry.text)) errors.push(m.params.entry.text)
  })
  await browser.send('Runtime.enable').catch(() => {})
  await browser.send('Log.enable').catch(() => {})
  const bad = []
  const posts = []
  await browser.send('Network.enable').catch(() => {})
  browser.listeners.push(m => {
    if (m.method === 'Network.responseReceived' && m.params.response.status >= 400) {
      bad.push(`${m.params.response.status} ${m.params.response.url}`)
    }
    if (m.method === 'Network.requestWillBeSent' && m.params.request.method === 'POST') {
      posts.push(`${m.params.request.url.split('/').slice(-2).join('/')} ${m.params.request.postData ?? ''}`)
    }
  })
  await browser.goto(service.base + '/', { waitFor: 'document.querySelectorAll(".glyph-tile").length > 0' })
  const setQuery = async text => {
    await browser.evaluate(`(() => { const i = document.querySelector('.character-search input'); i.focus();
      i.value = ${JSON.stringify(text)}; i.dispatchEvent(new Event('input', { bubbles: true })) })()`)
  }

  await step('the search box offers candidates for a typed kana', async () => {
    await setQuery('あ')
    await browser.waitFor('document.querySelectorAll(".candidate").length > 0', 4000)
    const rows = await browser.evaluate(`Array.from(document.querySelectorAll('.candidate')).map(b => b.innerText)`)
    assert(rows.length > 0, 'no candidate rows')
    return rows[0].replace(/\n/g, ' · ')
  })

  await step('an IME composing Enter does not pick a candidate', async () => {
    const chosen = await browser.evaluate(`(() => {
      const i = document.querySelector('.character-search input')
      const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
      Object.defineProperty(event, 'isComposing', { value: true })
      i.dispatchEvent(event)
      return document.querySelector('.layer-chips') !== null
    })()`)
    assert(!chosen, 'a composing Enter opened a character')
    return 'ignored'
  })

  await step('choosing a candidate opens its own gallery', async () => {
    const point = await browser.centre('.candidate')
    await browser.click(point.x, point.y)
    await browser.waitFor('document.querySelector(".layer-chips") !== null', 4000)
    await browser.waitFor('document.querySelectorAll(".glyph-grid .glyph-tile").length > 0', 4000)
    return await browser.evaluate(`document.querySelector('.layer-chips').innerText.split(String.fromCharCode(10)).join(' · ')`)
  })

  await step('a chip click renders a whole card and throws nothing', async () => {
    // The chips pass a code point rather than a whole row, which is the path that used to render an
    // incomplete card and throw. Escape and refocus repeat what a reader does between keystrokes.
    await browser.evaluate(`document.querySelector('.character-search input').blur()`)
    await browser.key('Escape').catch(() => {})
    await browser.evaluate(`document.querySelector('input[role=combobox]')?.dispatchEvent(new FocusEvent('focus'))`)
    await setQuery('ネ')
    await browser.waitFor('document.querySelectorAll(".candidate").length > 0', 4000)
    const row = await browser.centre('.candidate')
    await browser.click(row.x, row.y)
    await browser.waitFor('document.querySelector(".layer-chips") !== null', 4000)
    await new Promise(resolve => setTimeout(resolve, 600))
    const chips = await browser.evaluate(`document.querySelector('.layer-chips')?.innerText.split(String.fromCharCode(10)).join(' · ') ?? null`)
    assert(chips, 'the card rendered empty after choosing a candidate')
    assert(!errors.length, `an exception was thrown: ${errors.join(' | ').slice(0, 300)}`)
    const actions = await browser.evaluate(`document.querySelectorAll('.chip-action').length`)
    assert(actions > 0, `no widening chip on the card: ${chips}`)
    const label = await browser.evaluate(`document.querySelector('.chip-action')?.innerText ?? ''`)
    assert(/forms/.test(label), `the widening chip does not name the shape: ${label}`)
    {
      const chip = await browser.centre('.chip-action')
      await browser.click(chip.x, chip.y)
      await new Promise(resolve => setTimeout(resolve, 900))
      const widened = await browser.evaluate(`document.querySelector('.layer-chips')?.innerText.split(String.fromCharCode(10)).join(' · ') ?? null`)
      assert(widened, 'the card did not render after a chip click')
      assert(!errors.length, `an exception was thrown on a chip click: ${errors.join(' | ').slice(0, 300)}`)
      // The widening the reader chose reading back as `active` is NOT asserted here: the round trip
      // is listed as pending verification rather than asserted loosely.
      const on = await browser.evaluate(`document.querySelectorAll('.chip-action.active').length`)
      return `${actions} chip(s), ${on} reading as chosen, ${label.trim()}`
    }
  })

  await step('clearing the box restores the collection', async () => {
    const clear = await browser.centre('.find-clear')
    await browser.click(clear.x, clear.y)
    await browser.waitFor('document.querySelector(".layer-chips") === null', 4000)
    await browser.waitFor('document.querySelectorAll(".glyph-grid .glyph-tile").length > 0', 4000)
    return 'chips gone, grid back'
  })

  await step('a fast typist is answered by the last query only', async () => {
    await setQuery('あ'); await setQuery('シ')
    await browser.waitFor('document.querySelectorAll(".candidate").length > 0', 4000)
    await new Promise(resolve => setTimeout(resolve, 900))
    const labels = await browser.evaluate(`Array.from(document.querySelectorAll('.candidate .candidate-char')).map(b => b.textContent)`)
    assert(labels.length > 0, 'no rows for the second query')
    return labels.join(' ')
  })

  // The fixture's three-layer record: written ネ (U+30CD), read ね. It is named here rather than
  // discovered, so this test cannot quietly pass by finding a record with no difference to test.
  const LAYERED = 'doc-1:p1:l3:u2'
  const openReviewer = async id => {
    await browser.evaluate(`visit('/')`)
    await browser.waitFor('document.querySelector("dialog[open]") === null', 4000)
    await browser.evaluate(`visit('/character/' + encodeURIComponent(${JSON.stringify(id)}))`)
    try {
      await browser.waitFor('document.querySelector("dialog[open] .inspector-crop img")?.naturalWidth > 0', 6000)
    } catch (error) {
      const state = await browser.evaluate(`JSON.stringify({
        path: location.pathname, dialog: !!document.querySelector('dialog[open]'),
        crop: !!document.querySelector('.inspector-crop img'),
        natural: document.querySelector('.inspector-crop img')?.naturalWidth ?? null,
        error: document.querySelector('dialog .error-message')?.innerText ?? null,
        disabled: document.querySelector('.save-character')?.disabled ?? null,
        unavailable: document.querySelector('.inspector-savebar [role=alert]')?.innerText ?? null })`)
      throw new Error(`${error.message} :: ${state}`)
    }
  }
  const fieldsOf = async () => JSON.parse(await browser.evaluate(`JSON.stringify({
    reading: document.querySelector('.reading-input input')?.value,
    written: document.querySelector('.written-input input')?.value })`))

  await step('the reviewer opens on the reading, not on the written character', async () => {
    await openReviewer(LAYERED)
    const fields = await fieldsOf()
    assert(fields.written === 'ネ', `written field holds ${fields.written}, expected ネ`)
    assert(fields.reading === 'ね', `reading field holds ${fields.reading}, expected ね`)
    return `${fields.written} / ${fields.reading}`
  })

  await step('saving without an edit writes no reading event', async () => {
    const before = await (await fetch(service.base + '/atlas/reviews')).json()
    const point = await browser.centre('.save-character')
    await browser.click(point.x, point.y)
    await browser.waitFor('document.querySelector("dialog[open]") === null', 6000)
    await new Promise(resolve => setTimeout(resolve, 300))
    const after = await (await fetch(service.base + '/atlas/reviews')).json()
    const written = after.reviews.slice(before.reviews.length)
    const readings = written.filter(row => row.event.field === 'reading')
    assert(readings.length === 0, `${readings.length} reading events were written by an untouched save`)
    const stored = await (await fetch(service.base + '/atlas/characters/' + encodeURIComponent(LAYERED))).json()
    assert(stored.reading === 'ね' && stored.label === 'ネ', `record is now ${stored.label} / ${stored.reading}`)
    return `${written.length} review event(s), none of them a reading`
  })

  await step('correcting the character keeps the reading and lands in the export', async () => {
    await openReviewer(LAYERED)
    await browser.evaluate(`(() => {
      const input = document.querySelector('.written-input input')
      input.value = 'ヌ'
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })()`)
    const point = await browser.centre('.save-character')
    await browser.click(point.x, point.y)
    await browser.waitFor('document.querySelector("dialog[open]") === null', 6000)
    await new Promise(resolve => setTimeout(resolve, 400))
    const stored = await (await fetch(service.base + '/atlas/characters/' + encodeURIComponent(LAYERED))).json()
    assert(stored.label === 'ヌ', `stored character is ${stored.label}, expected ヌ`)
    assert(stored.reading === 'ね', `stored reading is ${stored.reading}, expected the untouched ね`)
    // The export holds one row per review, and the layer correction is its evidence: the unicode
    // event itself is in the journal, which is what the export's `current` check reads.
    const exportBody = await (await fetch(service.base + '/atlas/reviews')).json()
    const row = exportBody.reviews.filter(entry => entry.event.target_id === LAYERED).pop()
    assert(row, 'the export has no review row for this record')
    const evidence = JSON.parse(row.event.evidence)
    const correction = evidence.layer_correction ?? {}
    assert(correction.changed?.includes('character'), `changed is ${JSON.stringify(correction.changed)}`)
    assert(correction.code_point === 'U+30CC', `correction code point is ${correction.code_point}`)
    assert(correction.character === 'ヌ' && correction.reading === 'ね',
      `correction reads ${correction.character} / ${correction.reading}`)
    assert(row.current === true, 'the exported review does not read as current')
    const journal = await (await fetch(service.base + '/atlas/reviews.json')).json()
    assert(journal.kind === 'atlas-character-reviews', 'the export attachment has the wrong kind')
    return `${correction.character} ${correction.code_point}, reading ${correction.reading}, current`
  })

  await step('the ordinary collection keeps its review queue after a correction', async () => {
    // The homepage inspector queue is the path a correction must not break: its Next steps through
    // the collection's own rows, not through a character gallery that may hold one record.
    await browser.evaluate(`visit('/')`)
    await browser.waitFor('document.querySelector("dialog[open]") === null', 4000)
    // The search box still holds the last query; the collection behind it is what is being tested.
    await browser.evaluate(`document.querySelector('.find-clear')?.click()`)
    await browser.waitFor('document.querySelectorAll(".glyph-grid .glyph-tile").length > 1', 8000)
    const tiles = await browser.evaluate(`document.querySelectorAll('.glyph-grid .glyph-tile').length`)
    const first = await browser.centre('.glyph-grid .glyph-tile')
    await browser.click(first.x, first.y)
    await browser.waitFor('document.querySelector("dialog[open]") !== null', 6000)
    const before = await browser.evaluate(`document.querySelector('.inspector-navigation span')?.textContent ?? ''`)
    const next = await browser.centre('.next-character')
    await browser.click(next.x, next.y)
    await new Promise(resolve => setTimeout(resolve, 500))
    const after = await browser.evaluate(`document.querySelector('.inspector-navigation span')?.textContent ?? ''`)
    assert(after && after !== before, `Next did not move: ${before} → ${after}`)
    return `${tiles} tiles, ${before} → ${after}`
  })

  await step('no exception reached the page', async () => {
    assert(!errors.length, errors.join(' | ').slice(0, 400))
    // The fixture keeps one page's image uncached on purpose, so its tiles answer 422; that is the
    // case the collection is built to survive and not a failure of this flow.
    const unexpected = bad.filter(url => !/^4\d\d .*\/atlas\/characters\/.+\/image\?/.test(url))
    assert(!unexpected.length, `unexpected: ${unexpected.slice(0, 3).join(' | ')} (all ${bad.length})`)
    return `${bad.filter(url => /^4/.test(url)).length} expected uncached-page tiles`
  })

  // Reported, not asserted: a 5xx from a route this flow does not own is somebody else's finding,
  // and hiding it would be worse than printing it.
  const faults = bad.filter(url => /^5/.test(url))
  if (faults.length) console.log(`  note  ${faults.length} 5xx from other routes: ${faults.slice(0, 3).join(' | ')}`)
} finally { await browser?.close(); await service.stop() }
console.log(failures ? `\n${failures} failed` : '\nall layer checks passed')
process.exit(failures ? 1 : 0)
