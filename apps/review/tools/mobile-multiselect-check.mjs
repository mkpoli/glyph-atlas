#!/usr/bin/env bun
/**
 * Multi-select review on phone viewports: selecting many crops and entering the focus review
 * step, or opening the character search menu inside a bulk/form bar, never widens the page.
 *
 * Run through devrun:
 *   devrun bun apps/review/tools/mobile-multiselect-check.mjs
 */
import Browser from './browser.mjs'
import { boot, options } from './harness.mjs'

const config = options()
const service = await boot(config)
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }

try {
  browser = await Browser.launch({ width: 390, height: 844 })
  await browser.writeAs('mobile-multiselect', { anonymous: false })

  // Selecting ten crops in a round and opening the focus review step keeps the page within the
  // viewport while the thumbnail strip scrolls horizontally.
  await browser.goto(`${service.base}/zh-Hans/review/U+304B`, {
    waitFor: `document.querySelectorAll('.quiz-choice').length >= 10`,
  })
  await browser.evaluate(`for (const b of [...document.querySelectorAll('.quiz-choice')].slice(0, 10)) b.click()`)
  await browser.waitFor(`document.querySelector('.review-selected') && !document.querySelector('.review-selected').disabled`)
  for (const width of [390, 320]) {
    await browser.setViewport(width, 844)
    await Bun.sleep(150)
    const step1 = await browser.evaluate(`({ vw: innerWidth, sw: document.documentElement.scrollWidth })`)
    assert(step1.vw <= width && step1.sw <= width, `form bar widens mobile at ${width}px: ${JSON.stringify(step1)}`)
  }

  await browser.setViewport(390, 844)
  await browser.evaluate(`document.querySelector('.review-selected').click()`)
  await browser.waitFor(`document.querySelectorAll('.focus-thumb').length === 10`)
  for (const width of [390, 320]) {
    await browser.setViewport(width, 844)
    await Bun.sleep(150)
    const fit = await browser.evaluate(`(() => {
      const nav = document.querySelector('.focus-nav').getBoundingClientRect()
      const strip = document.querySelector('.focus-strip')
      return { vw: innerWidth, sw: document.documentElement.scrollWidth, navRight: nav.right, scrollable: strip.scrollWidth > strip.clientWidth }
    })()`)
    assert(fit.vw <= width && fit.sw <= width && fit.navRight <= width + 1 && fit.scrollable,
      `multi-select focus review widens mobile at ${width}px: ${JSON.stringify(fit)}`)
  }

  // Bulk selection bar and its open character search list stay within mobile viewports.
  await browser.setViewport(390, 844)
  await browser.goto(`${service.base}/zh-Hans/`, { waitFor: `document.querySelectorAll('.glyph-tile').length > 0` })
  await browser.waitFor(`document.querySelector('.select-toggle') !== null`)
  await browser.evaluate(`document.querySelector('.select-toggle').click()`)
  await browser.waitFor(`document.querySelector('.bulk-bar .character-search input') !== null`)
  await browser.evaluate(`(() => {
    const field = document.querySelector('.bulk-bar .character-search input')
    field.focus()
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
    setter.call(field, 'い')
    field.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: 'い' }))
  })()`)
  await browser.waitFor(`document.querySelector('.bulk-bar .candidate-list') !== null`)
  for (const width of [390, 320]) {
    await browser.setViewport(width, 844)
    await Bun.sleep(150)
    const box = await browser.evaluate(`(() => {
      const list = document.querySelector('.bulk-bar .candidate-list').getBoundingClientRect()
      return { vw: innerWidth, sw: document.documentElement.scrollWidth, left: list.left, right: list.right }
    })()`)
    assert(box.vw <= width && box.sw <= width && box.left >= -1 && box.right <= width + 1,
      `bulk bar search list widens mobile at ${width}px: ${JSON.stringify(box)}`)
  }

  console.log('mobile-multiselect-check passed')
} finally {
  await browser?.close()
  await service.stop?.()
}
