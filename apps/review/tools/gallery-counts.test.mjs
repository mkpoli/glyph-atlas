import { describe, expect, it, jest, mock } from 'bun:test'

mock.module('../src/lib/i18n.svelte.js', () => ({ t: (key, values = {}) => `${key} ${values.count ?? ''}`.trim() }))
mock.module('../src/lib/busy.svelte.js', () => ({ isReload: () => false }))
const { afterBurst, countsMoved, withSummary } = await import('../src/lib/galleryCounts.js')
const { matchesLabel } = await import('../src/lib/layers.js')

describe('a save', () => {
  const claim = grapheme => ({ grapheme_claim: { status: 'asserted', values: [{ grapheme }] } })
  it('moves the counts when it changes a crop\'s character or its grapheme, and not when it confirms it', () => {
    const crop = { id: 'a', label: '仮', state: 'pending' }
    expect(countsMoved(crop, { ...crop, state: 'checked' })).toBe(false)
    expect(countsMoved(crop, { ...crop, state: 'flagged', issue: 'crop' })).toBe(false)
    expect(countsMoved(crop, { ...crop, label: '假' })).toBe(true)
    expect(countsMoved(crop, { ...crop, ...claim('U+53CD') })).toBe(true)
    expect(countsMoved({ ...crop, ...claim('U+53CD') }, { ...crop, grapheme_claim: { status: 'retracted', values: [] } })).toBe(true)
    expect(countsMoved({ ...crop, ...claim('U+53CD') }, { ...crop, ...claim('U+53CD') })).toBe(false)
    // A corpus glyph a reader names, and a crop no list holds.
    expect(countsMoved({ id: 'b', label: 'は', written_character: null, identity_basis: 'normalized_transcription' }, { id: 'b', label: 'は', written_character: 'は', identity_status: 'assigned' })).toBe(true)
    expect(countsMoved(undefined, crop)).toBe(true)
  })
  it('leaves an open gallery its own total, and gives a listing the catalogue\'s', () => {
    const data = { query: '仮', total: 65, available: 65, categories: [1], other: true }
    const summary = { counts: { checked: 3 }, categories: [2], documents: [], total: 59, available: 800000, reported_count: 1, withheld_count: 2 }
    expect(withSummary(data, summary, { code_point: 'U+4EEE' })).toMatchObject({ total: 65, available: 65, categories: [2], counts: { checked: 3 }, other: true })
    expect(withSummary(data, summary, null)).toMatchObject({ total: 59, available: 800000 })
  })
  it('in a burst has the counts read once, after the last', () => {
    jest.useFakeTimers()
    try {
      const run = mock(() => {}), again = afterBurst(run, 800)
      again(); jest.advanceTimersByTime(500); again(); jest.advanceTimersByTime(799)
      expect(run).toHaveBeenCalledTimes(0)
      jest.advanceTimersByTime(1)
      expect(run).toHaveBeenCalledTimes(1)
      again(); again.cancel(); jest.advanceTimersByTime(2000)
      expect(run).toHaveBeenCalledTimes(1)
    } finally { jest.useRealTimers() }
  })
})

describe('the evidence beside a character\'s count', () => {
  it('is its lines and pages, and no second count of its crops', () => {
    expect(matchesLabel({ known: true, glyphs: 21, imported: 4, lines: 523, pages: 2268 })).toBe('layers.lineMatches.count 523 · layers.pageMatches.count 2268')
    expect(matchesLabel({ known: true, glyphs: 21, imported: 4, lines: 0, pages: 0 })).toBe('')
    expect(matchesLabel(null)).toBe('')
  })
})
