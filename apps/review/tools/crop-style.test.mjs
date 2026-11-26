import { describe, expect, it, mock } from 'bun:test'

mock.module('../src/lib/client.js', () => ({ request: async () => ({}) }))
const { inheritedStyle, ownStyle, pressStyle, stylesOffered } = await import('../src/lib/cropStyle.js')

const crop = (style, basis = 'none', inherited = style) => ({ style, style_basis: basis, style_inherited: inherited, style_choices: ['cursive', 'formal', 'mixed', 'printed', 'regular', 'running', 'seal'] })

describe('a crop\'s style', () => {
  it('is offered from the vocabulary the server can store, without mixed and the old codes', () => {
    expect(stylesOffered(crop('unassessed'))).toEqual(['regular', 'running', 'cursive', 'seal'])
    expect(stylesOffered({ style: 'unassessed' })).toHaveLength(7)
    // A style a reader gave it stays offered, so it can be taken back.
    expect(stylesOffered(crop('mixed', 'unit', 'cursive'))).toEqual(['regular', 'running', 'cursive', 'seal', 'mixed'])
  })

  it('is its own or inherited', () => {
    expect([ownStyle(crop('cursive')), inheritedStyle(crop('cursive'))]).toEqual([null, 'cursive'])
    expect([ownStyle(crop('regular', 'unit', 'cursive')), inheritedStyle(crop('regular', 'unit', 'cursive'))]).toEqual(['regular', 'cursive'])
    expect(inheritedStyle(crop('unassessed'))).toBe(null)
  })

  it('follows the buttons pressed', () => {
    const inherits = crop('cursive'), own = crop('regular', 'unit', 'cursive')
    // The inherited one pressed confirms it as the crop's own.
    expect(pressStyle(inherits, null, 'cursive')).toBe('cursive')
    expect(pressStyle(inherits, 'cursive', 'cursive')).toBe(null)
    expect(pressStyle(inherits, 'cursive', 'running')).toBe('running')
    // The crop's own pressed takes it back, and pressed again keeps it.
    expect(pressStyle(own, null, 'regular')).toBe('unassessed')
    expect(pressStyle(own, 'unassessed', 'regular')).toBe(null)
    expect(pressStyle(own, 'running', 'running')).toBe(null)
    expect(pressStyle(own, 'running', 'regular')).toBe(null)
  })
})

describe('a style given to many crops', async () => {
  const sent = []
  mock.module('../src/lib/client.js', () => ({ request: async (path, body) => {
    sent.push({ path, body })
    const crops = body.crops.filter(c => !c.id.startsWith('x')).map(c => ({ id: c.id, revision: (c.revision ?? 0) + 1, was: c.id === 'c1' && body.style !== 'seal' ? 'seal' : null,
      style: body.style === 'unassessed' ? 'cursive' : body.style, style_basis: body.style === 'unassessed' ? 'none' : 'unit', style_inherited: 'cursive' }))
    return { crops, refused: body.crops.filter(c => c.id.startsWith('x')).map(c => ({ id: c.id, reason: 'changed' })) }
  } }))
  const { styleCrops, unstyleCrops, STYLE_BATCH } = await import('../src/lib/cropStyle.js?many')
  it('goes in batches, and reports the crops refused', async () => {
    const crops = Array.from({ length: STYLE_BATCH + 2 }, (_, i) => ({ id: (i === 3 ? 'x' : 'c') + i, revision: 4, style: 'cursive', style_basis: 'none' }))
    const { styled, refused } = await styleCrops(crops, 'regular')
    expect(sent.map(s => s.body.crops.length)).toEqual([STYLE_BATCH, 2])
    expect([styled.length, refused]).toEqual([STYLE_BATCH + 1, [{ id: 'x3', reason: 'changed' }]])
    expect(styled[0].after).toMatchObject({ id: 'c0', revision: 5, style: 'regular', style_basis: 'unit' })
  })
  it('sends a batch made again after its answer as a new write', async () => {
    sent.length = 0
    const crops = [{ id: 'c7' }]
    await styleCrops(crops, 'running'); await styleCrops(crops, 'running')
    expect(new Set(sent.map(s => s.body.id)).size).toBe(2)
  })
  it('is taken back to each crop\'s own style, or to the one it inherits', async () => {
    sent.length = 0
    // A listing names no basis: the style each crop had of its own comes from the save's answer.
    const own = { id: 'c1', revision: 2, style: 'seal' }, inherits = { id: 'c2', revision: 2, style: 'cursive' }
    const { styled } = await styleCrops([own, inherits], 'regular')
    sent.length = 0
    const back = await unstyleCrops(styled)
    expect(sent.map(s => [s.body.style, s.body.crops])).toEqual([['seal', [{ id: 'c1', revision: 3 }]], ['unassessed', [{ id: 'c2', revision: 3 }]]])
    expect(back.styled.map(({ after }) => [after.id, after.revision])).toEqual([['c1', 4], ['c2', 4]])
  })
})
