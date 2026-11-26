import { describe, expect, it, mock } from 'bun:test'

mock.module('../src/lib/client.js', () => ({ request: async () => ({}) }))
const { inheritedStyle, ownStyle, pressStyle, stylesOffered } = await import('../src/lib/cropStyle.js')

const crop = (style, basis = 'none', inherited = style) => ({ style, style_basis: basis, style_inherited: inherited, style_choices: ['cursive', 'formal', 'mixed', 'printed', 'regular', 'running', 'seal'] })

describe('a crop\'s style', () => {
  it('is offered from the vocabulary the server can store, without mixed and the old codes', () => {
    expect(stylesOffered(crop('unassessed'))).toEqual(['regular', 'running', 'cursive', 'seal'])
    expect(stylesOffered({ style: 'unassessed' })).toEqual(['regular', 'running', 'cursive', 'clerical', 'seal'])
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
