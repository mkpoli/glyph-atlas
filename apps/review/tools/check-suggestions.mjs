import Browser from './browser.mjs'

const base = process.env.ATLAS_URL || 'http://127.0.0.1:4173/'
let browser
const assert = (condition, message) => { if (!condition) throw new Error(message) }
async function open(mode, index = 0) {
  await browser.goto(base, {waitFor: "document.querySelectorAll('.glyph-tile[data-unit]').length > 2"})
  await browser.evaluate(`
    window.suggestionMode = ${JSON.stringify(mode)}; window.delayedSuggestions = [];
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, options) => {
      const url = String(input);
      if (!url.includes('/suggestions')) return originalFetch(input, options);
      const context = url.includes('/suggestions/context');
      const value = {status: 'ready', candidates: [{text: window.suggestionMode === 'hold-both' ? '古い' : context ? 'シヨロ' : 'シヨ口',
        engine: context ? 'Nearby transcription' : 'NDLkotenOCR', score: .9, before: 'ベツヲ', after: '▲ベツ'}]};
      if (window.suggestionMode === 'hold-both' || (context && window.suggestionMode === 'visual-first') ||
          (!context && window.suggestionMode === 'context-first')) {
        return new Promise(resolve => window.delayedSuggestions.push(() => resolve(new Response(JSON.stringify(value)))));
      }
      return Promise.resolve(new Response(JSON.stringify(value)));
    };
    document.querySelectorAll('.glyph-tile[data-unit]')[${index}].click();
  `)
  await browser.waitFor("document.querySelector('.character-dialog[open] .issue-card') !== null")
  await browser.evaluate("[...document.querySelectorAll('.character-dialog .issue-card')].find(b => b.innerText.includes('Joined')).click()")
}
const buttons = label => `document.querySelector('[aria-label="${label} suggestions"]')?.querySelectorAll('.suggestion-options button').length > 0`
try {
  browser = await Browser.launch({width: 1280, height: 1000})
  await open('context-first')
  await browser.waitFor(buttons('Context'))
  assert(await browser.evaluate(`document.querySelector('[aria-label="Visual suggestions"]').innerText.includes('Reading the crop')`), 'Visual wait should not hide context')
  await browser.evaluate(`document.querySelector('[aria-label="Context suggestions"] button').click()`)
  const focused = await browser.evaluate('document.activeElement?.className')
  await browser.evaluate('window.delayedSuggestions.forEach(release => release())')
  await browser.waitFor(buttons('Visual'))
  assert(await browser.evaluate(`document.querySelector('[aria-label="Context suggestions"] button').getAttribute('aria-pressed') === 'true'`), 'Late visual answer changed context selection')
  assert(await browser.evaluate('document.activeElement?.className') === focused, 'Late arrival stole focus')
  console.log('PASS: context first, selection and focus survive late visual result')

  await open('visual-first')
  await browser.waitFor(buttons('Visual'))
  assert(await browser.evaluate(`document.querySelector('[aria-label="Context suggestions"]').innerText.includes('Checking context')`), 'Context wait should not hide visual')
  await browser.evaluate('window.delayedSuggestions.forEach(release => release())')
  await browser.waitFor(buttons('Context'))
  console.log('PASS: visual first, both sources stay visible')

  await open('hold-both')
  await browser.waitFor('window.delayedSuggestions.length === 2')
  await browser.key('Escape')
  await browser.waitFor("document.querySelector('.character-dialog') === null")
  await browser.evaluate(`window.suggestionMode = 'immediate'; document.querySelectorAll('.glyph-tile[data-unit]')[1].click()`)
  await browser.waitFor("document.querySelector('.character-dialog[open] .issue-card') !== null")
  await browser.evaluate("[...document.querySelectorAll('.character-dialog .issue-card')].find(b => b.innerText.includes('Joined')).click()")
  await browser.waitFor(buttons('Context'))
  // Resolve the old crop after the replacement is ready. Its component has been destroyed.
  const before = await browser.evaluate("document.querySelector('.reading-suggestions').innerText")
  await browser.evaluate('window.delayedSuggestions.forEach(release => release())')
  await browser.evaluate('new Promise(resolve => setTimeout(resolve, 100))')
  assert(await browser.evaluate("document.querySelector('.reading-suggestions').innerText") === before, 'Old crop response replaced current suggestions')
  console.log('PASS: late replies from a closed character are discarded')

  await browser.key('Escape')
  await browser.evaluate(`window.suggestionMode = 'context-first'; window.delayedSuggestions = []; visit('/en/review')`)
  await browser.waitFor("document.querySelector('.quiz-choice:not(:disabled)') !== null")
  await browser.evaluate("document.querySelector('.quiz-choice:not(:disabled)').click()")
  await browser.evaluate("[...document.querySelectorAll('.round-error-tools .issue-card')].find(b => b.innerText.includes('Joined')).click()")
  await browser.waitFor(buttons('Context'))
  assert(await browser.evaluate(`document.querySelector('[aria-label="Visual suggestions"]').innerText.includes('Reading the crop')`), 'Quick review context was blocked by visual OCR')
  await browser.evaluate(`document.querySelector('[aria-label="Context suggestions"] button').click(); window.delayedSuggestions.forEach(release => release())`)
  await browser.waitFor(buttons('Visual'))
  assert(await browser.evaluate(`document.querySelector('[aria-label="Context suggestions"] button').getAttribute('aria-pressed') === 'true'`), 'Quick review selection changed')
  console.log('PASS: quick review sources settle independently without saving a review')
} finally { if (browser) await browser.close() }
