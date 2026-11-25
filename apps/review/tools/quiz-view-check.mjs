#!/usr/bin/env bun
/**
 * Quick Review view modes, tile-size slider, and form sections, in a real browser,
 * against a disposable dataset.
 *
 * The disposable fixture's U+3042 round is augmented in-flight with three written forms
 * (あ, ア, 𛀂 with 字母 安) over real fixture crops so section headers, uniform auto-fill
 * columns, section select-all (false / mixed / true), visual-order Shift-click, compact
 * thumbnails, tile-size slider resizing, persistence across reloads, and mobile layouts
 * are all verified end-to-end.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/quiz-view-check.mjs
 */
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'

const config = options()
const argv = process.argv.slice(2)
const shotsArg = argv.includes('--shots') ? argv[argv.indexOf('--shots') + 1] : null
const shotsDir = shotsArg || '/tmp/atlas-quiz-view-shots'
mkdirSync(shotsDir, { recursive: true })

const service = await boot(config)
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }

try {
  browser = await Browser.launch({ width: 1440, height: 1000 })
  await browser.writeAs('quiz-view-check')
  const errors = []
  browser.listeners.push(m => {
    if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text)
  })

  // Intercept the U+3042 review deal to assign three realistic forms (あ, ア, 𛀂) across the fixture crops.
  await browser.send('Fetch.enable', { patterns: [{ urlPattern: '*/atlas?*', requestStage: 'Request' }] })
  browser.listeners.push(async m => {
    if (m.method !== 'Fetch.requestPaused') return
    const { requestId, request } = m.params
    const url = new URL(request.url)
    if (url.searchParams.get('purpose') === 'review' && url.searchParams.get('grapheme') === 'U+3042' && url.searchParams.get('state') === 'pending') {
      try {
        const res = await fetch(request.url)
        const payload = await res.json()
        const forms = [
          { label: 'あ', jibo: null },
          { label: 'ア', jibo: null },
          { label: '𛀂', jibo: '安' },
        ]
        // Interleave forms in dealt order so section grouping has to partition them cleanly.
        const items = (payload.items ?? []).map((item, i) => {
          const picked = forms[i % 5 === 4 ? 2 : i % 3 === 1 ? 1 : 0]
          return { ...item, label: picked.label, written_character: picked.label, jibo: picked.jibo }
        })
        const patched = { ...payload, grapheme: { ...(payload.grapheme ?? {}), key: 'U+3042', members: ['あ', 'ア', '𛀂'] }, items }
        await browser.send('Fetch.fulfillRequest', {
          requestId,
          responseCode: 200,
          responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
          body: Buffer.from(JSON.stringify(patched)).toString('base64'),
        })
        return
      } catch { /* fall through */ }
    }
    await browser.send('Fetch.continueRequest', { requestId }).catch(() => {})
  })

  const setViewport2x = async (width, height) => {
    await browser.send('Emulation.setDeviceMetricsOverride', {
      width,
      height,
      deviceScaleFactor: 2,
      mobile: width < 500,
    })
  }
  const click = async selector => {
    await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({ block: 'center' })`)
    await browser.waitFor(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); return el !== null && !el.disabled })()`)
    const p = await browser.centre(selector)
    await browser.click(p.x, p.y)
  }
  const setSlider = async index => {
    await browser.evaluate(`(() => {
      const input = document.querySelector('.quiz-zoom input[type="range"]')
      input.value = ${JSON.stringify(String(index))}
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })()`)
    await Bun.sleep(100)
  }

  await setViewport2x(1440, 1000)
  await browser.goto(`${service.base}/en/review/U+3042`, {
    waitFor: 'document.querySelectorAll(".quiz-section").length === 3',
  })
  await browser.waitFor('[...document.querySelectorAll(".quiz-tile img")].every(i => i.complete && i.naturalWidth > 0)', 20000)

  // 1. Default Cards mode + form sections (あ, ア, 𛀂 with 字母 安).
  const sectionForms = await browser.evaluate(`[...document.querySelectorAll('.quiz-section')].map(s => [s.dataset.form, s.querySelector('.quiz-section-jibo')?.innerText ?? null, s.querySelectorAll('.quiz-tile').length])`)
  assert(sectionForms.length === 3, `expected 3 form sections, got ${JSON.stringify(sectionForms)}`)
  assert(sectionForms.map(s => s[0]).join(',') === 'あ,ア,𛀂', `unexpected section order: ${JSON.stringify(sectionForms)}`)
  assert(sectionForms[2][1] === '安', `expected 字母 安 on 𛀂 section, got ${JSON.stringify(sectionForms[2])}`)

  // Uniform column width across sections (auto-fill, never stretched in smaller sections).
  const sectionWidths = await browser.evaluate(`[...document.querySelectorAll('.quiz-section')].map(s => Math.round(s.querySelector('.quiz-tile').getBoundingClientRect().width))`)
  assert(Math.max(...sectionWidths) - Math.min(...sectionWidths) <= 2, `uneven tile widths across sections: ${JSON.stringify(sectionWidths)}`)
  console.log(`ok   3 form sections rendered (${sectionForms.map(s => `${s[0]}:${s[2]}`).join(', ')}) with uniform tile width ${sectionWidths[0]}px`)

  // Light & dark screenshots in Cards mode with sections.
  await browser.evaluate('scrollTo(0, 0)')
  await browser.screenshot(join(shotsDir, 'cards-sections-light.png'))
  await browser.setColorScheme('dark')
  await Bun.sleep(100)
  await browser.screenshot(join(shotsDir, 'cards-sections-dark.png'))
  await browser.setColorScheme('light')

  // 2. Section checkbox: false -> mixed (after clicking 1 tile) -> true (selects whole section) -> false.
  assert(await browser.evaluate(`document.querySelector('.quiz-section[data-form="あ"] .section-select').getAttribute('aria-checked')`) === 'false', 'section checkbox starts unchecked')
  await click('.quiz-section[data-form="あ"] .quiz-tile .quiz-choice')
  assert(await browser.evaluate(`document.querySelector('.quiz-section[data-form="あ"] .section-select').getAttribute('aria-checked')`) === 'mixed', 'selecting 1 tile sets section checkbox to mixed')
  await click('.quiz-section[data-form="あ"] .section-select')
  assert(await browser.evaluate(`document.querySelector('.quiz-section[data-form="あ"] .section-select').getAttribute('aria-checked')`) === 'true', 'clicking mixed checkbox selects all in section')
  const firstSectionCount = sectionForms[0][2]
  assert(await browser.evaluate(`document.querySelectorAll('.quiz-section[data-form="あ"] .quiz-tile.selected').length`) === firstSectionCount, 'all tiles in first section selected')
  assert(await browser.evaluate(`document.querySelectorAll('.quiz-section[data-form="ア"] .quiz-tile.selected').length`) === 0, 'other section untouched')
  await click('.quiz-section[data-form="あ"] .section-select')
  assert(await browser.evaluate(`document.querySelectorAll('.quiz-tile.selected').length`) === 0, 'clicking checked checkbox clears section')
  console.log('ok   section select-all checkbox supports false, mixed, true')

  // 3. Visual-order Shift-click across section boundary selects contiguous visual tiles.
  const domIds = await browser.evaluate(`[...document.querySelectorAll('.quiz-tile')].map(t => t.dataset.unit)`)
  const crossEnd = Math.min(firstSectionCount + 1, domIds.length - 1)
  await browser.evaluate(`document.querySelectorAll('.quiz-choice')[${firstSectionCount - 1}].click()`)
  await browser.evaluate(`document.querySelectorAll('.quiz-choice')[${crossEnd}].dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true }))`)
  const picked = await browser.evaluate(`[...document.querySelectorAll('.quiz-tile.selected')].map(t => t.dataset.unit)`)
  assert(JSON.stringify(picked) === JSON.stringify(domIds.slice(firstSectionCount - 1, crossEnd + 1)), 'Shift-click follows visual section order')
  await browser.key('Escape')
  assert(await browser.evaluate(`document.querySelectorAll('.quiz-tile.selected').length`) === 0, 'Escape clears selection')
  console.log('ok   Shift-click selects in visual section order')

  // 4. Switch to Compact mode: square dense tiles, remembered across reload.
  await click('.quiz-mode-toggle button:nth-child(2)')
  await browser.waitFor('document.querySelector(".quiz-grid.compact") !== null')
  const compactBox = await browser.evaluate(`(() => { const r = document.querySelector('.quiz-tile').getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height) } })()`)
  assert(compactBox.w <= 96 && Math.abs(compactBox.w - compactBox.h) <= 2, `compact tile not small square: ${JSON.stringify(compactBox)}`)
  // Select two tiles so selection state is visible in screenshots too.
  await click('.quiz-section[data-form="あ"] .quiz-tile .quiz-choice')
  await click('.quiz-section[data-form="ア"] .quiz-tile .quiz-choice')
  assert(await browser.evaluate(`document.querySelectorAll('.quiz-tile.selected').length`) === 2, 'compact tiles toggle selection cleanly')
  await browser.evaluate('scrollTo(0, 0)')
  await browser.screenshot(join(shotsDir, 'compact-sections-light.png'))
  await browser.setColorScheme('dark')
  await Bun.sleep(100)
  await browser.screenshot(join(shotsDir, 'compact-sections-dark.png'))
  await browser.setColorScheme('light')
  await browser.key('Escape')
  console.log(`ok   compact mode renders ${compactBox.w}x${compactBox.h} square tiles`)

  // 5. Tile size slider resizes tiles; tiny 48px stop works; dragging down from Cards enters Compact.
  await setSlider(0) // 48px
  const tinyBox = await browser.evaluate(`(() => { const r = document.querySelector('.quiz-tile').getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height) } })()`)
  assert(tinyBox.w < compactBox.w && tinyBox.w <= 64, `48px slider stop did not shrink tiles: ${JSON.stringify(tinyBox)}`)
  await browser.screenshot(join(shotsDir, 'compact-tiny-light.png'))

  // Switch back to Cards -> returns to cards mode (>= 124px); dragging slider to 76px (index 2) re-enters Compact.
  await click('.quiz-mode-toggle button:nth-child(1)')
  await browser.waitFor('document.querySelector(".quiz-grid:not(.compact)") !== null')
  await setSlider(2) // 76px
  await browser.waitFor('document.querySelector(".quiz-grid.compact") !== null')
  console.log(`ok   tile size slider resizes down to ${tinyBox.w}px and switches to compact below card width`)

  // 6. Toggle By form OFF -> flat grid; reload preserves compact + size + ungrouped; toggle back ON.
  await click('.form-group-toggle')
  await browser.waitFor('document.querySelectorAll(".quiz-section").length === 0')
  assert(await browser.evaluate(`document.querySelectorAll('.quiz-tile').length`) === domIds.length, 'ungrouped grid keeps every tile')
  const reloaded = browser.once('Page.loadEventFired', 30000)
  await browser.send('Page.reload')
  await reloaded
  await browser.waitFor('document.documentElement.dataset.hydrated !== undefined && document.querySelectorAll(".quiz-tile").length > 0', 30000)
  assert(await browser.evaluate(`document.querySelector('.quiz-grid')?.classList.contains('compact')`), 'reload keeps compact mode')
  assert(await browser.evaluate(`document.querySelector('.quiz-zoom input[type="range"]')?.value`) === '2', 'reload keeps tile size')
  assert(await browser.evaluate(`document.querySelector('.form-group-toggle')?.getAttribute('aria-pressed')`) === 'false', 'reload keeps By form preference')
  await click('.form-group-toggle')
  await browser.waitFor('document.querySelectorAll(".quiz-section").length === 3')
  console.log('ok   mode, tile size, and By form persist across reload')

  // 7. Mobile (390px and 320px): zero horizontal overflow in both Compact and Cards modes.
  for (const width of [390, 320]) {
    await setViewport2x(width, 844)
    await Bun.sleep(100)
    assert(await browser.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), `compact mobile overflow at ${width}px`)
    if (width === 390) {
      await browser.evaluate('scrollTo(0, 0)')
      await browser.screenshot(join(shotsDir, 'compact-mobile-light.png'))
      await browser.setColorScheme('dark')
      await Bun.sleep(100)
      await browser.screenshot(join(shotsDir, 'compact-mobile-dark.png'))
      await browser.setColorScheme('light')
    }
    await click('.quiz-mode-toggle button:nth-child(1)')
    await browser.waitFor('document.querySelector(".quiz-grid:not(.compact)") !== null')
    assert(await browser.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), `cards mobile overflow at ${width}px`)
    await click('.quiz-mode-toggle button:nth-child(2)')
    await browser.waitFor('document.querySelector(".quiz-grid.compact") !== null')
  }
  console.log('ok   zero horizontal overflow at 390px and 320px in compact and cards modes')

  assert(errors.length === 0, 'browser exceptions: ' + errors.join('; '))
  console.log('\nquiz-view-check passed')
} catch (error) {
  if (browser) await browser.screenshot(join(shotsDir, 'failure.png')).catch(() => {})
  console.log('Service log:', service.log.slice(-6).join('').slice(-4000))
  throw error
} finally {
  await browser?.close()
  await service.stop()
}
