import { describe, expect, it, jest, mock } from 'bun:test'

mock.module('../src/lib/i18n.svelte.js', () => ({ t: (key, values = {}) => `${key} ${values.count ?? ''}`.trim() }))
mock.module('../src/lib/busy.svelte.js', () => ({ isReload: () => false }))
const { afterBurst, countsMoved, latest, listDone, owed, savedTile, withSummary } = await import('../src/lib/galleryCounts.js')
const { matchesLabel } = await import('../src/lib/layers.js')

describe('a save', () => {
  const claim = grapheme => ({ grapheme_claim: { status: 'asserted', values: [{ grapheme }] } })
  const named = { id: 'b', label: 'は', written_character: null, identity_basis: 'normalized_transcription', identity_status: 'unassigned' }
  it('moves the totals when it changes a crop\'s character or its grapheme, and nothing when it confirms it', () => {
    const crop = { id: 'a', label: '仮', state: 'pending', grapheme_claim: null }
    expect(countsMoved(crop, { ...crop, state: 'checked' })).toBe(null)
    expect(countsMoved(crop, { ...crop, state: 'flagged', issue: 'crop' })).toBe(null)
    expect(countsMoved(crop, { ...crop, label: '假' })).toBe('totals')
    expect(countsMoved(crop, { ...crop, ...claim('U+53CD') })).toBe('totals')
    // A corpus glyph a reader names, and a crop no list holds.
    expect(countsMoved(named, savedTile(named, { written_character: 'は', identity_status: 'assigned' }))).toBe('totals')
    expect(countsMoved(undefined, crop)).toBe('totals')
  })
  it('that takes a claim back moves them, as the record it was saved from tells and a tile does not', () => {
    const record = { id: 'a', label: '后', ...claim('U+5F8C') }, tile = { id: 'a', label: '后' }
    expect(countsMoved(record, { ...record, grapheme_claim: { status: 'retracted', values: [] } })).toBe('totals')
    expect(countsMoved(record, { ...record, state: 'checked' })).toBe(null)
    // A tile states no claim, so confirming a claimed crop from one is no change.
    expect(countsMoved(tile, savedTile(tile, { ...record, state: 'checked' }))).toBe(null)
  })
  it('and its undo move them, the tile back as it was naming no character of its own', () => {
    const tile = { id: 'a', label: '仮', state: 'pending' }, corrected = { ...tile, label: '仮', written_character: '假', state: 'checked' }
    expect(countsMoved(tile, corrected)).toBe('totals')
    expect(countsMoved(corrected, { ...tile, revision: 3 })).toBe('totals')
  })
  it('of a style moves what a gallery is narrowed by, and no total', () => {
    const crop = { id: 'a', label: '仮', style: 'cursive' }
    expect(countsMoved(crop, { ...crop, style: 'regular' })).toBe('facets')
    expect(countsMoved({ ...crop, style: null }, { ...crop, style: 'mixed' })).toBe(null)
    expect(countsMoved(crop, { ...crop, style: 'running' })).toBe(null)
    expect(countsMoved(crop, { ...crop, style: 'regular', label: '假' })).toBe('totals')
  })
  it('leaves a list its last page: what ends it is the page that names no next', () => {
    // 61 glyphs, 60 of them read; one moved out of the list makes the count 60, and a page is still to come.
    expect([listDone(true, '0S0.1.a', 60), listDone(true, null, 60), listDone(false, null)]).toEqual([false, true, false])
    // A variants widening lists no more than its cap, whatever cursor the last page names.
    expect([listDone(true, '0S0.1.a', 1980, 2000), listDone(true, '0S0.1.a', 2000, 2000)]).toEqual([false, true])
  })
  it('stays due until a read of it is taken', () => {
    const totals = owed()
    expect(totals.begin().due).toBe(false)
    totals.mark()
    // A style's save begins another read before the first is in; the first is dropped, and the second owes all.
    const first = totals.begin(), second = totals.begin()
    expect([first.due, second.due]).toEqual([true, true])
    // It fails: the next read still owes it.
    const third = totals.begin()
    expect(third.due).toBe(true)
    // What moves while it is read is the next one's.
    totals.mark(); third.done()
    const fourth = totals.begin()
    expect(fourth.due).toBe(true)
    fourth.done(); first.done()
    expect(totals.begin().due).toBe(false)
  })
  it('has the counts of the last read begun, in whatever order reads come in', async () => {
    const begin = latest(), answers = []
    let shown = null
    const read = value => { const current = begin(); return new Promise(done => answers.push(done)).then(() => { if (current()) shown = value }) }
    const before = read('before the save'), after = read('after it')
    answers[1](); await after
    expect(shown).toBe('after it')
    answers[0](); await before
    expect(shown).toBe('after it')
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
