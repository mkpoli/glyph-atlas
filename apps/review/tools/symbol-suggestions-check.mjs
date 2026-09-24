import assert from 'node:assert/strict'
import Browser from './browser.mjs'

const base = process.env.ATLAS_URL || 'http://127.0.0.1:8770/'
let browser
try {
  browser = await Browser.launch({ width: 1280, height: 1000 })
  await browser.goto(base, { waitFor: "document.querySelector('.glyph-tile[data-unit]') !== null" })
  await browser.evaluate(`
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, options) => {
      const url = String(input);
      if (!url.includes('/suggestions')) return originalFetch(input, options);
      if (url.includes('/suggestions/context')) return Promise.resolve(new Response(JSON.stringify({
        status: 'ready', candidates: ['アカ', 'アキ', 'アク', 'アケ', 'アコ', 'アサ'].map(text => ({text, engine: 'Nearby transcription'}))
      })));
      return new Promise(resolve => window.releaseSymbol = () => resolve(new Response(JSON.stringify({
        status: 'ready', candidates: [{text: 'シ▲', basis: 'symbol-parts', engine: 'Separated symbol OCR'}]
      }))));
    };
    document.querySelector('.glyph-tile[data-unit]').click();
  `)
  await browser.waitFor("document.querySelector('.character-dialog[open] .issue-card') !== null")
  await browser.evaluate("[...document.querySelectorAll('.character-dialog .issue-card')].find(b => b.innerText.includes('Joined')).click()")
  await browser.waitFor("document.querySelectorAll('.suggestion-options button').length === 6")
  await browser.evaluate("document.querySelector('.suggestion-options button').focus(); document.querySelector('.suggestion-options button').click()")
  const before = await browser.evaluate("[...document.querySelectorAll('.suggestion-options button')].map(b => b.innerText)")
  await browser.waitFor("typeof window.releaseSymbol === 'function'")
  await browser.evaluate('window.releaseSymbol()')
  await browser.waitFor("[...document.querySelectorAll('.suggestion-options button')].some(b => b.innerText === 'シ▲')")
  const after = await browser.evaluate("[...document.querySelectorAll('.suggestion-options button')].map(b => b.innerText)")
  assert.deepEqual(after.slice(0, 6), before, 'Late marker moved existing choices')
  assert.equal(await browser.evaluate("document.querySelector('.suggestion-options button').getAttribute('aria-pressed')"), 'true')
  assert.equal(await browser.evaluate("document.activeElement === document.querySelector('.suggestion-options button')"), true)
  await browser.evaluate("[...document.querySelectorAll('.suggestion-options button')].find(b => b.innerText === 'シ▲').click()")
  assert.equal(await browser.evaluate("[...document.querySelectorAll('.suggestion-options button')].find(b => b.innerText === 'シ▲').getAttribute('aria-pressed')"), 'true')
  const colors = await browser.evaluate("[...document.querySelectorAll('.suggestion-options button.chosen .script-char')].map(e => getComputedStyle(e).color)")
  // The mixed sequence also keeps the character-type distinction in its rendering.
  assert.equal(colors.length, 2)
  assert.notEqual(colors[0], colors[1])
  assert.equal(await browser.evaluate("document.querySelector('.suggestion-options button.chosen').innerText"), 'シ▲')
  console.log('PASS: delayed ▲ remains visible and selectable after six context choices; order, focus and selection preserved.')
} finally { if (browser) await browser.close() }
