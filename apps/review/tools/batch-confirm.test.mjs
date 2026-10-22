// Selecting tiles in the collection view and confirming them all with "Looks right": the selection's
// toggles, ranges and arrow moves, and the confirmation's batches, bodies and per-crop report.
import { describe, expect, it } from 'bun:test'
import { arrowTarget, toggled, withRange } from '../src/lib/selection.js'
import { confirmationBatches, confirmationBody, confirmationOutcome, confirmationPlan, confirmedCharacter, reportByReason, sendBatches } from '../src/lib/corrections.js'
import { ROUND_MAX } from '../../cloudflare/src/rounds.ts'

const tile = (id, extra = {}) => ({ id, label: 'あ', revision: 3, image_sha256: 'a'.repeat(64), state: 'pending', ...extra })
const glyph = (id, extra = {}) => ({ id, origin: 'corpus', label: 'い', written_character: 'い', revision: 2, source_revision: 'b'.repeat(64), proxyable: true, image: '/i.png', state: 'pending', ...extra })

describe('selecting tiles', () => {
  it('toggles one tile in and out', () => {
    const once = toggled(new Set(), 'a')
    expect([...once]).toEqual(['a'])
    expect([...toggled(once, 'a')]).toEqual([])
  })
  it('takes a range in display order either way round, keeping what was selected and leaving out what cannot be', () => {
    const order = ['a', 'b', 'c', 'd', 'e'].map(id => ({ id }))
    expect([...withRange(new Set(['x']), order, 1, 3)]).toEqual(['x', 'b', 'c', 'd'])
    expect([...withRange(new Set(), order, 4, 2)]).toEqual(['c', 'd', 'e'])
    expect([...withRange(new Set(), order, 0, 4, item => item.id !== 'c')]).toEqual(['a', 'b', 'd', 'e'])
  })
})

describe('moving between tiles with the arrow keys', () => {
  // A grid of three columns of 100 × 100 tiles, seven tiles: the last row holds one.
  const grid = Array.from({ length: 7 }, (_, i) => ({ left: (i % 3) * 100, top: Math.floor(i / 3) * 100, width: 100, height: 100 }))
  it('steps through the display order left and right, stopping at its ends', () => {
    expect(arrowTarget(grid, 2, 'ArrowRight')).toBe(3)
    expect(arrowTarget(grid, 3, 'ArrowLeft')).toBe(2)
    expect(arrowTarget(grid, 0, 'ArrowLeft')).toBeNull()
    expect(arrowTarget(grid, 6, 'ArrowRight')).toBeNull()
  })
  it('goes to the tile above or below in the same column', () => {
    expect(arrowTarget(grid, 1, 'ArrowDown')).toBe(4)
    expect(arrowTarget(grid, 4, 'ArrowUp')).toBe(1)
    expect(arrowTarget(grid, 0, 'ArrowUp')).toBeNull()
  })
  it('goes to the nearest row first, then the nearest tile across it', () => {
    // From the middle row's last column the next row holds only the first column's tile.
    expect(arrowTarget(grid, 5, 'ArrowDown')).toBe(6)
  })
  it('reads a collage, whose rows are not aligned, by the tiles\' centres', () => {
    const collage = [{ left: 0, top: 0, width: 160, height: 120 }, { left: 160, top: 0, width: 80, height: 120 },
      { left: 0, top: 120, width: 90, height: 140 }, { left: 90, top: 120, width: 150, height: 140 }]
    expect(arrowTarget(collage, 1, 'ArrowDown')).toBe(3)
    expect(arrowTarget(collage, 0, 'ArrowDown')).toBe(2)
    expect(arrowTarget(collage, 3, 'ArrowUp')).toBe(1)
  })
})

