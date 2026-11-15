import { describe, expect, it } from 'bun:test'
import { place, sheet, wrap } from '../src/lib/collageImage.js'

const measure = (text, size) => text.length * size * 0.6

describe('the collage image', () => {
  it('places each crop at the top of the shortest column, in order', () => {
    const { blocks: [{ cells }], width } = place([{ aspects: [1, 2, 1, 1] }], 2, 100)
    expect(width).toBe(2 * 24 + 2 * 100 + 4)
    expect(cells).toEqual([
      { x: 24, y: 24, w: 100, h: 100 }, { x: 128, y: 24, w: 100, h: 200 },
      { x: 24, y: 128, w: 100, h: 100 }, { x: 128, y: 228, w: 100, h: 100 },
    ])
  })
  it('sets one block under another, each below its heading', () => {
    const { blocks: [first, second], type } = place([{ heading: 'a', aspects: [1, 1] }, { heading: 'b', aspects: [1] }], 2, 100)
    expect(first.heading).toBe(24 + type)
    expect(second.heading).toBeGreaterThan(first.cells[0].y + first.cells[0].h)
    expect(second.cells[0].y).toBeGreaterThan(second.heading)
  })
  it('breaks the credit between its parts', () => {
    expect(wrap(['aaaa', 'bbbb', 'cccc'], 12, text => text.length)).toEqual(['aaaa · bbbb', 'cccc'])
    expect(wrap(['a part longer than the line', 'b'], 5, text => text.length)).toEqual(['a part longer than the line', 'b'])
  })
  it('draws a few crops at their widest and many within what a canvas holds', () => {
    const few = sheet([{ aspects: Array(60).fill(1.2) }], 10, [['Glyph Atlas', '60 glyphs']], measure)
    expect(few.blocks[0].cells[0].w).toBe(160)
    expect(few.lines).toEqual(['Glyph Atlas · 60 glyphs'])
    expect(few.height).toBeGreaterThan(few.credit)
    const many = sheet([{ aspects: Array(6000).fill(1.2) }], 10, [], measure)
    expect(many.blocks[0].cells[0].w).toBeLessThan(160)
    expect(many.width * many.height).toBeLessThanOrEqual(16_000_000)
    expect(many.height).toBeLessThanOrEqual(16000)
  })
})