describe('what "Looks right" confirms', () => {
  it('names the one written character of a crop, and none for an unassigned crop or a label of several', () => {
    expect(confirmedCharacter(tile('a'))).toBe('あ')
    expect(confirmedCharacter(tile('a', { written_character: 'ゑ' }))).toBe('ゑ')
    expect(confirmedCharacter(glyph('g'))).toBe('い')
    expect(confirmedCharacter(glyph('g', { written_character: null, identity_basis: 'normalized_transcription' }))).toBeNull()
    expect(confirmedCharacter(tile('a', { identity_status: 'unassigned' }))).toBeNull()
    expect(confirmedCharacter(tile('a', { label: 'あい' }))).toBeNull()
    // A base with its combining mark is one character.
    expect(confirmedCharacter(tile('a', { label: 'が' }))).toBe('が')
  })
  it('sends the crops left to confirm and reports the rest by reason', () => {
    const plan = confirmationPlan([tile('a'), tile('b', { state: 'checked' }), tile('c', { label: 'あい' }), glyph('d', { proxyable: false }), glyph('e'), tile('f', { state: 'flagged' })])
    expect(plan.crops.map(item => item.id)).toEqual(['a', 'e', 'f'])
    expect(plan.report).toEqual([{ id: 'b', reason: 'checked' }, { id: 'c', reason: 'unlabelled' }, { id: 'd', reason: 'unavailable' }])
  })
  it('cuts a large selection into batches of at most ROUND_MAX crops, each with its own submission id', () => {
    const crops = Array.from({ length: ROUND_MAX * 2 + 5 }, (_, i) => tile('c' + i))
    const batches = confirmationBatches(crops, ROUND_MAX)
    expect(batches.map(batch => batch.crops.length)).toEqual([ROUND_MAX, ROUND_MAX, 5])
    expect(new Set(batches.map(batch => batch.id)).size).toBe(3)
    expect(batches.flatMap(batch => batch.crops)).toEqual(crops)
  })
  it('keeps a batch\'s submission id while it is unanswered, and gives the same crops at a new revision a new one', () => {
    const crops = [tile('k1'), tile('k2')]
    const [first] = confirmationBatches(crops, ROUND_MAX)
    expect(confirmationBatches(crops, ROUND_MAX)[0].id).toBe(first.id)
    expect(confirmationBatches([tile('k1', { revision: 4 }), tile('k2')], ROUND_MAX)[0].id).not.toBe(first.id)
  })
  it('sends each crop as its own character at the revision and pixels it was shown at, as a line correction', () => {
    const [batch] = confirmationBatches([tile('a', { written_character: 'ゑ' }), glyph('g')], ROUND_MAX)
    expect(confirmationBody(batch)).toEqual({ id: batch.id, line: true, crops: [
      { id: 'a', revision: 3, character: 'ゑ', image_sha256: 'a'.repeat(64) },
      { id: 'g', revision: 2, character: 'い', source_revision: 'b'.repeat(64) }] })
  })
  it('sends the batches in turn; a refusal stops the run and names the batches answered before it', async () => {
    const crops = Array.from({ length: 300 }, (_, i) => tile('s' + i))
    const sent = []
    const refusal = Object.assign(new Error('Too many corrections at once.'), { status: 429 })
    const run = sendBatches(confirmationBatches(crops, ROUND_MAX), async batch => {
      sent.push(confirmationBody(batch).crops.length)
      if (sent.length === 2) throw refusal
      return { id: batch.id, results: [], unchanged: [], skipped: [] }
    })
    const error = await run.catch(e => e)
    expect(error).toBe(refusal)
    expect(sent).toEqual([ROUND_MAX, ROUND_MAX])
    expect(error.answered.map(({ item }) => item.crops.length)).toEqual([ROUND_MAX])
    expect(error.item.crops.length).toBe(ROUND_MAX)
  })
})

describe('what the site answered', () => {
  const batch = { id: 'b1', crops: [tile('a'), tile('b'), tile('c'), glyph('old:d'), tile('e'), tile('f')] }
  // `a` saved, `b` already confirmed, `c` changed since it was shown, the corpus glyph saved under its
  // public id, `e` saved but still reported for its box, `f` named nowhere.
  const answer = { id: 'b1', unchanged: ['b'], skipped: [{ id: 'c', reason: 'changed' }],
    results: [{ target_id: 'a', revision: 4, state: 'checked' }, { target_id: 'p:d', revision: 3, state: 'checked' }, { target_id: 'e', revision: 4, state: 'flagged' }] }
  it('names each saved crop\'s new revision and state, matching the answers in the order they were sent', () => {
    const { saved } = confirmationOutcome([{ item: batch, result: answer }])
    expect(Object.fromEntries(saved)).toEqual({ a: { revision: 4, state: 'checked' }, 'old:d': { revision: 3, state: 'checked' }, e: { revision: 4, state: 'flagged' } })
  })
  it('reports every other crop by reason, and none silently dropped', () => {
    const { report } = confirmationOutcome([{ item: batch, result: answer }])
    expect(report).toEqual([{ id: 'b', reason: 'checked' }, { id: 'c', reason: 'changed' }, { id: 'e', reason: 'flagged' }, { id: 'f', reason: 'changed' }])
  })
  it('gathers a report by reason, in a fixed order', () => {
    expect(reportByReason([{ id: 'x', reason: 'unlabelled' }, { id: 'y', reason: 'changed' }, { id: 'z', reason: 'changed' }, { id: 'w', reason: 'checked' }]))
      .toEqual([['changed', ['y', 'z']], ['checked', ['w']], ['unlabelled', ['x']]])
  })
})
